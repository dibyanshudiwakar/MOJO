"""What people have already decided, handed back to the agent that asked.

``clarification_reuse`` answers a *repeat* of a question. This is the broader
half: every answer a person has given to a question an agent raised becomes
context for that agent's later work, on any job. The agent that was told the
context root is ``/pbmmember_14/...`` on one run should not have to ask again,
and should not author the next artefact as if it had never been told.

Rules that keep this safe to feed a model:

* **Decisions by people only.** An answer auto mode recorded is the model's own
  guess, and returning guesses to the model as if they were rulings would
  reinforce them. Reused answers are excluded for the same reason; the person's
  original is in the set already.
* **Only the agent that raised the question.** An ace-analysis answer is context
  for ace-analysis. It is looked up through the run that raised the question.
* **Nearest first, and labelled.** This job, then other jobs of the same
  service, then other services. Every entry says which, and names its service,
  so the model can weigh a decision about a different service accordingly
  rather than being told it applies.
* **Bounded.** A fixed budget of entries and characters, so a long history
  cannot crowd out the input the request is actually about.

It is advice to the model, not an answer: nothing here is written to a
clarification, and the gates are untouched.
"""
import os

from app.services import automode
from app.services.clarification_reuse import ACTOR as REUSED_ACTOR, normalise

#: At most this many decisions, and this many characters of them, per request.
MAX_ENTRIES = 40
MAX_CHARS = 8000
#: Candidates read from the database, newest first. A bound, not a policy.
LIMIT = 1000

#: Said to the model alongside the decisions. It lives in the payload, next to
#: ``_correction`` and ``_prior_agents``, rather than in the approved prompt
#: templates: it applies to every prompt and the templates are governed text.
NOTE = ('Decisions a person made earlier on questions this agent raised, nearest first. '
        'Treat one as settled where it concerns the same construct or context as this request, '
        'and do not raise a clarification for something it already answers. Entries marked '
        '"other service" are about a different service: use them as precedent only where the '
        'same reasoning clearly applies. Each entry has a ref (D1, D2, ...) that can be cited.')

HUMAN_ONLY = (automode.ACTOR, REUSED_ACTOR, '')


def enabled():
    """On unless ``FACTORY_PRECEDENTS=0``."""
    return (os.getenv('FACTORY_PRECEDENTS', '1') or '').strip().lower() not in ('0', 'false', 'no', 'off')


def decisions_for(repo, job_id, agent_id):
    """The earlier human decisions relevant to ``agent_id`` on ``job_id``, best first."""
    if not agent_id:
        return []
    job = repo.one('select service from jobs where id=?', (job_id,)) or {}
    service = job.get('service') or ''
    rows = repo.rows(
        "select c.id,c.question,c.answer,c.stage,c.answered_by,c.answered_at,c.job_id,j.service"
        " from clarifications c"
        " join agent_runs r on r.id=c.run_id"
        " join jobs j on j.id=c.job_id"
        " where r.agent_id=? and c.status='ANSWERED' and trim(c.answer)<>''"
        " order by c.answered_at desc limit ?", (agent_id, LIMIT))

    def scope(row):
        if row['job_id'] == job_id:
            return 0, 'this job'
        if service and row['service'] == service:
            return 1, 'same service'
        return 2, 'other service'

    ranked = sorted(((scope(r), r) for r in rows
                     if str(r.get('answered_by') or '') not in HUMAN_ONLY),
                    key=lambda pair: pair[0][0])
    chosen, seen, size = [], set(), 0
    for (_, label), row in ranked:
        key = normalise(row['question'])
        if key in seen:
            continue
        entry = {'ref': 'D%d' % (len(chosen) + 1), 'question': row['question'],
                 'answer': row['answer'], 'scope': label,
                 'service': row['service'] or '', 'stage': row['stage'], 'id': row['id']}
        cost = len(entry['question']) + len(entry['answer']) + 60
        if len(chosen) >= MAX_ENTRIES or size + cost > MAX_CHARS:
            break
        seen.add(key)
        chosen.append(entry)
        size += cost
    return chosen


def for_context(context):
    """The decisions for a running agent, looked up once per run.

    ``produce_many`` makes one request per source file and ace-analysis can make
    seventeen, so the lookup is cached on the context rather than repeated.
    """
    if not enabled():
        return []
    cached = getattr(context, '_precedents', None)
    if cached is not None:
        return cached
    try:
        full = decisions_for(context.repo, context.job_id, getattr(context, 'agent_id', ''))
    except Exception:
        # Context is a convenience. A failure to read it must not fail the
        # agent, only run it without.
        full = []
    # What the model sees has no database ids; the ids stay here so a decision
    # it cites by ref can be traced back to the clarification it came from.
    found = [{k: v for k, v in d.items() if k != 'id'} for d in full]
    context._precedents = found
    context._precedent_refs = {d['ref']: {'id': d['id'], 'answer': d['answer']} for d in full}
    logger = getattr(context, 'logger', None)
    if found and logger:
        logger.info('Using %d earlier decision(s) as context' % len(found))
    return found


def settled_by(context, ref):
    """The earlier decision a model-cited ``ref`` points at, or ``None``.

    Only refs this run was actually shown resolve, so a made-up one settles
    nothing.
    """
    return (getattr(context, '_precedent_refs', None) or {}).get(str(ref or '').strip())
