"""Claude provider adapter using the CVS Health cvscode gateway.

Implements the ProviderExecutionAdapter seam so every agent that reasons through
``ReasoningGateway`` reaches Claude via the CVS LMS gateway, exactly as the
cvscode CLI does. Nothing is stored in agent configuration: the API key is
fetched at call time from the cvscode helper.

Key resolution (first hit wins):
  1. ``CVS_KEY_HELPER`` command (default: ``cvscode.exe auth print-lms-key``)
  2. ``~/.claude/.cvscode-api-key``

Model selection is per *alias*, not global. ``AgentRegistry.dispatch`` resolves
each agent's ``default_model_alias`` (or an active AGENT_OVERRIDE) and
``ReasoningGateway`` hands that alias here through ``for_alias``, so the
``reasoning-large`` agents and the ``reasoning-small`` ones genuinely reach
different models instead of collapsing onto one.

The model ids below were read from this gateway's own ``GET /v1/models``. Note
what it does *not* serve: there is no ``claude-haiku-5-5`` (its newest Haiku is
``claude-haiku-4-5@20251001``) and no Fable. Each alias can be overridden with
``MOJO_MODEL_<ALIAS>`` (e.g. ``MOJO_MODEL_REASONING_LARGE=claude-opus-5``); the
gateway also accepts a ``[1m]`` suffix for the 1M-context variants.
"""
import json
import os
import subprocess
from pathlib import Path
from app.agents.reasoning import ProviderExecutionAdapter

DEFAULT_BASE_URL = 'https://hyperion-lms-api.prod.cvshealth.com'
DEFAULT_KEY_HELPER = str(Path.home() / 'AppData' / 'Local' / 'cvscode' / 'cvscode.exe') + ' auth print-lms-key'

# The gateway requires the same X-Client-* headers cvscode sends.
GATEWAY_HEADERS = {
    'X-Client-Name': 'c031730',
    'X-Client-Version': '1.6.1',
    'X-Client-Platform': 'win32',
    'X-Client-Arch': 'x64',
    'X-Client-Env': 'prod',
    'X-Client-User': 'cvscode',
}

#: Per-model request shape. ``effort`` is None where the model rejects
#: ``output_config.effort`` (Haiku 4.5 errors on it). ``temperature`` is not in
#: this SDK build's ``Messages.create()`` signature at all -- passing it raises
#: a ``TypeError`` before any request is sent, for every model, so this adapter
#: never sends it. Getting ``effort`` wrong is a hard request failure, not a
#: quality regression, so the shape travels with the model id rather than
#: being assumed.
MODEL_SHAPES = {
    'claude-opus-5-5': {'effort': 'high'},
    'claude-sonnet-5-5': {'effort': 'high'},
    'claude-opus-5': {'effort': 'high'},
    'claude-sonnet-5': {'effort': 'high'},
    'claude-opus-4-8': {'effort': 'high'},
    'claude-sonnet-4-6': {'effort': 'high'},
    'claude-haiku-4-5@20251001': {'effort': None},
}
#: Applied to a model id this module has no row for (including an operator's
#: ``MOJO_MODEL_*`` override): assume the current-generation contract, because
#: sending a removed parameter fails the request outright while omitting an
#: accepted one only costs a little quality.
FALLBACK_SHAPE = {'effort': 'high'}

#: alias -> (model id, effort override). Effort None means "use the model's
#: shape default"; ``coding`` asks for xhigh because code generation and
#: agentic work are where the top of the effort range actually pays for itself.
ALIAS_MODELS = {
    'reasoning-large': ('claude-opus-5-5', 'high'),
    'coding': ('claude-opus-5-5', 'xhigh'),
    'semantic-review': ('claude-sonnet-5-5', 'high'),
    'reasoning-small': ('claude-haiku-4-5@20251001', None),
}

#: Aliases that do not name a chat model. Claude has no embeddings endpoint at
#: all, so an ``embeddings`` alias cannot be served here. Failing loudly is the
#: point: quietly answering an embedding request with a chat completion would
#: produce prose where the caller expected a vector.
NON_CHAT_ALIASES = {'embeddings'}

#: Used only when an agent resolves to no alias at all.
DEFAULT_ALIAS = 'reasoning-large'


def get_cvs_api_key():
    """Fetch the CVS Health LMS API key via the cvscode setup."""
    helper = os.environ.get('CVS_KEY_HELPER') or DEFAULT_KEY_HELPER
    try:
        result = subprocess.run(helper, shell=True, capture_output=True, text=True, timeout=15)
        key = result.stdout.strip()
        if key:
            return key
    except Exception:
        pass
    key_file = Path.home() / '.claude' / '.cvscode-api-key'
    if key_file.exists():
        key = key_file.read_text(encoding='utf-8').strip()
        if key:
            return key
    raise RuntimeError('cvscode_api_key_unavailable: install cvscode or create ~/.claude/.cvscode-api-key')


def resolve_alias(alias):
    """Return ``(model_id, effort)`` for a gateway model alias."""
    name = (alias or DEFAULT_ALIAS).strip() or DEFAULT_ALIAS
    if name in NON_CHAT_ALIASES:
        raise RuntimeError(
            'alias_not_served_by_claude:' + name +
            ': Claude exposes no embeddings endpoint. Re-point the agent at a chat '
            'alias in config/agent-catalogue.v1.json, or serve this alias elsewhere.')
    override = os.environ.get('MOJO_MODEL_' + name.replace('-', '_').upper())
    model, effort = ALIAS_MODELS.get(name, ALIAS_MODELS[DEFAULT_ALIAS])
    return (override or model), effort


class CvscodeClaudeAdapter(ProviderExecutionAdapter):
    """Call Claude through the CVS cvscode gateway.

    One instance serves every alias; ``for_alias`` returns a cheap bound copy
    pinned to one model. ``ReasoningGateway`` calls that when the resolved alias
    is known, so this object stays stateless and safe to share between jobs.
    """

    #: ``invoke`` takes ``skill=``; ReasoningGateway passes the agent's skill to adapters that say so.
    accepts_skill = True

    def __init__(self, base_url='', model='', effort=None, timeout=600, max_tokens=16384,
                 alias=DEFAULT_ALIAS):
        self.base_url = base_url or os.environ.get('ANTHROPIC_BASE_URL') or DEFAULT_BASE_URL
        self.alias = alias
        if model:
            self.model, default_effort = model, None
        else:
            self.model, default_effort = resolve_alias(alias)
        shape = MODEL_SHAPES.get(self.model, FALLBACK_SHAPE)
        self.effort = effort or default_effort or shape['effort']
        self.timeout = timeout
        self.max_tokens = max_tokens

    def for_alias(self, alias):
        """A copy of this adapter pinned to ``alias``'s model."""
        if not alias or alias == self.alias:
            return self
        return CvscodeClaudeAdapter(base_url=self.base_url, timeout=self.timeout,
                                    max_tokens=self.max_tokens, alias=alias)

    def _client(self):
        import anthropic
        # This SDK build validates ``http_client`` against ``httpx2``, not the
        # plain ``httpx`` package -- passing an ``httpx.Client`` raises
        # "Invalid `http_client` argument" even though the two are API-compatible.
        import httpx2
        key = get_cvs_api_key()  # fresh on every call; keys rotate
        return anthropic.Anthropic(
            api_key=key,
            base_url=self.base_url,
            # The key goes in BOTH headers on purpose. The SDK sends ``api_key``
            # as ``x-api-key``, which is what Anthropic's own API reads, but this
            # gateway reads ``Authorization`` only -- it answers a request
            # carrying just ``x-api-key`` with 401 "Authorization header
            # missing", and the message changes to "Invalid token" as soon as
            # ``Authorization`` is present. Sending both keeps the adapter
            # working against either endpoint rather than trading one for the
            # other, and costs nothing: each side ignores the header it does not
            # read. Built here rather than in GATEWAY_HEADERS because the key is
            # fetched per call.
            default_headers={**GATEWAY_HEADERS, 'Authorization': 'Bearer ' + key},
            http_client=httpx2.Client(trust_env=False, timeout=httpx2.Timeout(float(self.timeout))),
        )

    def _request(self, template, payload):
        body = {
            'model': self.model,
            'max_tokens': self.max_tokens,
            'system': template,
            'messages': [{'role': 'user', 'content': json.dumps(payload, sort_keys=True, default=str)}],
        }
        if self.effort:
            body['output_config'] = {'effort': self.effort}
        return body

    def invoke(self, template, payload, skill=None):
        """Send the approved template + cleared payload to Claude; return a dict.

        A JSON-object answer is returned as-is (agents expect structured output),
        possibly inside a ```json fence; anything else is wrapped under 'answer'.

        ``skill`` is the optional per-agent guidance from ``agent-skills/``, in
        the shape ``tests/agents/test_agent_skills.py`` specifies for a
        skill-aware adapter. ``ReasoningGateway`` passes the running agent's
        skill because this class sets ``accepts_skill``. The approved prompt
        stays first in the system prompt -- the skill is guidance appended to
        it, never a replacement for the template the gateway authorised.
        """
        system = template
        if skill is not None:
            try:
                system = template + '\n\n---\n' + skill.as_system_prompt()
            except AttributeError:
                pass
        try:
            message = self._client().messages.create(**self._request(system, payload))
        except Exception as exc:  # SDK/network errors surface as a governed provider failure
            raise RuntimeError('provider_error:%s:%s' % (type(exc).__name__, exc)) from exc
        if getattr(message, 'stop_reason', '') == 'refusal':
            raise RuntimeError('provider_refused:' + str(getattr(message, 'stop_details', '')))
        text = ''.join(b.text for b in message.content if getattr(b, 'type', '') == 'text')
        parsed = _parse_json(text)
        answer = parsed if isinstance(parsed, dict) else {'answer': text}
        answer.setdefault('_model', self.model)
        return answer


def _parse_json(text):
    s = text.strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[1] if '\n' in s else ''
        s = s.rsplit('```', 1)[0]
    try:
        return json.loads(s.strip())
    except (ValueError, TypeError):
        return None
