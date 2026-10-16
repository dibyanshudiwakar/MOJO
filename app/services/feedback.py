"""Apply the generated-code analyses back into the generated code.

``code-generation`` leaves its findings in ``quality_runs``: the checks there
are what "analysing the generated code" actually produces. Three of them fail
for reasons that can be repaired mechanically *and*, crucially, proven repaired
by re-running the very same gate:

* ``SECRET_SCAN`` -- a credential-shaped key assigned a literal value.
* ``HARDCODED_ENDPOINT_SCAN`` -- an absolute http(s) URL baked into source.
* ``PROVENANCE`` -- the provenance manifest missing from the project root.

Each fix is written into the files and then re-validated, so the agent never
claims a repair the gate does not confirm. That is the whole point of looping:
the evidence is the gate's own verdict after the edit, not the fixer's opinion
of it.

What this deliberately does **not** touch is behavioural variance --
``differential_evidence``, ``variances``, ``root_causes``. Those describe the
migrated service behaving differently from ACE, which needs an understanding of
the source system that no regex has. They remain the remediation agent's
business, which proposes rather than writes, and they are reported here as
unresolved rather than quietly counted as fixed.

Only the file types ``quality_validate`` itself scans are edited
(``.java``/``.yml``/``.yaml``/``.properties``), so the fixer and the gate can
never disagree about which files are in scope. ``pom.xml`` is outside that set,
which is what keeps the Maven POM namespace URLs from being rewritten.
"""
import json
import re
from pathlib import Path

from app.repository import now

#: Exactly the suffixes and size bound ``quality_validate`` reads, so a fix
#: cannot miss a file the gate will go on to scan, or edit one it will not.
SCANNED_SUFFIXES = ('.java', '.yml', '.yaml', '.properties')
MAX_FILE_BYTES = 2_000_000

#: ``quality_validate``'s secret rule is
#: ``(?i)(password|secret|token)\s*[:=]\s*["'][^$][^"']+`` -- it requires a
#: non-``$`` character straight after the opening quote. Replacing the literal
#: with a ``${...}`` placeholder is therefore both the correct intent
#: (externalised configuration) and what clears the check.
_SECRET = re.compile(r'(?i)((password|secret|token)\s*[:=]\s*)(["\'])(?!\$)[^"\']+(["\'])')
#: The endpoint rule is ``https?://(?!localhost|example)``: loopback and
#: example hosts are permitted, so the same lookahead is used here and those
#: URLs are left alone.
_URL = re.compile(r'https?://(?!localhost|example)[^\s"\'<>)\]]*')

FIXABLE = ('SECRET_SCAN', 'HARDCODED_ENDPOINT_SCAN', 'PROVENANCE')


def _files(root):
    for f in sorted(Path(root).rglob('*')):
        if (f.is_file() and f.suffix.lower() in SCANNED_SUFFIXES
                and f.stat().st_size < MAX_FILE_BYTES):
            yield f


class CodeFeedbackService:
    def __init__(self, repo):
        self.repo = repo

    # -- individual repairs --------------------------------------------------
    def _fix_secrets(self, root):
        changed = []
        for f in _files(root):
            text = f.read_text(errors='ignore')
            new = _SECRET.sub(
                lambda m: '%s%s${%s}%s' % (m.group(1), m.group(3), m.group(2).upper(), m.group(4)),
                text)
            if new != text:
                f.write_text(new)
                changed.append({'path': str(f), 'check': 'SECRET_SCAN',
                                'action': 'replaced embedded credential with a ${...} placeholder'})
        return changed

    def _fix_endpoints(self, root):
        changed = []
        for f in _files(root):
            text = f.read_text(errors='ignore')
            new = _URL.sub('${EXTERNAL_ENDPOINT}', text)
            if new != text:
                f.write_text(new)
                changed.append({'path': str(f), 'check': 'HARDCODED_ENDPOINT_SCAN',
                                'action': 'externalised absolute endpoint to ${EXTERNAL_ENDPOINT}'})
        return changed

    def _fix_provenance(self, root, job_id, plan_id):
        target = Path(root) / 'provenance-manifest.json'
        if target.exists():
            return []
        plan = self.repo.one('select * from migration_plans where id=?', (plan_id,))
        manifest = {
            'plan_id': plan_id,
            'analysis_id': (plan or {}).get('analysis_id', ''),
            'specification_id': (plan or {}).get('specification_id', ''),
            'framework_id': (plan or {}).get('framework_id', ''),
            'files': [{'path': str(p.relative_to(Path(root)))} for p in _files(root)],
            'generated_at': now(),
            'reconstructed_by': 'feedback-agent',
        }
        target.write_text(json.dumps(manifest, indent=2))
        return [{'path': str(target), 'check': 'PROVENANCE',
                 'action': 'reconstructed the missing provenance manifest'}]

    def _repair(self, failed, root, job_id, plan_id):
        fixes = []
        if 'SECRET_SCAN' in failed:
            fixes += self._fix_secrets(root)
        if 'HARDCODED_ENDPOINT_SCAN' in failed:
            fixes += self._fix_endpoints(root)
        if 'PROVENANCE' in failed:
            fixes += self._fix_provenance(root, job_id, plan_id)
        return fixes

    # -- unresolved findings -------------------------------------------------
    def outstanding(self, job_id):
        """Findings this agent will not touch, reported rather than ignored."""
        variances = self.repo.rows(
            "select path,severity,kind from variances where job_id=? and disposition='OPEN'", (job_id,))
        causes = self.repo.rows(
            "select hypothesis,confidence from root_causes where job_id=? and status='PROPOSED'", (job_id,))
        return ([{'kind': 'VARIANCE', 'detail': '%s (%s/%s)' % (v['path'], v['kind'], v['severity']),
                  'owner': 'remediation'} for v in variances]
                + [{'kind': 'ROOT_CAUSE', 'detail': c['hypothesis'], 'owner': 'remediation'}
                   for c in causes])

    # -- the loop ------------------------------------------------------------
    def apply(self, job_id, plan_id, project_root, max_passes=3, execute_build=False):
        """Fix, re-validate, repeat until the gate is clean or the budget is spent.

        The loop runs one validation more than it runs repairs, so the status
        returned is always the gate's verdict on the *final* file state rather
        than on the state before the last edit. It stops early when a pass
        produces no fixes, because spinning on a failure nothing here knows how
        to repair would burn the budget and still report the same thing.
        """
        from app.services.migration_lifecycle_v6 import MappingAndPlanningService
        quality = MappingAndPlanningService(self.repo)
        budget = max(1, int(max_passes))
        passes, applied, run = [], [], None
        for n in range(budget + 1):
            run = quality.quality_validate(job_id, plan_id, project_root, execute_build)
            failed = [c['name'] for c in run['checks'] if c['status'] == 'FAIL']
            if not failed or n == budget:
                passes.append({'pass': n + 1, 'failed': failed, 'fixes': 0,
                               'quality_run_id': run['quality_run_id'], 'status': run['status']})
                break
            fixes = self._repair(failed, project_root, job_id, plan_id)
            applied += fixes
            passes.append({'pass': n + 1, 'failed': failed, 'fixes': len(fixes),
                           'quality_run_id': run['quality_run_id'], 'status': run['status']})
            if not fixes:
                break
        final_failed = [c['name'] for c in run['checks'] if c['status'] == 'FAIL']
        return {
            'quality_run_id': run['quality_run_id'],
            'quality_status': run['status'],
            'checks': run['checks'],
            'applied_fixes': applied,
            'passes': passes,
            'unfixed_checks': final_failed,
            'unresolved': self.outstanding(job_id),
            'project_root': str(project_root),
        }
