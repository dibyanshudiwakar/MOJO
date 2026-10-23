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
import re
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
#: shape default". Each tier is one step down from where it was: what ran on Opus
#: runs on Sonnet, and what ran on Sonnet runs on Haiku. ``coding`` is held at
#: ``high`` rather than the ``xhigh`` it asked of Opus: xhigh was only ever
#: measured against Opus, and an effort level a model does not accept is a hard
#: request failure, not a quality loss.
ALIAS_MODELS = {
    'reasoning-large': ('claude-sonnet-5-5', 'high'),
    'coding': ('claude-sonnet-5-5', 'high'),
    'semantic-review': ('claude-haiku-4-5@20251001', None),
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
    #: ...and ``effort=``, a per-call override of how hard the model reasons.
    #: Hidden reasoning is billed against ``max_tokens`` but never appears in the
    #: reply, so a listing task at an alias's top effort can spend its whole
    #: output budget thinking: a 15,096-character file layout used all 32,768
    #: tokens at ``xhigh``. See ``tests/test_cvscode_adapter_parsing.py``.
    accepts_effort = True

    #: Output budget per alias. 16384 was one number for every agent and it is
    #: too small for the artefacts this factory asks for: the mapping catalogue
    #: for a real flow, the migration specification and a whole generated file
    #: all exceeded it, each surfacing as a truncated answer. The frontier
    #: models carry a far larger output window than Haiku, so the budget follows
    #: the alias rather than being set once -- asking Haiku for 32k would be
    #: refused outright by the gateway.
    #:
    #: ``MOJO_MAX_TOKENS`` overrides every alias, for a deployment whose gateway
    #: caps output lower (or higher) than these.
    ALIAS_MAX_TOKENS = {
        'reasoning-large': 32768,
        'coding': 32768,
        'semantic-review': 32768,
        'reasoning-small': 16384,
    }
    DEFAULT_MAX_TOKENS = 16384

    def __init__(self, base_url='', model='', effort=None, timeout=600, max_tokens=0,
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
        # An explicit argument wins, then the environment, then this alias's
        # own budget. ``for_alias`` passes 0 so a bound copy picks up the new
        # alias's budget instead of inheriting the one it was built with.
        override = 0
        try:
            override = int(os.environ.get('MOJO_MAX_TOKENS') or 0)
        except ValueError:
            override = 0
        self.max_tokens = (max_tokens or override
                           or self.ALIAS_MAX_TOKENS.get(alias, self.DEFAULT_MAX_TOKENS))

    def for_alias(self, alias):
        """A copy of this adapter pinned to ``alias``'s model."""
        if not alias or alias == self.alias:
            return self
        return CvscodeClaudeAdapter(base_url=self.base_url, timeout=self.timeout,
                                    alias=alias)

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

    def _request(self, template, payload, effort=None):
        body = {
            'model': self.model,
            'max_tokens': self.max_tokens,
            'system': template,
            'messages': [{'role': 'user', 'content': json.dumps(payload, sort_keys=True, default=str)}],
        }
        # A model with no effort parameter (Haiku errors on it) keeps none, however
        # one is asked for.
        chosen = (effort or self.effort) if self.effort else None
        if chosen:
            body['output_config'] = {'effort': chosen}
        return body

    def invoke(self, template, payload, skill=None, effort=None):
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
            message = self._client().messages.create(**self._request(system, payload, effort))
        except Exception as exc:  # SDK/network errors surface as a governed provider failure
            raise RuntimeError('provider_error:%s:%s' % (type(exc).__name__, exc)) from exc
        if getattr(message, 'stop_reason', '') == 'refusal':
            raise RuntimeError('provider_refused:' + str(getattr(message, 'stop_details', '')))
        text = ''.join(b.text for b in message.content if getattr(b, 'type', '') == 'text')
        parsed = _parse_json(text)
        brace, bracket = text.find('{'), text.find('[')
        if not isinstance(parsed, dict) and brace >= 0 and (bracket < 0 or brace < bracket):
            # The reply opens like an object but did not parse as one. A scalar
            # or array recovered from inside it is a fragment, not the answer.
            parsed = None
        if parsed is None and getattr(message, 'stop_reason', '') == 'max_tokens':
            # The answer was cut off mid-JSON, so it cannot parse. Reported as
            # itself rather than wrapped as {'answer': <raw>}: that wrapper then
            # fails the response schema and the run blames the model's shape for
            # what is really a length limit. Seen on the specification document,
            # which is the largest artefact any agent returns.
            # The head and tail are included because the raw reply is not
            # stored, and what a runaway answer is *made of* (a long rationale,
            # an entry per statement, a repeated block) is the only thing that
            # says how to shorten it.
            flat = ' '.join(text.split())
            raise RuntimeError(
                'provider_response_truncated:max_tokens=%d: the answer was cut off '
                'mid-JSON (%d chars; %s). Raise max_tokens for this agent or ask for a '
                'smaller artefact. Began: %s ... Ended: %s'
                % (self.max_tokens, len(text), _spend(message), flat[:200], flat[-300:]))
        if parsed is None:
            # Named for every agent, never wrapped. ``{'answer': <raw text>}``
            # passed an unparseable answer on to the response schema, which then
            # reported whichever property it required as missing -- so a wrapper
            # or length problem presented as the model returning the wrong
            # shape, identically and unhelpfully for all 15 agents. A
            # RuntimeError degrades (ReasoningGateway catches it) and carries
            # enough of the answer to see what actually came back.
            raise RuntimeError(
                'provider_response_unparseable:%d_chars:stop_reason=%s:%s'
                % (len(text), getattr(message, 'stop_reason', '?'),
                   ' '.join(text.strip()[:200].split())))
        answer = parsed if isinstance(parsed, dict) else {'answer': text}
        answer.setdefault('_model', self.model)
        return answer


def _spend(message):
    """Where the output budget went: reasoning, or the visible answer.

    A cut-off reply of 39,313 characters against a 32,768-token limit is about
    1.2 characters a token -- far below what JSON costs -- which says part of
    the budget went to something that is not in the text. Thinking blocks are
    skipped when the text is joined, so without this the cost is invisible.
    """
    blocks = {}
    for block in getattr(message, 'content', None) or []:
        kind = getattr(block, 'type', '?')
        size = len(getattr(block, 'text', '') or getattr(block, 'thinking', '') or '')
        blocks[kind] = blocks.get(kind, 0) + size
    out = getattr(getattr(message, 'usage', None), 'output_tokens', None)
    spend = 'output_tokens=%s, blocks=%s' % (out if out is not None else '?',
                                            ','.join('%s:%d' % kv for kv in sorted(blocks.items())) or 'none')
    if 'thinking' in blocks or 'redacted_thinking' in blocks:
        # A thinking block of 0 characters is not "no thinking": the gateway
        # omits the text, not the work. Misread once as proof reasoning was not
        # the problem, on a reply that spent 32,768 tokens to write 15,096
        # characters.
        spend += ' -- the model reasoned before answering; its text is omitted but its tokens count against max_tokens'
    return spend


def _span(text, opener, closer):
    """The outermost balanced ``opener``...``closer`` span, or ``''``.

    String-aware, so a brace inside a JSON string value does not unbalance the
    count -- generated Java and quoted ESQL are full of them.
    """
    start = text.find(opener)
    if start < 0:
        return ''
    depth = 0
    in_string = escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ''


_OPENERS = {'{': '}', '[': ']'}


def _rebracket(text):
    """Rewrite each closing bracket to match its actual opener, in nesting order.

    Observed live on ``agent/repository-intake/inventory``: Haiku, after a long
    run of sibling arrays (``files``, ``flows``, ``subflows``, ``esql``,
    ``schemas``, ``maps``, ``policies``), closed the enclosing ``inventory``
    *object* with ``]`` instead of ``}`` -- reproduced 4 times out of 5 tries,
    not a one-off. The opener at each nesting depth is never ambiguous, so the
    matching closer is always recoverable from it alone, even when the model's
    own closer is the wrong character. This makes the fix a safe no-op on
    already well-formed text: a closer that was already right is replaced with
    itself. String contents are left untouched so a literal bracket inside a
    quoted value is never rewritten.
    """
    stack = []
    out = list(text)
    in_string = escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in _OPENERS:
            stack.append(_OPENERS[char])
        elif char in ('}', ']') and stack:
            out[index] = stack.pop()
    return ''.join(out)


def _parse_json(text):
    """The model's JSON, however it chose to wrap it. ``None`` if unrecoverable.

    The original handled a fenced block opening at character zero, which covers
    a fenced answer and -- because ``rsplit`` on an absent fence returns the
    whole string -- one whose closing fence was cut off. What it did not handle
    was a fence that follows a sentence ("Here is the inventory:"), or an
    unfenced object with prose around it: neither starts with a fence, so no
    stripping happened and the whole thing failed to parse.

    Each plausible reading is now tried in turn, ending with a balanced-span
    extraction that finds a complete object regardless of what surrounds it.
    Each one is additionally tried through ``_rebracket`` before being given
    up on, which repairs a wrong-closer-type mistake (``]`` where nesting
    requires ``}``) without otherwise changing well-formed text. An answer
    genuinely cut off mid-object still returns ``None``, which the caller
    reports as unparseable rather than disguising as a schema failure.
    """
    if not text:
        return None
    s = str(text).strip()
    candidates = [s]
    fence = re.search(r'```[A-Za-z0-9_-]*[ \t]*\r?\n', s)
    if fence:
        body = s[fence.end():]
        candidates.append(body.rsplit('```', 1)[0] if '```' in body else body)
    # Only the outermost structure: whichever opener comes first in the text.
    # Trying '[' after '{' failed let an *inner* array of a document that did
    # not parse as a whole (cut off, or malformed) parse on its own, return as
    # the answer, and defeat both the max_tokens check and the unparseable
    # error below -- the run then reported a schema failure on {'answer': ...}
    # for what was a truncated specification.
    openers = [(s.find(o), o, c) for o, c in (('{', '}'), ('[', ']')) if o in s]
    if openers:
        _, opener, closer = min(openers)
        candidates.append(_span(s, opener, closer))
    for candidate in candidates:
        candidate = (candidate or '').strip()
        if not candidate:
            continue
        # The rebracketed version is tried second, not instead: it is only
        # ever needed when the literal text does not already parse.
        for attempt in (candidate, _rebracket(candidate)):
            try:
                return json.loads(attempt)
            except (ValueError, TypeError):
                continue
    return None
