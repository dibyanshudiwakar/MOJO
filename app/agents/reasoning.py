"""Governed model access for agents that reason rather than only compute.

Every model-assisted step goes through ``AIRegistryService.gateway_request``,
which is the only place that enforces an approved prompt version, an approved
model version, an approved ``model-gateway`` connector profile, the data-loss
rules and the region policy. Agents never hold a provider credential or a raw
endpoint; they hold a prompt *name*.

The important property of this module is what happens when the gateway cannot
answer. ``reason`` returns ``(None, provenance)`` with a ``reason`` naming the
blocker; it never raises for a governed outcome. What the *caller* does with
that has changed: ``app.agents.artefact.produce`` used to compute the artefact
instead, and now retries or fails the agent. So these are no longer degradations
with a working alternative behind them -- each one stops a migration, and the
reason string is the only thing that explains why:

* ``no_approved_prompt_or_model`` -- the catalogue in
  ``config/agent-prompts.v1.json`` is registered and approved by an
  ``AI_GOVERNANCE_ADMIN`` through the governed endpoint, never self-approved
  here. ``Repository._seed_model_gateway`` now does that on a fresh database so
  this is not the out-of-the-box state.
* ``no_provider_execution_adapter`` -- no provider is configured.
  ``gateway_request`` records ``PROVIDER_EXECUTION_REQUIRED`` and returns;
  connectors are deliberately boundaries rather than simulations, so a request
  alone yields no completion.
* ``gateway_refused`` -- the data-loss rules refuse a great deal of legitimate
  migration content. The ``PAN`` rule matches any run of 13 or more digits,
  which an ACE semantic model trips routinely. ``artefact.scrub`` exists to keep
  that from ending runs.
* ``provider_execution_failed`` -- a network error, an expired token, a
  truncated answer. The one group worth another attempt, which is why
  ``artefact.RETRYABLE`` names it.

``mode`` is recorded on the run and in the evidence metadata either way, so an
auditor can see which model and prompt version shaped a stored artefact, and
which runs never reached one.
"""
import json
import re
from pathlib import Path
from app.services.platform_v7 import AIRegistryService
from app.agents.skills import skill_for, SkillIntegrityError

CATALOGUE = Path(__file__).resolve().parents[2] / 'config' / 'agent-prompts.v1.json'

MODEL_GATEWAY = 'MODEL_GATEWAY'
#: The gateway could not answer. Named for what happened rather than for what
#: used to happen next: there is no deterministic fallback any more, so a run
#: carrying this mode produced no artefact. ``reason`` says which blocker fired.
UNAVAILABLE = 'MODEL_UNAVAILABLE'
#: The model was never asked, legitimately -- an operator supplied the artefact
#: outright, or there was nothing to ask about (an empty plan has no files to
#: list). Distinct from UNAVAILABLE: nothing is wrong, and distinct from
#: MODEL_GATEWAY: the artefact is not model-authored.
NOT_CONSULTED = 'MODEL_NOT_CONSULTED'


def version_key(version):
    """A sortable key for a version string: ``'v1'``, ``'1'`` and ``'2.10'`` all work.

    Numbers compare as numbers, so 10 outranks 9, and a leading ``v`` is
    ignored, so a legacy ``'v1'`` ties with ``'1'`` instead of beating ``'4'``.
    A version with no digits sorts lowest.
    """
    return tuple(int(n) for n in re.findall(r'\d+', str(version or ''))) or (-1,)


def prompt_catalogue():
    """The version-controlled prompt templates agents reason with."""
    return json.loads(CATALOGUE.read_text(encoding='utf-8'))['prompts']


class ProviderExecutionAdapter:
    """The seam a real model provider is plugged into.

    Implementations receive the approved template and the payload the gateway
    already cleared, and return a JSON-serialisable answer. The factory ships no
    implementation: an external provider call is an enterprise adapter, and this
    codebase does not simulate those. Tests supply a deterministic double.

    ``skill`` is the optional per-agent guidance loaded from ``agent-skills/``.
    Adapters that support it (``CvscodeClaudeAdapter``) append it to the system
    prompt; adapters that do not accept it (test doubles) can ignore it.
    """

    #: Set True on an adapter whose ``invoke`` accepts a ``skill=`` keyword. The
    #: skill is passed only to adapters that declare this, so adapters written
    #: against ``invoke(template, payload)`` keep working unchanged.
    accepts_skill = False

    def invoke(self, template, payload, skill=None):
        raise NotImplementedError('provider_execution_adapter_required')


class ReasoningGateway:
    """Resolve an approved prompt/model pair and, if possible, get an answer."""

    def __init__(self, repo):
        self.repo = repo
        self.registry = AIRegistryService(repo)

    def _approved(self, prompt_name, model_alias=None):
        # The newest approved version, compared as numbers. This was
        # ``order by version desc``, which compares text: a legacy seed row
        # versioned 'v1' sorted above '2', '3' and '4', so for 32 prompts every
        # later version was registered, approved and never used -- planning ran
        # on its original prompt through four revisions of it, and '10' would
        # have lost to '9' the same way.
        prompt = max(
            self.repo.rows("select * from prompt_registry where name=? and status='APPROVED'",
                           (prompt_name,)),
            key=lambda row: (version_key(row['version']), row['created_at'] or ''),
            default=None)
        if model_alias:
            model = self.repo.one(
                "select * from model_registry where name=? and status='APPROVED'"
                ' order by created_at desc limit 1', (model_alias,))
        else:
            model = self.repo.one(
                "select * from model_registry where status='APPROVED'"
                ' order by created_at desc limit 1')
        return prompt, model

    def reason(self, context, prompt_name, payload, skill=None, effort=None):
        """Return ``(answer_or_None, provenance)``.

        ``skill`` overrides the agent's own skill for this one call (see
        ``Skill.focus``); without it the agent's skill from ``context`` is used.

        ``provenance`` always records which mode produced the agent's result and,
        when a request was made, the prompt and model versions it was made
        against -- so a stored artefact can be traced to the exact template that
        shaped it rather than to "some model, at some point".
        """
        _log = getattr(context, 'logger', None)
        _log_info = _log.info if _log else (lambda m: None)
        _log_warn = _log.warn if _log else (lambda m: None)

        alias = getattr(context, 'effective_model_alias', '') or None
        provenance = {'mode': UNAVAILABLE, 'prompt_name': prompt_name,
                      'model_alias': alias or '', 'binding_source': getattr(context, 'binding_source', 'DEFAULT')}
        _log_info('LLM reasoning: looking up prompt "%s" with model alias "%s"' % (prompt_name, alias or '(default)'))
        # Which skill (by content hash) was available to this step, in every mode.
        # Guidance only: a damaged skill is recorded here and the step carries on.
        skill_provenance = getattr(context, 'skill_provenance', None)
        if skill is not None:
            provenance.update(skill.provenance())
        elif skill_provenance:
            provenance.update(skill_provenance())
        prompt, model = self._approved(prompt_name, alias)
        if not prompt or not model:
            provenance['reason'] = 'no_approved_prompt_or_model'
            missing = []
            if not prompt:
                missing.append('prompt')
            if not model:
                missing.append('model')
            _log_warn('LLM reasoning: no approved %s found — no artefact can be produced' % ' or '.join(missing))
            return None, provenance
        _log_info('LLM prompt resolved: "%s" v%s (hash: %s)' % (prompt['name'], prompt['version'], prompt['content_hash'][:12]))
        _log_info('LLM model resolved: "%s" v%s (id: %s)' % (model['name'], model['version'], model['id'][:12]))
        try:
            request = self.registry.gateway_request({
                'job_id': context.job_id, 'prompt_id': prompt['id'], 'model_id': model['id'],
                'input_evidence': payload, 'correlation_id': context.correlation_id or None})
        except (PermissionError, ValueError) as exc:
            # A refusal is a governed outcome rather than a crash, so it is
            # returned rather than raised -- but it is terminal for the
            # artefact: the same payload trips the same rule on every attempt,
            # so artefact.TERMINAL names it and the agent fails here rather
            # than three model calls later.
            provenance['mode'] = UNAVAILABLE
            provenance['reason'] = 'gateway_refused:' + str(exc)
            _log_warn('LLM gateway refused request: %s — no artefact can be produced' % str(exc)[:200])
            return None, provenance
        _log_info('LLM gateway request accepted: %s (status: %s)' % (request['request_id'][:12], request['status']))
        provenance.update({'request_id': request['request_id'], 'prompt_id': prompt['id'],
                           'prompt_version': prompt['version'], 'prompt_hash': prompt['content_hash'],
                           'model_id': model['id'], 'model_version': model['version'],
                           'region': request.get('region', ''), 'status': request['status']})
        if context.provider is None:
            provenance['reason'] = 'no_provider_execution_adapter'
            _log_warn('LLM reasoning: no provider execution adapter configured — no artefact can be produced')
            return None, provenance
        # An adapter that serves more than one model exposes ``for_alias`` and is
        # pinned to the alias the registry resolved for this agent, so the
        # ``reasoning-small`` agents do not quietly borrow the large model. The
        # seam itself is unchanged -- an adapter without the hook (every test
        # double) is used as-is.
        provider = context.provider
        selector = getattr(provider, 'for_alias', None)
        if alias and callable(selector):
            try:
                provider = selector(alias)
            except (RuntimeError, ValueError) as exc:
                provenance['reason'] = 'provider_alias_unavailable:' + str(exc)
                _log_warn('LLM provider alias "%s" unavailable: %s' % (alias, str(exc)[:200]))
                try:
                    self.registry.complete_request(request['request_id'], {}, 'FAILED')
                except (PermissionError, ValueError):
                    pass
                return None, provenance
        provider_model = getattr(provider, 'model', '')
        provenance['provider_model'] = provider_model
        # Fall back to the agent's own skill only when the caller did not supply
        # one. A caller-supplied skill is deliberately narrower -- variance
        # analysis passes ``own.focus('parity-skill.md')`` so a parity call is
        # guided by that reference alone rather than the whole SKILL.md -- and
        # overwriting it here discarded that focus entirely.
        #
        # A damaged or missing skill is recorded and skipped: it is guidance,
        # not authority, so its absence must not prevent the agent from running.
        ctx_agent_id = getattr(context, 'agent_id', '') or ''
        if skill is None and ctx_agent_id:
            try:
                skill = skill_for(ctx_agent_id)
                if skill is None:
                    _log_info('No skill defined for agent "%s" — invoking without skill guidance' % ctx_agent_id)
            except SkillIntegrityError as exc:
                _log_warn('Skill integrity error for "%s": %s — invoking without skill guidance' % (ctx_agent_id, exc))
                provenance['skill_error'] = str(exc)
        # Recorded for whichever skill is actually in play, caller-supplied or
        # not. ``skill_hash`` is what gates passing it to the adapter below, so
        # setting it only on the unavailable path meant a focused skill was loaded
        # and then never handed over. Skill.provenance() also carries
        # ``skill_focus`` when the skill is narrowed to one reference.
        if skill is not None:
            provenance.update(skill.provenance())
            _log_info('Skill in use: "%s" (hash: %s, %d references)%s' % (
                skill.name, skill.content_hash[:12], len(skill.references),
                ', focused on ' + skill.focused_on if getattr(skill, 'focused_on', '') else ''))
        # Enrich the payload with accumulated pipeline context so the LLM sees
        # what every prior agent produced, which skills guided them, and the
        # full evidence chain.  This is the "zero context loss" mechanism: the
        # model receives the entire pipeline's accumulated knowledge alongside
        # the agent's own payload.
        enriched_payload = dict(payload) if isinstance(payload, dict) else payload
        prior = getattr(context, 'pipeline_context', None)
        if prior and isinstance(enriched_payload, dict):
            # Compact summary: agent chain, skill summaries, evidence summary.
            # Full per-agent outputs are large; include only the summary and
            # let the model ask for specifics via the agent's own service calls.
            enriched_payload['_prior_agents'] = {
                'agent_chain': prior.get('agent_chain', []),
                'skills_guidance': prior.get('skills_summary', ''),
                'evidence_produced': [{'kind': e.get('kind'), 'id': e.get('id')[:12] if e.get('id') else ''}
                                      for e in prior.get('evidence_summary', [])],
                'agent_summaries': {
                    aid: {
                        'status': info.get('status', ''),
                        'skill': info.get('skill_name', ''),
                        'metrics': info.get('metrics', {}),
                        'reasoning_mode': (info.get('reasoning') or {}).get('mode', ''),
                        'outputs': info.get('outputs', {}),
                    }
                    for aid, info in (prior.get('agents') or {}).items()
                },
            }
            _log_info('LLM payload enriched with pipeline context: %d prior agents' % len(prior.get('agent_chain', [])))
        _log_info('LLM invoking provider: model=%s, prompt="%s", skill=%s, payload_keys=%s' % (
            provider_model, prompt_name, skill.name if skill else '(none)',
            sorted(enriched_payload.keys()) if isinstance(enriched_payload, dict) else '(non-dict)'))
        try:
            # The skill travels outside ``payload`` on purpose: the payload is the
            # job's evidence and was DLP-cleared by the gateway; the skill is
            # static, reviewed repository content. The adapter appends it to the
            # approved template; it never replaces it.
            guidance = None
            if getattr(provider, 'accepts_skill', False) and 'skill_hash' in provenance:
                guidance = skill if skill is not None else context.skill()
            # Reasoning effort for this one call, only to an adapter that says it
            # takes one -- the same arrangement as ``skill`` and for the same
            # reason: a provider written against invoke(template, payload) must
            # keep working.
            options = {}
            if effort and getattr(provider, 'accepts_effort', False):
                options['effort'] = effort
                provenance['effort'] = effort
            if guidance is not None:
                answer = provider.invoke(prompt['template'], enriched_payload, skill=guidance, **options)
            else:
                # No skill keyword at all. An adapter leaving accepts_skill False
                # is declaring the two-argument signature, so passing skill= here
                # raised TypeError and took down every provider double written
                # against invoke(template, payload).
                answer = provider.invoke(prompt['template'], enriched_payload, **options)
            provenance['skill_included'] = guidance is not None
            recorded = self.registry.complete_request(request['request_id'], answer)
        except (PermissionError, ValueError, NotImplementedError, RuntimeError) as exc:
            # RuntimeError is what every shipped adapter raises: CvscodeClaudeAdapter
            # wraps an SDK or network failure as provider_error:<type>:<detail>, a
            # gateway refusal as provider_refused, and an unavailable key as
            # cvscode_api_key_unavailable. Omitting it here meant an expired token
            # took the whole agent down with a 401 instead of degrading -- the
            # opposite of this module's contract, and observed in practice:
            # governance failed mid-pipeline on
            # "provider_error:AuthenticationError:Error code: 401".
            #
            # Deliberately not bare ``except``: a KeyboardInterrupt or a
            # MemoryError is not a degradation, and a programming error in an
            # adapter should still surface rather than be reported as a model outage.
            provenance['reason'] = 'provider_execution_failed:' + str(exc)
            _log_warn('LLM provider execution failed: %s' % str(exc)[:200])
            try:
                self.registry.complete_request(request['request_id'], {}, 'FAILED')
            except (PermissionError, ValueError):
                pass
            return None, provenance
        provenance.update({'mode': MODEL_GATEWAY, 'status': recorded['status'],
                           'response_hash': recorded['response_hash']})
        _log_info('LLM response received: status=%s, response_hash=%s, mode=%s' % (
            recorded['status'], recorded['response_hash'][:12] if recorded.get('response_hash') else '?', MODEL_GATEWAY))
        return answer, provenance
