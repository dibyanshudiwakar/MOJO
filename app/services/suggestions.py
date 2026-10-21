"""Suggested answers for clarifications that were raised without one.

A run raises its questions and persists them first, then asks the model for a draft
answer to each in batches. If the run is stopped, or the model is unavailable, or a
batch comes back malformed, the questions are already saved and the suggestions are
simply never made -- and nothing comes back for them. Two hundred open questions
sat without a suggestion for exactly that reason.

This asks again for the ones still missing, grouped by the agent that raised them
(its model alias, its earlier decisions and its skill shape the answer, as they did
the first time). It only *suggests*: it records the draft with the model's
confidence and caveat and answers nothing, because the run that would have acted on
an auto-mode decision is long over.
"""
from app.agents import load as load_implementation
from app.agents.artefact import ArtefactUnavailable, STRUCTURED_EFFORT, produce, run_parallel
from app.providers import default_provider
from app.repository import now, uid

PROMPT = 'agent/clarification/suggested-answers'
#: The same bound the run itself uses: the answer grows with the number of
#: questions while max_tokens does not.
BATCH = 12


def suggest_missing(registry, job_id, identity='system', roles=(), provider=None):
    """Ask for a suggestion for every OPEN clarification of ``job_id`` that has none."""
    repo = registry.repo
    rows = repo.rows(
        "select c.id, c.question, c.impact, c.stage, r.agent_id, r.agent_version "
        "from clarifications c join agent_runs r on r.id=c.run_id "
        "where c.job_id=? and c.status='OPEN' and trim(coalesce(c.suggested_answer,''))='' "
        "order by c.created_at", (job_id,))
    by_agent = {}
    for row in rows:
        by_agent.setdefault((row['agent_id'], row['agent_version']), []).append(row)
    result = {'requested': len(rows), 'suggested': 0, 'errors': []}
    if not rows:
        return result
    provider = provider or default_provider()
    job = repo.one('select iteration from jobs where id=?', (job_id,)) or {}
    for (agent_id, version), items in by_agent.items():
        try:
            contract = registry.contract(agent_id, version)
            default_alias = contract.get('default_model_alias') or ''
            alias, source = registry.bindings.resolve(agent_id, default_alias)
            context = load_implementation(agent_id)(repo).context(
                job_id, identity=identity, roles=tuple(roles or ()), params={}, correlation_id=uid(),
                iteration=int(job.get('iteration') or 1), provider=provider, channel='',
                default_model_alias=default_alias, effective_model_alias=alias, binding_source=source,
                logger=None, agent_id=agent_id, cancelled=None)
        except Exception as exc:                       # noqa: BLE001 -- reported, not raised
            result['errors'].append('%s: %s' % (agent_id, exc))
            continue
        batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]

        def ask(batch, context=context):
            payload = {'questions': [{'question': r['question'], 'impact': r['impact'], 'stage': r['stage']}
                                     for r in batch]}
            try:
                answer, provenance = produce(context, PROMPT, payload, effort=STRUCTURED_EFFORT)
                return batch, answer, provenance, None
            except ArtefactUnavailable as exc:
                return batch, None, None, exc

        for batch, answer, provenance, error in run_parallel(
                context, PROMPT, [(lambda b=b: ask(b)) for b in batches]):
            if error is not None:
                result['errors'].append('%s: %s' % (agent_id, error.reason))
                continue
            batch_suggs = [s for s in (answer.get('suggestions') or []) if isinstance(s, dict)]
            by_question = {str(s.get('question') or '').strip().lower(): s for s in batch_suggs}
            by_ordinal = dict(enumerate(batch_suggs)) if len(batch_suggs) == len(batch) else {}
            with repo.connect() as c:
                for idx, row in enumerate(batch):
                    s = by_question.get(str(row['question'] or '').strip().lower())
                    if s is None:
                        s = by_ordinal.get(idx)
                    if not s or not str(s.get('suggested_answer') or '').strip():
                        continue
                    c.execute("update clarifications set suggested_answer=?, suggestion_mode=?, "
                              "suggestion_confidence=?, suggestion_severity=?, suggestion_caveat=?, suggested_at=? "
                              "where id=? and status='OPEN' and trim(coalesce(suggested_answer,''))=''",
                              (s['suggested_answer'], provenance.get('mode', ''), s.get('confidence', ''),
                               s.get('severity', ''), s.get('caveat', ''), now(), row['id']))
                    result['suggested'] += 1
    return result
