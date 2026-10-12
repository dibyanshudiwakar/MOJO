"""Evidence-bundle preconditions for late-lifecycle state changes.

A gate approval proves that somebody holding the right role signed. It does not
prove the migration produced anything. Because the only promotion check was
"an approval exists that cites at least one evidence row", a job could be walked
from CREATED to COMPLETED carrying one arbitrary blob per gate and no
differential result, no quality run, no rollback point and no version snapshot.

These checks are the second half of the gate: the approval says who agreed, the
bundle says what they agreed about. They are deliberately confined to the three
transitions where an unevidenced promotion is the actual risk, and each returns
the artefact references it verified so the state-change event records the bundle
that justified it.
"""
import json


class PreconditionError(PermissionError):
    """A state change was refused because its evidence bundle is incomplete."""


def _fail(target, reason):
    raise PreconditionError('precondition_unmet:%s:%s' % (target, reason))


def _differential(c, job, target, require_dispositioned):
    pkg = c.execute('select id,payload,content_hash from differential_packages where job_id=? order by created_at desc limit 1', (job['id'],)).fetchone()
    if not pkg:
        _fail(target, 'differential_package_missing')
    cases = {x.get('id') for x in json.loads(pkg['payload']).get('cases', []) if x.get('id')}
    if not cases:
        _fail(target, 'differential_package_has_no_cases:' + pkg['id'])
    rows = c.execute('select case_id,status,disposition from differential_evidence where job_id=? and package_id=?', (job['id'], pkg['id'])).fetchall()
    unexecuted = sorted(cases - {x['case_id'] for x in rows})
    if unexecuted:
        _fail(target, 'differential_cases_unexecuted:' + ','.join(unexecuted))
    bundle = {'differential_package_id': pkg['id'], 'differential_package_hash': pkg['content_hash'], 'case_count': len(cases)}
    if require_dispositioned:
        undispositioned = sorted({x['case_id'] for x in rows if (x['disposition'] or 'OPEN') == 'OPEN'})
        if undispositioned:
            _fail(target, 'differential_variances_undispositioned:' + ','.join(undispositioned))
        bundle['variance_count'] = sum(1 for x in rows if x['status'] == 'VARIANCE')
    return bundle


def _quality(c, job, target):
    q = c.execute('select id,status,content_hash from quality_runs where job_id=? order by created_at desc limit 1', (job['id'],)).fetchone()
    if not q:
        _fail(target, 'quality_run_missing')
    if q['status'] == 'FAIL':
        _fail(target, 'quality_run_failed:' + q['id'])
    return {'quality_run_id': q['id'], 'quality_status': q['status'], 'quality_run_hash': q['content_hash']}


def _security(c, job, target):
    open_high = c.execute("select count(*) from security_findings where job_id=? and status='OPEN' and severity='HIGH'", (job['id'],)).fetchone()[0]
    if open_high:
        _fail(target, 'open_high_security_findings:%d' % open_high)
    return {'open_high_security_findings': 0}


def _rollback(c, job, target):
    pointer = str(job['rollback_pointer'] or '').strip()
    if not pointer:
        _fail(target, 'rollback_pointer_missing')
    iteration = int(job['iteration'] or 1)
    v = c.execute('select id from versions where job_id=? and iteration=? order by created_at desc limit 1', (job['id'], iteration)).fetchone()
    if not v:
        _fail(target, 'version_snapshot_missing_for_iteration:%d' % iteration)
    return {'rollback_pointer': pointer, 'version_id': v['id'], 'iteration': iteration}


def _differential_executed(c, job, target):
    return _differential(c, job, target, False)


def _differential_dispositioned(c, job, target):
    return _differential(c, job, target, True)


REQUIRED = {
    'VARIANCE_REVIEW': (_differential_executed,),
    'READY_FOR_PROMOTION': (_differential_executed, _quality),
    'COMPLETED': (_differential_dispositioned, _quality, _security, _rollback),
}


def evidence_bundle(c, job, target):
    """Verify the bundle required to enter ``target`` and return its references."""
    bundle = {}
    for check in REQUIRED.get(target, ()):
        bundle.update(check(c, job, target))
    return bundle
