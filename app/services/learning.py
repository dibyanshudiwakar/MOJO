"""What each pipeline run teaches, kept so the next one can use it.

Nothing here trains a model. A run leaves evidence of what went wrong and what
fixed it -- a schema violation the model had to be corrected on, a skill GAP the
gate named, an edit the feedback agent made, an error that failed the run -- and
that evidence is thrown away when the next run starts. This keeps it:

* ``run_metrics``: one row per agent run, so "is this agent getting better" has
  numbers to answer it (how many attempts, how many corrections, how many gaps).
* ``lessons``: one row per *distinct* situation, counting how often it recurs and
  across how many jobs. A lesson seen once is an incident; the same one seen in
  five jobs is a defect in a prompt, a skill or a gate rule, which is where a fix
  belongs.

Capture only, by design. Lessons start ``CAPTURED``; nothing reads them back into
a prompt yet. Putting unreviewed text into prompts would let one bad run teach
every later run the wrong thing, so curation (``APPROVED`` / ``REJECTED``) comes
before retrieval. Recording must never fail a run, so every entry point here
swallows its own errors.
"""
import json
import re
import sys

from app.repository import now, uid

#: Distinct jobs remembered per lesson. Enough to show breadth; not unbounded.
MAX_JOBS = 20
MAX_TEXT = 600

CAPTURED, APPROVED, REJECTED = 'CAPTURED', 'APPROVED', 'REJECTED'

SCHEMA = '''
create table if not exists run_metrics(run_id text primary key, job_id text, agent_id text, agent_version text,
  status text, duration_seconds real, attempts integer, corrections integer, effort_step_downs integer,
  gap_count integer, failed_checks integer, clarifications integer, error text, created_at text);
create table if not exists lessons(id text primary key, agent_id text, kind text, signature text,
  situation text, resolution text, evidence text, occurrences integer, jobs text, scope text,
  status text, first_seen text, last_seen text, unique(agent_id, kind, signature));
'''


def ensure(conn):
    for statement in [s for s in SCHEMA.split(';') if s.strip()]:
        conn.execute(statement)


def normalise(text):
    """A stable key for a situation: no ids, numbers or quoting that vary run to run."""
    text = re.sub(r"['\"`]", '', str(text or '').lower())
    text = re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27}', '#', text)
    text = re.sub(r'\d+', '#', text)
    return re.sub(r'\s+', ' ', text).strip()[:160]


def _lesson(agent_id, kind, signature, situation, resolution, evidence=None):
    return {'agent_id': agent_id, 'kind': kind, 'signature': normalise(signature),
            'situation': str(situation)[:MAX_TEXT], 'resolution': str(resolution)[:MAX_TEXT],
            'evidence': evidence or {}}


def lessons_from(agent_id, status, metrics, reasoning, outputs, error=''):
    """The lessons one finished run offers. Pure: no database, so it is testable alone."""
    out = []
    metrics, reasoning, outputs = metrics or {}, reasoning or {}, outputs or {}
    prompt = reasoning.get('prompt_name', '')
    for text in (reasoning.get('all_corrections') or reasoning.get('corrections') or []):
        out.append(_lesson(
            agent_id, 'SCHEMA_CORRECTION', '%s|%s' % (prompt, text),
            'The model returned an answer that broke the schema for %s: %s' % (prompt or agent_id, text),
            'Asking again with the violation named produced a valid answer. The prompt can state this rule up front.',
            {'prompt': prompt}))
    for step in (reasoning.get('all_effort_lowered') or reasoning.get('effort_lowered') or []):
        out.append(_lesson(
            agent_id, 'EFFORT_STEP_DOWN', '%s|%s' % (prompt, step),
            'A reply for %s was cut off after the model reasoned (%s).' % (prompt or agent_id, step),
            'It succeeded one effort level down. That prompt can start there and save the failed attempt.',
            {'prompt': prompt, 'step': step}))
    for gap in metrics.get('gaps') or []:
        if isinstance(gap, dict):
            out.append(_lesson(
                agent_id, 'GATE_GAP', gap.get('check', ''),
                'The skill gate named %s (%s): %s' % (gap.get('check'), gap.get('skill_ref', ''),
                                                      '; '.join(str(f) for f in gap.get('findings', [])[:2])),
                gap.get('remediation', ''), {'skill_ref': gap.get('skill_ref', '')}))
    for check in metrics.get('failed_checks') or []:
        name = check if isinstance(check, str) else (check or {}).get('name', '')
        out.append(_lesson(agent_id, 'GATE_FAIL', name,
                           'The quality gate failed %s on freshly generated code.' % name,
                           'Handed to the feedback agent to repair.'))
    for fix in outputs.get('applied_fixes') or []:
        if isinstance(fix, dict):
            out.append(_lesson(
                agent_id, 'REPAIR', '%s|%s' % (fix.get('check', ''), fix.get('action', '')),
                'The feedback agent had to repair %s.' % fix.get('check', ''),
                fix.get('action', ''), {'check': fix.get('check', '')}))
    for name in metrics.get('unfixed_gaps') or []:
        out.append(_lesson(agent_id, 'GAP_UNREPAIRED', name,
                           'Skill gap %s was still open after the feedback agent finished.' % name,
                           'No repair closed it: a candidate for a rule in the generation skill or a new repair.'))
    if error and status in ('FAILED', 'ERROR'):
        out.append(_lesson(agent_id, 'RUN_FAILURE', error, 'The run failed: %s' % error[:300],
                           'Unresolved at the time of the run.'))
    return out


def _upsert(conn, lesson, job_id):
    row = conn.execute('select id, occurrences, jobs from lessons where agent_id=? and kind=? and signature=?',
                       (lesson['agent_id'], lesson['kind'], lesson['signature'])).fetchone()
    ts = now()
    if row:
        rid, count, jobs = row['id'], row['occurrences'], json.loads(row['jobs'] or '[]')
        if job_id and job_id not in jobs:
            jobs = (jobs + [job_id])[-MAX_JOBS:]
        conn.execute('update lessons set occurrences=?, jobs=?, last_seen=? where id=?',
                     ((count or 0) + 1, json.dumps(jobs), ts, rid))
        return
    conn.execute('insert into lessons(id,agent_id,kind,signature,situation,resolution,evidence,occurrences,'
                 'jobs,scope,status,first_seen,last_seen) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (uid(), lesson['agent_id'], lesson['kind'], lesson['signature'], lesson['situation'],
                  lesson['resolution'], json.dumps(lesson['evidence']), 1,
                  json.dumps([job_id] if job_id else []), 'SERVICE', CAPTURED, ts, ts))


def record_run(repo, *, run_id, job_id, agent_id, agent_version, status, started_at, metrics=None,
               reasoning=None, outputs=None, clarifications=0, error=''):
    """Store the run's metrics and lessons. Never raises: learning must not fail a run."""
    try:
        metrics, reasoning = metrics or {}, reasoning or {}
        from datetime import datetime
        try:
            duration = max(0.0, (datetime.fromisoformat(now()) - datetime.fromisoformat(started_at)).total_seconds())
        except (TypeError, ValueError):
            duration = 0.0
        corrections = reasoning.get('all_corrections') or reasoning.get('corrections') or []
        lowered = reasoning.get('all_effort_lowered') or reasoning.get('effort_lowered') or []
        found = lessons_from(agent_id, status, metrics, reasoning, outputs, error)
        with repo.connect() as c:
            ensure(c)
            c.execute('insert or replace into run_metrics values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (run_id, job_id, agent_id, agent_version, status, duration,
                       int(reasoning.get('attempts_total') or reasoning.get('attempts') or 0),
                       len(corrections), len(lowered), int(metrics.get('gap_count') or len(metrics.get('unfixed_gaps') or [])),
                       len(metrics.get('failed_checks') or []), int(clarifications or 0), str(error or '')[:300], now()))
            for lesson in found:
                _upsert(c, lesson, job_id)
        return len(found)
    except Exception as exc:                       # noqa: BLE001 -- see the module docstring
        sys.stderr.write('learning: could not record run %s (%s)\n' % (run_id, exc))
        return 0


def overview(repo):
    """The learning page: totals, per-agent trend inputs, and the lessons by recurrence."""
    with repo.connect() as c:
        ensure(c)
    metrics = repo.rows('select agent_id, count(*) runs, '
                        "sum(case when status='COMPLETED' then 1 else 0 end) completed, "
                        'round(avg(duration_seconds),1) avg_seconds, round(avg(attempts),2) avg_attempts, '
                        'sum(corrections) corrections, sum(effort_step_downs) step_downs, '
                        'round(avg(gap_count),2) avg_gaps, round(avg(clarifications),1) avg_clarifications '
                        'from run_metrics group by agent_id order by agent_id')
    lessons = repo.rows('select * from lessons order by occurrences desc, last_seen desc limit 200')
    for lesson in lessons:
        lesson['jobs'] = len(json.loads(lesson.get('jobs') or '[]'))
        lesson.pop('evidence', None)
    return {'summary': {'runs': sum(m['runs'] for m in metrics), 'lessons': len(lessons),
                        'recurring': sum(1 for x in lessons if x['jobs'] >= 2)},
            'agents': metrics, 'lessons': lessons}
