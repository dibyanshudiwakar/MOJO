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

    def invoke(self, template, payload):
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

    def reason(self, context, prompt_name, payload):
        """Return ``(answer_or_None, provenance)``.

        ``provenance`` always records which mode produced the agent's result and,
        when a request was made, the prompt and model versions it was made
        against -- so a stored artefact can be traced to the exact template that
        shaped it rather than to "some model, at some point".
        """
        _log = getattr(context, 'logger', None)
        _log_info = _log.info if _log else (lambda m: None)
        _log_warn = _log.warn if _log else (lambda m: None)

        alias = getattr(context, 'effective_model_alias', '') or None
        provenance = {'mode': DETERMINISTIC, 'prompt_name': prompt_name,
                      'model_alias': alias or '', 'binding_source': getattr(context, 'binding_source', 'DEFAULT')}
        _log_info('LLM reasoning: looking up prompt "%s" with model alias "%s"' % (prompt_name, alias or '(default)'))
        prompt, model = self._approved(prompt_name, alias)
        if not prompt or not model:
            provenance['reason'] = 'no_approved_prompt_or_model'
            missing = []
            if not prompt:
                missing.append('prompt')
            if not model:
                missing.append('model')
            _log_warn('LLM reasoning: no approved %s found — falling back to deterministic computation' % ' or '.join(missing))
            return None, provenance
        _log_info('LLM prompt resolved: "%s" v%s (hash: %s)' % (prompt['name'], prompt['version'], prompt['content_hash'][:12]))
        _log_info('LLM model resolved: "%s" v%s (id: %s)' % (model['name'], model['version'], model['id'][:12]))
        try:
            request = self.registry.gateway_request({
                'job_id': context.job_id, 'prompt_id': prompt['id'], 'model_id': model['id'],
                'input_evidence': payload, 'correlation_id': context.correlation_id or None})
        except (PermissionError, ValueError) as exc:
            # A refusal is a governed outcome, not an agent failure: the request
            # was recorded or rejected by policy and the agent computes instead.
            provenance['mode'] = DETERMINISTIC
            provenance['reason'] = 'gateway_refused:' + str(exc)
            _log_warn('LLM gateway refused request: %s — falling back to deterministic computation' % str(exc)[:200])
            return None, provenance
        _log_info('LLM gateway request accepted: %s (status: %s)' % (request['request_id'][:12], request['status']))
        provenance.update({'request_id': request['request_id'], 'prompt_id': prompt['id'],
                           'prompt_version': prompt['version'], 'prompt_hash': prompt['content_hash'],
                           'model_id': model['id'], 'model_version': model['version'],
                           'region': request.get('region', ''), 'status': request['status']})
        if context.provider is None:
            provenance['reason'] = 'no_provider_execution_adapter'
            _log_warn('LLM reasoning: no provider execution adapter configured — using deterministic fallback')
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
        _log_info('LLM invoking provider: model=%s, prompt="%s", payload_keys=%s' % (
            provider_model, prompt_name, sorted(payload.keys()) if isinstance(payload, dict) else '(non-dict)'))
        try:
            answer = provider.invoke(prompt['template'], payload)
            recorded = self.registry.complete_request(request['request_id'], answer)
        except (PermissionError, ValueError, NotImplementedError) as exc:
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
