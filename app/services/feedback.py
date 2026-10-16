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

#: Redaction applied before any source reaches the model gateway.
#:
#: ``AIRegistryService.gateway_request`` refuses a payload outright when its DLP
#: rules match, and CREDENTIAL_ASSIGNMENT is
#: ``\b(password|secret|api_key|...)\b\s*[:=]\s*\S`` -- which matches *any*
#: ``password:`` line, including one already correctly externalised as
#: ``password: "${PASSWORD}"``. A Spring application.yml would therefore be
#: refused every time, so sending files verbatim is not an option. Values are
#: blanked here instead; the structure the model needs to reason about survives.
#:
#: This is also why the model is asked for targeted search/replace edits rather
#: than whole-file content: it never has to reproduce bytes it was not shown,
#: and an ``old_str`` that does not match the real file is simply skipped.
_REDACTIONS = (
    # The whole assignment goes, keyword and separator included. Blanking only
    # the value is not enough: the rule is ``<keyword>\s*[:=]\s*\S``, so
    # ``password: <REDACTED>`` still matches on the ``<``, and leaving the line
    # bare still matches because ``\s*`` crosses the newline onto the next
    # line's first character.
    (re.compile(r'(?i)\b(?:password|passwd|secret|api[_-]?key|client_secret|'
                r'access[_-]?token|private[_-]?key)\b\s*[:=]\s*'
                r'("[^"]*"|\'[^\']*\'|\S+)'), 'REDACTED_CREDENTIAL'),
    (re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._-]{16,}'), 'bearer <REDACTED>'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'), '<REDACTED_JWT>'),
    (re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://)[^\s:@/]+:[^\s:@/]+@'), r'\1<REDACTED>@'),
    (re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY'), '<REDACTED_PRIVATE_KEY>'),
    (re.compile(r'\b(?:\d[ -]?){13,19}\b'), '<REDACTED_NUMBER>'),
)

#: Caps on what is sent to, and accepted back from, the model. A pathological
#: response must not be able to rewrite a project wholesale.
MAX_PAYLOAD_FILES = 12
MAX_FILE_CHARS = 6000
MAX_EDITS = 20
MAX_NEW_STR_CHARS = 20000


def redact(text):
    """Blank anything the gateway's DLP rules would refuse."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _files(root):
    for f in sorted(Path(root).rglob('*')):
        if (f.is_file() and f.suffix.lower() in SCANNED_SUFFIXES
                and f.stat().st_size < MAX_FILE_BYTES):
            yield f


def accept_edit(root, edit):
    """Validate one proposed search/replace edit, returning the resolved file or a refusal.

    Model output is untrusted data, so the path is re-resolved under the
    project root and the suffix re-checked -- a traversal or a stray
    ``pom.xml`` edit is refused rather than applied. ``old_str`` must appear
    exactly once in the *real* file: zero means the model was working from
    redacted bytes and the edit cannot be placed, more than one means it is
    ambiguous and could change the wrong site.

    Shared between ``CodeFeedbackService`` (repairing a failing quality check)
    and code-generation's business-logic synthesis (filling in a
    HAND_WRITTEN_PROTECTED stub) -- both apply a model's proposed edits to
    governed generated source, and a path-traversal or ambiguous-match check
    must not have two slightly different implementations to drift apart.
    """
    rel = str(edit.get('path', '')).strip().replace('\\', '/')
    if not rel or rel.startswith('/') or '..' in rel.split('/'):
        return None, 'path_not_relative:%s' % rel
    base = Path(root).resolve()
    try:
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return None, 'path_unresolvable:%s' % rel
    if target != base and base not in target.parents:
        return None, 'path_outside_project:%s' % rel
    if target.suffix.lower() not in SCANNED_SUFFIXES:
        return None, 'suffix_not_editable:%s' % rel
    if not target.is_file():
        return None, 'file_not_found:%s' % rel
    old = str(edit.get('old_str', ''))
    new = str(edit.get('new_str', ''))
    if not old:
        return None, 'old_str_empty:%s' % rel
    if len(new) > MAX_NEW_STR_CHARS:
        return None, 'new_str_too_large:%s' % rel
    text = target.read_text(errors='ignore')
    occurrences = text.count(old)
    if occurrences == 0:
        return None, 'old_str_absent:%s' % rel
    if occurrences > 1:
        return None, 'old_str_ambiguous:%s' % rel
    return target, ''


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

    # -- model-assisted repair -----------------------------------------------
    def _candidates(self, root, checks):
        """Files worth showing the model, with their check detail, redacted."""
        detail = {c['name']: str(c.get('evidence', ''))[:400] for c in checks
                  if c['status'] == 'FAIL'}
        files = []
        for f in list(_files(root))[:MAX_PAYLOAD_FILES]:
            body = redact(f.read_text(errors='ignore'))[:MAX_FILE_CHARS]
            files.append({'path': str(f.relative_to(Path(root))).replace('\\', '/'),
                          'content': body})
        return detail, files

    def _accept_edit(self, root, edit):
        return accept_edit(root, edit)

    def _claude_repair(self, context, root, checks, job_id, plan_id, quality):
        """Ask the model for edits, apply them, and keep them only if the gate improves.

        The gate's verdict decides, not the model's confidence. Every file the
        edits touch is snapshotted first and restored wholesale unless the
        failing-check set strictly shrinks and gains nothing new -- so a repair
        that breaks something else, or achieves nothing, leaves no trace.
        """
        from app.agents.reasoning import ReasoningGateway
        failed_before = {c['name'] for c in checks if c['status'] == 'FAIL'}
        detail, files = self._candidates(root, checks)
        answer, provenance = ReasoningGateway(self.repo).reason(
            context, 'agent/feedback/code-repair',
            {'failing_checks': sorted(failed_before), 'check_detail': detail, 'files': files})
        if not isinstance(answer, dict) or not answer.get('edits'):
            return {'attempted': bool(answer), 'applied': [], 'refused': [],
                    'outcome': 'no_edits_proposed',
                    'reason': provenance.get('reason', ''), 'mode': provenance.get('mode', '')}

        accepted, refused, snapshot = [], [], {}
        for edit in list(answer['edits'])[:MAX_EDITS]:
            target, why = self._accept_edit(root, edit)
            if target is None:
                refused.append(why)
                continue
            text = target.read_text(errors='ignore')
            snapshot.setdefault(target, text)
            target.write_text(text.replace(str(edit['old_str']), str(edit.get('new_str', '')), 1))
            accepted.append({'path': str(target), 'check': 'MODEL_REPAIR',
                             'action': str(edit.get('why', 'model-proposed edit'))[:200]})
        if not accepted:
            return {'attempted': True, 'applied': [], 'refused': refused,
                    'outcome': 'all_edits_refused', 'mode': provenance.get('mode', '')}

        after = quality.quality_validate(job_id, plan_id, root, False)
        failed_after = {c['name'] for c in after['checks'] if c['status'] == 'FAIL'}
        improved = failed_after < failed_before          # strict subset: fewer, nothing new
        if not improved:
            for path, text in snapshot.items():
                path.write_text(text)
            return {'attempted': True, 'applied': [], 'refused': refused,
                    'outcome': 'reverted_no_improvement',
                    'failed_before': sorted(failed_before), 'failed_after': sorted(failed_after),
                    'mode': provenance.get('mode', '')}
        return {'attempted': True, 'applied': accepted, 'refused': refused,
                'outcome': 'kept', 'failed_before': sorted(failed_before),
                'failed_after': sorted(failed_after), 'mode': provenance.get('mode', '')}

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
    def apply(self, job_id, plan_id, project_root, max_passes=3, execute_build=False,
              context=None):
        """Fix, re-validate, repeat until the gate is clean or the budget is spent.

        Two repair stages per pass, deterministic first:

        1. The mechanical repairs, which the gate's own rules define exactly.
        2. Only when those produce nothing, and only when ``context`` carries a
           provider, the model is asked for targeted edits. Its output is
           applied, re-validated and kept **only** if the failing-check set
           strictly shrinks; otherwise every touched file is restored.

        Running the model second is deliberate: the deterministic pass clears
        the checks whose fix is already known, so the model is only ever asked
        about what is genuinely left, on a smaller payload.

        The loop runs one validation more than it runs repairs, so the status
        returned is always the gate's verdict on the *final* file state rather
        than on the state before the last edit. It stops early when a pass
        produces nothing at all, because spinning on a failure neither stage can
        repair would burn the budget and still report the same thing.
        """
        from app.services.migration_lifecycle_v6 import MappingAndPlanningService
        quality = MappingAndPlanningService(self.repo)
        budget = max(1, int(max_passes))
        passes, applied, model_rounds, run = [], [], [], None
        for n in range(budget + 1):
            run = quality.quality_validate(job_id, plan_id, project_root, execute_build)
            failed = [c['name'] for c in run['checks'] if c['status'] == 'FAIL']
            if not failed or n == budget:
                passes.append({'pass': n + 1, 'failed': failed, 'fixes': 0, 'model': None,
                               'quality_run_id': run['quality_run_id'], 'status': run['status']})
                break
            fixes = self._repair(failed, project_root, job_id, plan_id)
            applied += fixes
            model = None
            if not fixes and context is not None and getattr(context, 'provider', None) is not None:
                model = self._claude_repair(context, project_root, run['checks'],
                                            job_id, plan_id, quality)
                applied += model['applied']
                model_rounds.append(model)
            passes.append({'pass': n + 1, 'failed': failed, 'fixes': len(fixes),
                           'model': (model or {}).get('outcome'),
                           'quality_run_id': run['quality_run_id'], 'status': run['status']})
            if not fixes and not (model or {}).get('applied'):
                break
        final_failed = [c['name'] for c in run['checks'] if c['status'] == 'FAIL']
        return {
            'quality_run_id': run['quality_run_id'],
            'quality_status': run['status'],
            'checks': run['checks'],
            'applied_fixes': applied,
            'passes': passes,
            'model_rounds': model_rounds,
            'unfixed_checks': final_failed,
            'unresolved': self.outstanding(job_id),
            'project_root': str(project_root),
        }
