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

Lessons start ``CAPTURED`` and are inert. A reviewer (``KNOWLEDGE_CURATOR`` or
``GOVERNANCE_ADMIN``) approves or rejects each one, and only ``APPROVED`` lessons
are read back into an agent's requests -- putting unreviewed text into prompts
would let one bad run teach every later run the wrong thing. What is shown to the
model is the reviewer's *guidance* text, which they may rewrite before approving,
not the raw record. Recording and retrieval must never fail a run, so both swallow
their own errors.
"""
import os
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


#: Columns added after the table first shipped; added in place on an existing database.
_LATER_COLUMNS = {'guidance': "text default ''", 'applied': 'integer default 0',
                  'reviewed_by': "text default ''", 'reviewed_at': "text default ''", 'note': "text default ''"}


def ensure(conn):
    for statement in [s for s in SCHEMA.split(';') if s.strip()]:
        conn.execute(statement)
    have = {r[1] for r in conn.execute('pragma table_info(lessons)').fetchall()}
    for column, kind in _LATER_COLUMNS.items():
        if column not in have:
            conn.execute('alter table lessons add column %s %s' % (column, kind))


def normalise(text):
    """A stable key for a situation: no ids, numbers or quoting that vary run to run."""
    text = re.sub(r"['\"`]", '', str(text or '').lower())
    text = re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27}', '#', text)
    text = re.sub(r'\d+', '#', text)
    return re.sub(r'\s+', ' ', text).strip()[:160]


def _signature(kind, signature):
    """Check names are identifiers, not prose: ``SKILL_M5_M16`` and ``SKILL_M7_M10`` differ
    only in digits, which ``normalise`` would erase and merge into one lesson."""
    signature = str(signature or '')
    if kind in ('GATE_GAP', 'GATE_FAIL', 'GAP_UNREPAIRED'):
        return signature.strip().lower()[:160]
    if kind == 'REPAIR':
        check, _, action = signature.partition('|')
        return (check.strip().lower() + '|' + normalise(action))[:160]
    return normalise(signature)


def _lesson(agent_id, kind, signature, situation, resolution, evidence=None):
    return {'agent_id': agent_id, 'kind': kind, 'signature': _signature(kind, signature),
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
            for lesson_id in (reasoning.get('lessons_used') or [])[:MAX_LESSONS]:
                c.execute('update lessons set applied=coalesce(applied,0)+1 where id=?', (lesson_id,))
        return len(found)
    except Exception as exc:                       # noqa: BLE001 -- see the module docstring
        sys.stderr.write('learning: could not record run %s (%s)\n' % (run_id, exc))
        return 0


# ---- review and retrieval ------------------------------------------------------------------

#: Lessons worth putting in front of a model. Effort step-downs and run failures
#: describe the platform, not what a prompt should say.
INJECTED_KINDS = ('SCHEMA_CORRECTION', 'GATE_GAP', 'GATE_FAIL', 'REPAIR', 'GAP_UNREPAIRED')
DECISIONS = {'APPROVE': APPROVED, 'REJECT': REJECTED, 'RETRACT': REJECTED}
MAX_LESSONS = 8
MAX_CHARS = 2400
MAX_GUIDANCE = 300

#: Said to the model alongside the guidance. Like the precedents note it lives in
#: the payload, not the approved template, and it ranks below the instructions.
NOTE = ('Guidance from earlier runs of this agent, reviewed and approved by a person. '
        'Apply it where it concerns this request; the instructions and schema above take '
        'precedence, and it never lets you skip a required field.')


def enabled():
    """On unless ``FACTORY_LESSONS=0``."""
    return (os.getenv('FACTORY_LESSONS', '1') or '').strip().lower() not in ('0', 'false', 'no', 'off')


def default_guidance(lesson):
    """What a reviewer starts from: the lesson, as an instruction."""
    kind, resolution = lesson.get('kind'), (lesson.get('resolution') or '').strip()
    situation = (lesson.get('situation') or '').strip()
    if kind == 'SCHEMA_CORRECTION':
        return 'Earlier answers were rejected for this and had to be redone: %s' % situation.split(': ', 1)[-1]
    if kind in ('GATE_GAP', 'GAP_UNREPAIRED'):
        return 'The quality gate flagged this in earlier output; get it right the first time. %s %s' % (
            situation, resolution)
    if kind == 'REPAIR':
        return 'Earlier output needed this repair afterwards; generate it correctly instead: %s' % resolution
    return '%s %s' % (situation, resolution)


def decide(repo, lesson_id, decision, actor, guidance='', note=''):
    """Approve, reject or retract one lesson. Returns the updated row."""
    decision = str(decision or '').strip().upper()
    if decision not in DECISIONS:
        raise ValueError('decision_must_be_APPROVE_REJECT_or_RETRACT')
    with repo.connect() as c:
        ensure(c)
    lesson = repo.one('select * from lessons where id=?', (lesson_id,))
    if not lesson:
        raise ValueError('lesson_not_found')
    status = DECISIONS[decision]
    text = str(guidance or '').strip()[:MAX_GUIDANCE]
    if status == APPROVED:
        if lesson['kind'] not in INJECTED_KINDS:
            raise ValueError('this_kind_of_lesson_describes_the_platform_not_a_prompt')
        text = text or default_guidance(lesson)[:MAX_GUIDANCE]
    with repo.connect() as c:
        c.execute('update lessons set status=?, guidance=?, reviewed_by=?, reviewed_at=?, note=? where id=?',
                  (status, text if status == APPROVED else (lesson.get('guidance') or ''),
                   actor, now(), str(note or '')[:300], lesson_id))
    return repo.one('select * from lessons where id=?', (lesson_id,))


def approved_for(repo, agent_id):
    """The approved guidance for one agent, most-recurring first, within a fixed budget."""
    if not agent_id:
        return []
    with repo.connect() as c:
        ensure(c)
    rows = repo.rows("select id, kind, guidance, occurrences from lessons where agent_id=? and status=? "
                     "and trim(guidance)<>'' order by occurrences desc, last_seen desc", (agent_id, APPROVED))
    chosen, size = [], 0
    for row in rows:
        if len(chosen) >= MAX_LESSONS or size + len(row['guidance']) > MAX_CHARS:
            break
        chosen.append({'id': row['id'], 'kind': row['kind'], 'guidance': row['guidance']})
        size += len(row['guidance'])
    return chosen


def for_context(context):
    """The approved guidance for a running agent, looked up once per run."""
    if not enabled():
        return []
    cached = getattr(context, '_lessons', None)
    if cached is not None:
        return cached
    try:
        found = approved_for(context.repo, getattr(context, 'agent_id', ''))
    except Exception:                              # noqa: BLE001 -- a convenience; never fail the agent
        found = []
    context._lessons = found
    logger = getattr(context, 'logger', None)
    if found and logger:
        logger.info('Using %d approved lesson(s) from earlier runs as guidance' % len(found))
    return found


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
