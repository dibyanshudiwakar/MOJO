"""Applying a root cause's fix to the generated project, and proving it did no harm.

Remediation used to stop at a plan. For root causes found in the static reports (parity and
hardcoding) the fix is a change to the migrated code, so this proposes targeted search/replace
edits with the model, applies them through the same path-confined check the feedback agent
uses, and keeps them only if the quality gate is no worse afterwards.

What "no worse" can and cannot show: the gate checks that the project builds, starts and meets
the skill's rules. It does not re-run the parity comparison, so a kept fix is *not regressed*,
not *proved closed*. Every fix is recorded with that verdict and nothing stronger.
"""
import json
import re
from pathlib import Path

from app.services import feedback

PROMPT = 'agent/remediation/code-fix'
MAX_FILES_PER_CAUSE = 8
MAX_FINDINGS_SHOWN = 25
MAX_PROJECT_FILES_LISTED = 100
#: A location in a report reads like ``OrderService.java:45``, ``com.x.OrderService#find`` or
#: ``OrderService (line 45)``. Class-like names and file names are both looked up.
_FILE = re.compile(r'[\w./-]+\.(?:java|ya?ml|properties|xml)')
_CLASS = re.compile(r'\b[A-Z][A-Za-z0-9_]{2,}\b')


def locate_project(repo, job_id):
    """``(project_root, plan_id)`` of the newest quality run whose project is still on disk."""
    for run in repo.rows('select project_root,plan_id from quality_runs where job_id=? order by created_at desc',
                         (job_id,)):
        root = str(run.get('project_root') or '')
        if root and Path(root).is_dir():
            return root, run.get('plan_id') or ''
    return '', ''


def latest_checks(repo, job_id):
    run = repo.one('select payload from quality_runs where job_id=? order by created_at desc limit 1', (job_id,))
    try:
        return (json.loads(run['payload'] or '{}').get('checks') or []) if run else []
    except (TypeError, ValueError):
        return []


def candidate_files(root, rows):
    """The project files the findings point at, as ``[(relative path, path)]``."""
    base = Path(root)
    files = [f for f in feedback._files(root)]
    by_name = {}
    for f in files:
        by_name.setdefault(f.name.lower(), []).append(f)
    by_stem = {}
    for f in files:
        by_stem.setdefault(f.stem.lower(), []).append(f)
    chosen = []

    def take(candidates):
        for c in candidates:
            if c not in chosen:
                chosen.append(c)

    for r in rows:
        place = r.get('ms_source') or ''
        for name in _FILE.findall(place):
            take(by_name.get(Path(name).name.lower(), []))
        for word in _CLASS.findall(place):
            take(by_stem.get(word.lower(), []))
    if not chosen:
        # A GAP often names nothing in the migrated code (it is missing). The service and
        # controller classes and the main configuration are where such a thing is added.
        take([f for f in files if f.suffix == '.java' and any(k in f.name for k in ('Service', 'Controller'))][:4])
        take([f for f in files if f.name.startswith('application')][:2])
    return [(str(f.relative_to(base)).replace('\\', '/'), f) for f in chosen[:MAX_FILES_PER_CAUSE]]


def propose(context, repo, cause, rows, root):
    """Ask the model for edits to the files the findings point at. Returns ``(answer, provenance, files)``."""
    from app.agents.reasoning import ReasoningGateway
    files = candidate_files(root, rows)
    shown = [{'path': rel, 'content': feedback.redact(feedback.lp(f).read_text(errors='ignore')[:feedback.MAX_CITED_FILE_CHARS])}
             for rel, f in files]
    listing = [str(f.relative_to(Path(root))).replace('\\', '/') for f in feedback._files(root)][:MAX_PROJECT_FILES_LISTED]
    payload = {
        'root_cause': cause['hypothesis'],
        'findings': [{'status': r['status'], 'area': r['area'], 'iib_source': r['iib_source'],
                      'iib_value': r['iib_value'][:300], 'ms_source': r['ms_source'],
                      'ms_value': r['ms_value'][:300], 'remarks': r['remarks'][:400],
                      **({'fix_hint': r['fix_hint']} if r.get('fix_hint') else {})}
                     for r in rows[:MAX_FINDINGS_SHOWN]],
        'files': shown, 'project_files': listing}
    answer, provenance = ReasoningGateway(repo).reason(context, PROMPT, payload)
    return answer, provenance, [rel for rel, _ in files]


def apply_edits(root, edits):
    """Apply accepted edits, returning ``(applied, refused, snapshot)``; ``snapshot`` restores them."""
    applied, refused, snapshot = [], [], {}
    for edit in list(edits)[:feedback.MAX_EDITS]:
        target, why = feedback.accept_edit(root, edit)
        if target is None:
            refused.append(why)
            continue
        text = feedback.lp(target).read_text(errors='ignore')
        snapshot.setdefault(target, text)
        feedback.lp(target).write_text(text.replace(str(edit['old_str']), str(edit.get('new_str', '')), 1))
        applied.append({'path': str(target), 'check': 'REMEDIATION',
                        'action': str(edit.get('why') or edit.get('reason') or 'model-proposed edit')[:200]})
    return applied, refused, snapshot


def save_snapshot(root, snapshot_id, snapshot):
    """Keep the files as they were before the kept edits, so the remediation can be undone.

    Written beside the project, not inside it: the quality gate and the repair loop scan the project
    tree, and an old copy of a file there would be checked as if it were part of the service.
    Returns the directory, which is the remediation's rollback pointer.
    """
    base = Path(root).resolve()
    out = base.parent / (base.name + '_rb') / snapshot_id[:8]
    for path, text in snapshot.items():
        target = out / Path(path).resolve().relative_to(base)
        target.parent.mkdir(parents=True, exist_ok=True)
        feedback.lp(target).write_text(text, encoding='utf-8')
    return str(out)


def restore(snapshot):
    for path, text in snapshot.items():
        feedback.lp(path).write_text(text)


def not_worse(before, after):
    """Nothing that was passing now fails, the build got no less far, and no more errors than before."""
    others_b, stage_b, errors_b = before
    others_a, stage_a, errors_a = after
    if not others_a <= others_b or stage_a < stage_b:
        return False
    return stage_a > stage_b or errors_a <= errors_b
