"""HTML reports for a failed pipeline step and for a failed quality gate.

A failure used to be a one-line error and a status. These collect what a person needs to act on it
in one page that opens like the variance-analysis and ACE-analysis reports do: what failed and why,
the evidence behind it (build output, failing checks), what usually fixes it, and how far the
pipeline got.

Two kinds, stored as ``validation_reports`` rows and rendered from their JSON on request so the page
always reflects the stored facts and nothing is interpreted as markup (every value is escaped):

* ``QUALITY_GATE_REPORT`` -- one per failed quality run (code generation, feedback).
* ``FAILURE_REPORT`` -- one per failed pipeline step.

The pages reuse the static-check reports' stylesheet and badge classes, so they look the same and need
no new CSS in the shared report stylesheet.
"""
import json

from app.repository import digest, now, uid
from app.services import static_check_reports as H

QUALITY = 'QUALITY_GATE_REPORT'
FAILURE = 'FAILURE_REPORT'
REPORT_TYPES = (QUALITY, FAILURE)

#: Lines of a step's log, build errors and evidence shown on a page. Enough to diagnose, never the whole log.
MAX_LOG_LINES = 40
MAX_ERRORS = 40
MAX_EVIDENCE_LINES = 12
MAX_TEXT = 4000

#: Quality statuses shown with the badge colours the shared stylesheet already has.
_BADGE = {'PASS': 'MATCH', 'GAP': 'PARTIAL', 'FAIL': 'GAP', 'FAILED': 'GAP', 'COMPLETED': 'MATCH',
          'SKIPPED': 'OTHER', 'RUNNING': 'PARTIAL', 'PENDING': 'OTHER', 'CANCELLED': 'OTHER',
          'WAITING_APPROVAL': 'PARTIAL', 'WAITING_CLARIFICATION': 'PARTIAL'}


def html_url(report_id):
    return '/api/v2/reports/%s/html' % report_id


# -- what usually fixes it -------------------------------------------------------------------------------
#: (substring of the error, title, what it means, what to do). First match wins, so specific entries come first.
HINTS = (
    ('required_agent_role_missing', 'The account lacks the role this agent needs',
     'Every agent needs the role named in its contract; the pipeline runs as the account that created it.',
     ['Sign in again if roles were changed recently (a token carries the roles it was issued with)',
      'Ask an administrator to confirm the pipeline owner holds the agent\'s role',
      'Retry the step']),
    ('remediation_requires_pattern_tests_and_rollback', 'Remediation had no rollback pointer',
     'A remediation must name how to undo it. The job has none recorded.',
     ['Retry the step after upgrading: remediation now records its own rollback point',
      'Or set a rollback pointer on the job']),
    ('agent_dependency_not_satisfied', 'An earlier agent has not produced what this one needs',
     'The agent needs a completed run of an upstream agent for this job and found none.',
     ['Open the upstream step named in the error and fix or re-run it first',
      'Retry this step once the upstream step is COMPLETED']),
    ('artefact_unavailable', 'The model could not produce the artefact',
     'The step asked the model for an artefact and no valid answer came back after the allowed attempts.',
     ['Check the model gateway token and quota, then retry the step',
      'Look at the Logs tab for the last model response status',
      'If it keeps failing with a schema error, the prompt or schema needs review']),
    ('required_gate_approval_missing', 'A governance gate has not been approved',
     'The step is blocked by an approval gate that is still open.',
     ['Open the Approval Inbox and have the gate approver decide', 'Retry once the gate is approved']),
    ('quality gate', 'The quality gate failed', 'The generated project did not pass its quality checks.',
     ['Read the failing checks in the quality gate report', 'Run the feedback agent, or fix the named checks']),
    ('non-resolvable parent pom', 'Maven could not resolve the parent POM',
     'The build could not download the framework parent. Its artefacts resolve only through the internal '
     'repositories named in settings.xml.',
     ['Confirm settings.xml is at the project root and the build passes it with -s',
      'Confirm this machine can reach the internal Nexus and has credentials if it requires them',
      'If a framework repository was supplied, check that its own build succeeded first']),
    ('timed out', 'The operation timed out', 'It did not finish in the time allowed.',
     ['Check connectivity to the host involved', 'Retry the step']),
    ('permission', 'Permission denied', 'The account or token does not have the access the step needs.',
     ['Check the GitHub token scopes and repository access', 'Check the account\'s roles']),
    ('clone', 'Repository clone failed', 'The repository could not be cloned.',
     ['Confirm the URL and branch exist', 'Check the GitHub token is valid and has the repo scope']),
)


def hint_for(error):
    """``(title, meaning, [next steps])`` for an error message."""
    low = str(error or '').lower()
    for needle, title, meaning, steps in HINTS:
        if needle in low:
            return title, meaning, list(steps)
    return ('The step failed', 'An error the platform has no specific advice for.',
            ['Read the technical details below and the step log', 'Retry the step to rule out a transient fault'])


# -- helpers -----------------------------------------------------------------------------------------------
def _badge(status):
    label = str(status or '—').upper()
    return '<span class="badge %s">%s</span>' % (_BADGE.get(label, 'OTHER'), H._e(label.replace('_', ' ')))


def _pre(text, limit=MAX_TEXT):
    text = str(text or '')
    return '<code>%s</code>' % H._e(text if len(text) <= limit else text[:limit] + '\n... (truncated)')


def _lines(value, cap=MAX_EVIDENCE_LINES):
    if value is None or value == '':
        return []
    if isinstance(value, dict):
        return [json.dumps(value, sort_keys=True, default=str)[:600]]
    if isinstance(value, (list, tuple)):
        out = []
        for x in value:
            out += _lines(x, cap)
        return out[:cap]
    return [str(value)[:600]]


def _page(title, subtitle, body):
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex"><title>%s</title><style>%s</style></head>'
            '<body><main><h1>%s</h1><p class="sub">%s</p>%s</main></body></html>'
            % (H._e(title), H.CSS, H._e(title), subtitle or '&nbsp;', ''.join(body)))


def _footer(meta):
    labels = [('report_id', 'Report'), ('job_id', 'Job'), ('generated_at', 'Generated'), ('reference', 'About')]
    items = ''.join('<dt>%s</dt><dd>%s</dd>' % (H._e(label), H._e(meta[key])) for key, label in labels if meta.get(key))
    return ('<footer><div>Built from the platform\'s own records of this failure. It reports what was recorded; '
            'it does not change anything.</div><dl>%s</dl></footer>' % items)


# -- quality gate -----------------------------------------------------------------------------------------
def _quality_data(repo, quality_run_id):
    run = repo.one('select * from quality_runs where id=?', (quality_run_id,))
    if not run:
        return None
    payload = json.loads(run['payload'] or '{}')
    checks = []
    build = None
    for c in payload.get('checks', []):
        evidence = c.get('evidence')
        entry = {'name': c.get('name', ''), 'status': c.get('status', ''), 'severity': c.get('severity', ''),
                 'skill_ref': c.get('skill_ref', ''), 'remediation': c.get('remediation', ''),
                 'evidence': _lines(evidence.get('errors') if isinstance(evidence, dict) and evidence.get('errors') else
                                    evidence)}
        if c.get('name') == 'COMPILE_AND_UNIT_TEST' and isinstance(evidence, dict):
            build = {k: evidence.get(k) for k in ('status', 'command', 'returncode', 'started_at', 'completed_at', 'reason')}
            build['errors'] = (evidence.get('errors') or [])[:MAX_ERRORS]
            if evidence.get('framework'):
                build['framework'] = evidence['framework']
        checks.append(entry)
    return {'quality_run_id': quality_run_id, 'job_id': run['job_id'], 'plan_id': run.get('plan_id', ''),
            'status': run['status'], 'project_root': run.get('project_root', ''), 'created_at': run.get('created_at', ''),
            'checks': checks, 'build': build}


def record_quality(repo, job_id, quality_run_id):
    """Store the report for a quality run (once per run) and return ``{'report_id', 'html_url'}``."""
    existing = repo.one('select id from validation_reports where report_type=? and reference_id=?', (QUALITY, quality_run_id))
    if existing:
        return {'report_id': existing['id'], 'html_url': html_url(existing['id'])}
    data = _quality_data(repo, quality_run_id)
    if data is None:
        return None
    rid, payload = uid(), json.dumps(data, sort_keys=True, default=str)
    with repo.connect() as c:
        repo.insert(c, 'validation_reports', {'id': rid, 'job_id': job_id, 'report_type': QUALITY, 'version': '1.0.0',
                                              'payload': payload, 'content_hash': digest(payload), 'status': 'ANALYSED',
                                              'created_at': now(), 'reference_id': quality_run_id})
    return {'report_id': rid, 'html_url': html_url(rid)}


def render_quality(data, meta):
    checks = data['checks']
    failed = [c for c in checks if c['status'] == 'FAIL']
    gaps = [c for c in checks if c['status'] == 'GAP']
    pending = [c for c in checks if str(c['status']).startswith('NOT_')]
    body = [
        '<p class="note">The generated project was checked for structure, secrets, hard-coded endpoints, provenance, '
        'its build and start-up, and the code-generation skill\'s own checklist. Any FAIL stops the gate.</p>',
        '<div class="tiles">%s</div>' % ''.join([
            H._tile('Gate', data['status'].replace('_', ' '), 'GAP' if data['status'] == 'FAIL' else 'MATCH'),
            H._tile('Checks', len(checks)), H._tile('Failed', len(failed), 'GAP'),
            H._tile('Gaps', len(gaps), 'PARTIAL'), H._tile('Not executed', len(pending))]),
    ]
    if failed:
        rows = []
        for c in failed:
            ev = ''.join(_pre(line, 700) for line in c['evidence'][:MAX_EVIDENCE_LINES]) or '<span class="empty">no detail recorded</span>'
            fix = H._e(c['remediation']) if c['remediation'] else '<span class="empty">see the build output below</span>'
            rows.append([H._e(c['name']), _badge(c['status']), ev, fix])
        body.append('<h2>Why it failed<small>%d</small></h2>%s' % (len(failed), H._table(
            ['Check', 'Status', 'Evidence', 'How to fix'], rows, ['18%', '8%', '46%', '28%'])))
    b = data.get('build')
    if b:
        fw = b.get('framework') or {}
        kv = [('Command', ' '.join(map(str, b.get('command') or []))), ('Exit code', b.get('returncode')),
              ('Reason', b.get('reason'))]
        if fw:
            kv.insert(0, ('Framework build', '%s %s' % (fw.get('status', ''), fw.get('note', ''))))
        body.append('<h2>Build</h2>' + H._table(['Item', 'Value'], [[H._e(k), _pre(v, 600)] for k, v in kv if v not in (None, '')],
                                                ['22%', '78%']))
        if b.get('errors'):
            body.append('<h2>Build errors<small>%d</small></h2>%s' % (len(b['errors']), ''.join(_pre(x, 800) for x in b['errors'])))
    if gaps:
        rows = [[H._e(c['name']), H._e(c['skill_ref']), ''.join(_pre(x, 500) for x in c['evidence'][:6]), H._e(c['remediation'])]
                for c in gaps]
        body.append('<h2>Open skill gaps<small>%d</small></h2>%s' % (len(gaps), H._table(
            ['Check', 'Skill reference', 'Evidence', 'How to fix'], rows, ['16%', '20%', '38%', '26%'])))
    rows = [[H._e(c['name']), _badge(c['status']), H._e(c['severity'] or '—')] for c in checks]
    body.append('<h2>All checks<small>%d</small></h2>%s' % (len(checks), H._table(['Check', 'Status', 'Severity'], rows,
                                                                                    ['56%', '20%', '24%'])))
    meta = dict(meta, reference='quality run %s' % data['quality_run_id'])
    body.append(_footer(meta))
    return _page('Quality gate report', H._e(data.get('project_root') or ''), body)


# -- pipeline failure -------------------------------------------------------------------------------------
def _failure_data(repo, step_id):
    step = repo.one('select * from pipeline_steps where id=?', (step_id,))
    if not step:
        return None
    pipeline = repo.one('select * from pipelines where id=?', (step['pipeline_id'],)) or {}
    steps = repo.rows('select id,sequence,kind,agent_id,status,started_at,completed_at,error from pipeline_steps '
                      'where pipeline_id=? order by sequence', (step['pipeline_id'],))
    logs = repo.rows('select created_at,level,message from pipeline_step_logs where step_id=? order by created_at',
                     (step_id,))[-MAX_LOG_LINES:]
    outputs = json.loads(step.get('outputs') or '{}')
    run = repo.one('select model_provenance,effective_model_alias,binding_source from agent_runs where id=?',
                   (outputs.get('_run_id', ''),)) if outputs.get('_run_id') else None
    provenance = json.loads((run or {}).get('model_provenance') or '{}')
    job = repo.one('select service,state from jobs where id=?', (pipeline.get('job_id', ''),)) or {}
    quality = repo.one('select id,status from quality_runs where job_id=? order by created_at desc limit 1',
                       (pipeline.get('job_id', ''),)) if step.get('agent_id') in ('code-generation', 'feedback') else None
    return {
        'step': {k: step.get(k, '') for k in ('id', 'kind', 'agent_id', 'status', 'started_at', 'completed_at', 'error')},
        'pipeline': {k: pipeline.get(k, '') for k in ('id', 'name', 'status', 'workspace_dir', 'job_id')},
        'job': job, 'steps': steps, 'logs': [[l['created_at'], l['level'], l['message']] for l in logs],
        'model': {'mode': provenance.get('mode', ''), 'alias': (run or {}).get('effective_model_alias', ''),
                  'prompt': provenance.get('prompt_name', ''), 'reason': provenance.get('reason', '')} if run else {},
        'quality_run_id': (quality or {}).get('id', '')}


def record_failure(repo, step_id):
    """Store a report for a failed step and return ``{'report_id', 'html_url'}`` (newest report for the step wins)."""
    data = _failure_data(repo, step_id)
    if data is None:
        return None
    rid, payload = uid(), json.dumps(data, sort_keys=True, default=str)
    with repo.connect() as c:
        repo.insert(c, 'validation_reports', {'id': rid, 'job_id': data['pipeline'].get('job_id', ''), 'report_type': FAILURE,
                                              'version': '1.0.0', 'payload': payload, 'content_hash': digest(payload),
                                              'status': 'ANALYSED', 'created_at': now(), 'reference_id': step_id})
    return {'report_id': rid, 'html_url': html_url(rid)}


def render_failure(data, meta):
    step, pipe = data['step'], data['pipeline']
    title, meaning, advice = hint_for(step.get('error'))
    name = step.get('agent_id') or step.get('kind') or 'step'
    body = [
        '<p class="note">%s</p>' % H._e(meaning),
        '<div class="tiles">%s</div>' % ''.join([
            H._tile('Step', name), H._tile('Status', str(step.get('status', '')).replace('_', ' '), 'GAP'),
            H._tile('Pipeline', str(pipe.get('status', '')).replace('_', ' ')),
            H._tile('Service', (data.get('job') or {}).get('service', '') or '—')]),
        '<h2>What happened</h2><div class="card"><p><b>%s</b></p>%s</div>' % (H._e(title), _pre(step.get('error') or 'No error text was recorded.')),
        '<h2>What to do next</h2><div class="card">%s</div>' % ('<ol>%s</ol>' % ''.join('<li>%s</li>' % H._e(a) for a in advice)),
    ]
    if data.get('quality_run_id'):
        body.append('<p class="note">This step also left a quality run (%s). Its report lists every failing check.</p>' % H._e(data['quality_run_id']))
    if data.get('model'):
        m = data['model']
        body.append('<h2>Model</h2>' + H._table(['Item', 'Value'], [[H._e(k), H._e(v)] for k, v in (
            ('Mode', m.get('mode')), ('Alias', m.get('alias')), ('Prompt', m.get('prompt')), ('Reason', m.get('reason'))) if v],
            ['22%', '78%']))
    rows = [[H._e(s['sequence']), H._e(s['agent_id'] or s['kind']), _badge(s['status']), H._e(s['started_at'] or '—'),
             H._e(s['completed_at'] or '—')] for s in data['steps']]
    body.append('<h2>Pipeline progress<small>%d</small></h2>%s' % (len(rows), H._table(
        ['#', 'Step', 'Status', 'Started', 'Finished'], rows, ['6%', '30%', '16%', '24%', '24%'])))
    if data['logs']:
        body.append('<h2>Step log<small>last %d</small></h2>%s' % (len(data['logs']), ''.join(
            _pre('%s  [%s]  %s' % (t, lvl, msg), 900) for t, lvl, msg in data['logs'])))
    meta = dict(meta, reference='pipeline %s · step %s' % (pipe.get('id', ''), step.get('id', '')))
    body.append(_footer(meta))
    return _page('Failure report — %s' % name, H._e(pipe.get('name') or pipe.get('workspace_dir') or ''), body)


# -- serving ------------------------------------------------------------------------------------------------
def render(row):
    """The HTML page for a stored ``validation_reports`` row of either kind."""
    data = json.loads(row['payload'] or '{}')
    meta = {'report_id': row.get('id', ''), 'job_id': row.get('job_id', ''), 'generated_at': row.get('created_at', '')}
    return (render_quality if row['report_type'] == QUALITY else render_failure)(data, meta)


def latest_for_job(repo, job_id, report_type=QUALITY):
    row = repo.one('select id from validation_reports where job_id=? and report_type=? order by created_at desc limit 1',
                   (job_id, report_type))
    return {'report_id': row['id'], 'html_url': html_url(row['id'])} if row else None


def for_step(repo, step_id):
    row = repo.one('select id from validation_reports where report_type=? and reference_id=? order by created_at desc limit 1',
                   (FAILURE, step_id))
    return {'report_id': row['id'], 'html_url': html_url(row['id'])} if row else None
