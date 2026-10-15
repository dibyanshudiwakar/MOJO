"""Governed model access for agents that reason rather than only compute.

Every model-assisted step goes through ``AIRegistryService.gateway_request``,
which is the only place that enforces an approved prompt version, an approved
model version, an approved ``model-gateway`` connector profile, the data-loss
rules and the region policy. Agents never hold a provider credential or a raw
endpoint; they hold a prompt *name*.

The important property of this module is what happens when the gateway cannot
answer -- which, in this system, is the normal case:

* No provider execution exists anywhere in the factory. ``gateway_request``
  records ``PROVIDER_EXECUTION_REQUIRED`` and returns. Connectors are
  deliberately boundaries rather than simulations, so a request alone yields no
  completion.
* Out of the box there are no APPROVED prompts. The catalogue in
  ``config/agent-prompts.v1.json`` is registered and approved by an
  ``AI_GOVERNANCE_ADMIN`` through the existing governed endpoint, never
  self-approved here.
* The data-loss rules refuse a great deal of legitimate migration content. The
  ``PAN`` rule matches any run of 13 or more digits, which an ACE semantic model
  or a differential payload trips routinely.

So the deterministic, service-backed computation is the agent's answer unless a
provider is configured *and* the gateway permits the request. That is a
fallback, not a silent one: ``mode`` is recorded on the run and in the evidence
metadata, so an auditor can tell a model-assisted result from a computed one.
Refusing to fall back would mean 14 agents that cannot complete.
"""
import json
from pathlib import Path
from app.services.platform_v7 import AIRegistryService

CATALOGUE = Path(__file__).resolve().parents[2] / 'config' / 'agent-prompts.v1.json'

MODEL_GATEWAY = 'MODEL_GATEWAY'
DETERMINISTIC = 'DETERMINISTIC_FALLBACK'


def prompt_catalogue():
    """The version-controlled prompt templates agents reason with."""
    return json.loads(CATALOGUE.read_text(encoding='utf-8'))['prompts']


class ProviderExecutionAdapter:
    """The seam a real model provider is plugged into.

    Implementations receive the approved template and the payload the gateway
    already cleared, and return a JSON-serialisable answer. The factory ships no
    implementation: an external provider call is an enterprise adapter, and this
    codebase does not simulate those. Tests supply a deterministic double.
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
        prompt = self.repo.one(
            "select * from prompt_registry where name=? and status='APPROVED'"
            ' order by version desc limit 1', (prompt_name,))
        if model_alias:
            model = self.repo.one(
                "select * from model_registry where name=? and status='APPROVED'"
                ' order by created_at desc limit 1', (model_alias,))
        else:
            model = self.repo.one(
                "select * from model_registry where status='APPROVED'"
                ' order by created_at desc limit 1')
        return prompt, model

    def reason(self, context, prompt_name, payload, skill=None):
        """Return ``(answer_or_None, provenance)``.

        ``skill`` overrides the agent's own skill for this one call (see
        ``Skill.focus``); without it the agent's skill from ``context`` is used.

        ``provenance`` always records which mode produced the agent's result and,
        when a request was made, the prompt and model versions it was made
        against -- so a stored artefact can be traced to the exact template that
        shaped it rather than to "some model, at some point".
        """
        alias = getattr(context, 'effective_model_alias', '') or None
        provenance = {'mode': DETERMINISTIC, 'prompt_name': prompt_name,
                      'model_alias': alias or '', 'binding_source': getattr(context, 'binding_source', 'DEFAULT')}
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
            return None, provenance
        try:
            request = self.registry.gateway_request({
                'job_id': context.job_id, 'prompt_id': prompt['id'], 'model_id': model['id'],
                'input_evidence': payload, 'correlation_id': context.correlation_id or None})
        except (PermissionError, ValueError) as exc:
            # A refusal is a governed outcome, not an agent failure: the request
            # was recorded or rejected by policy and the agent computes instead.
            provenance['mode'] = DETERMINISTIC
            provenance['reason'] = 'gateway_refused:' + str(exc)
            return None, provenance
        provenance.update({'request_id': request['request_id'], 'prompt_id': prompt['id'],
                           'prompt_version': prompt['version'], 'prompt_hash': prompt['content_hash'],
                           'model_id': model['id'], 'model_version': model['version'],
                           'region': request.get('region', ''), 'status': request['status']})
        if context.provider is None:
            provenance['reason'] = 'no_provider_execution_adapter'
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
                try:
                    self.registry.complete_request(request['request_id'], {}, 'FAILED')
                except (PermissionError, ValueError):
                    pass
                return None, provenance
        provenance['provider_model'] = getattr(provider, 'model', '')
        try:
            # The skill travels outside ``payload`` on purpose: the payload is the
            # job's evidence and was DLP-cleared by the gateway; the skill is
            # static, reviewed repository content. The adapter appends it to the
            # approved template; it never replaces it.
            guidance = None
            if getattr(provider, 'accepts_skill', False) and 'skill_hash' in provenance:
                guidance = skill if skill is not None else context.skill()
            if guidance is not None:
                answer = provider.invoke(prompt['template'], payload, skill=guidance)
            else:
                answer = provider.invoke(prompt['template'], payload)
            provenance['skill_included'] = guidance is not None
            recorded = self.registry.complete_request(request['request_id'], answer)
        except (PermissionError, ValueError, NotImplementedError) as exc:
            provenance['reason'] = 'provider_execution_failed:' + str(exc)
            try:
                self.registry.complete_request(request['request_id'], {}, 'FAILED')
            except (PermissionError, ValueError):
                pass
            return None, provenance
        provenance.update({'mode': MODEL_GATEWAY, 'status': recorded['status'],
                           'response_hash': recorded['response_hash']})
        return answer, provenance
