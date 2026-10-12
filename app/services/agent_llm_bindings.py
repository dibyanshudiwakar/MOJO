"""Per-agent LLM binding service.

Each agent may have at most one binding row (unique agent_id). Two statuses:
  DRAFT  — saved but not yet active; resolve() ignores it, default alias is used.
  ACTIVE — the effective override; resolve() returns its gateway_model_alias.

Lifecycle:
  save()     → binding_mode DEFAULT saves immediately as ACTIVE
               binding_mode AGENT_OVERRIDE saves as DRAFT until activate() is called
  activate() → DRAFT → ACTIVE; runs AT-07 capability, AT-12 licence, AT-14 registry checks
  revert()   → deletes the binding row; agent falls back to catalogue default

Guards enforced here:
  AT-07  capability check — embedding alias rejected for chat agent and vice versa
  AT-08  raw-secret guard — model_id must not look like a bare API key
  AT-12  licence policy  — MIT/GPL licences rejected under Apache-2.0-only policy
  AT-14  registry pre-check — alias must be APPROVED in model_registry before activation

Guards enforced in dispatch() (agents.py):
  AT-05  fail-closed — active override whose alias is gone from model_registry raises,
                       never silently falls back to the catalogue default

Advisory warnings (validate() only — cannot be enforced without agent implementations):
  AT-09  governance agent — deterministic policy engine cannot be replaced by LLM
  AT-10  variance-analysis — structural/value comparator runs before any LLM fallback
"""
from app.repository import uid, now

VALID_MODES = ('DEFAULT', 'AGENT_OVERRIDE')

# Known capability buckets — only well-known aliases are checked; custom/unknown
# aliases bypass the guard rather than false-positive on BYO deployments.
_EMBEDDING_ALIASES = frozenset({'embeddings'})
_CHAT_ALIASES = frozenset({'reasoning-large', 'reasoning-small', 'coding', 'semantic-review'})

# AT-08: known credential prefixes that must never appear in agent config fields.
# Credentials must use vault references (vault://<approved-path>).
_SECRET_PREFIXES = ('sk-', 'ghp_', 'gho_', 'github_pat_', 'xoxb-', 'xoxp-', 'AKIA')

# AT-12: licences rejected under the Apache-2.0-only model-weights policy.
# BGE-M3 is the canonical example (MIT). [Design doc §2, §9, AT-12]
_APACHE2_RESTRICTED_LICENCES = frozenset({'mit', 'gpl', 'gpl-2.0', 'gpl-3.0', 'lgpl', 'lgpl-2.1', 'lgpl-3.0'})

# AT-09/AT-10: agents whose LLM binding is constrained by architecture.
# The binding only controls the *explanation* or *semantic-fallback* LLM;
# the deterministic engine cannot be replaced by a model override.
_CONSTRAINED_AGENTS = {
    'governance': (
        'AT-09:deterministic_policy_engine_cannot_be_overridden_by_llm_binding'
        ':only_the_explanation_llm_is_configurable'
    ),
    'variance-analysis': (
        'AT-10:structural_value_comparator_always_runs_first'
        ':llm_available_only_for_approved_semantic_fallback'
    ),
}


class AgentLLMBindingError(ValueError):
    pass


class AgentLLMBindingService:
    def __init__(self, repo):
        self.repo = repo

    # ------------------------------------------------------------------ read

    def get(self, agent_id):
        """Return the current binding row for this agent, or None."""
        return self.repo.one(
            'select * from agent_llm_bindings where agent_id=?', (agent_id,))

    def resolve(self, agent_id, default_alias):
        """Return (effective_alias, binding_source) for dispatch().

        Only an ACTIVE AGENT_OVERRIDE binding with a non-empty alias overrides
        the seeded default. A DRAFT binding is invisible at runtime.
        """
        binding = self.get(agent_id)
        if (binding
                and binding['binding_mode'] == 'AGENT_OVERRIDE'
                and binding.get('gateway_model_alias')
                and binding.get('status') == 'ACTIVE'):
            return binding['gateway_model_alias'], 'AGENT_OVERRIDE'
        return default_alias or '', 'DEFAULT'

    def get_history(self, agent_id, limit=20):
        """Return binding change events newest-first (AT-03/04 audit trail)."""
        return self.repo.rows(
            'select * from agent_llm_binding_history'
            ' where agent_id=? order by created_at desc limit ?',
            (agent_id, limit))

    def validate(self, agent_id):
        """Pre-activation safety check (§7.2, AT-07, AT-09, AT-10, AT-13).

        Returns {'valid': bool, 'errors': [...], 'warnings': [...], 'agent_id': ..., 'alias': ...}

        errors   → block activation
        warnings → advisory (AT-09/AT-10 agent constraints, AT-13 missing default)
        """
        errors = []
        warnings = []
        binding = self.get(agent_id)
        if not binding:
            return {'valid': False, 'errors': ['no_binding_found'],
                    'warnings': [], 'agent_id': agent_id, 'alias': ''}

        agent = self.repo.one(
            'select default_model_alias from agent_registry where agent_id=?', (agent_id,))
        default_alias = (agent or {}).get('default_model_alias') or ''

        # AT-09/AT-10: advisory warning for constrained agents
        constraint = _CONSTRAINED_AGENTS.get(agent_id)
        if constraint and binding.get('binding_mode') == 'AGENT_OVERRIDE':
            warnings.append(constraint)

        if binding['binding_mode'] == 'DEFAULT':
            # AT-13: warn when the catalogue default isn't registered yet
            if default_alias and not self.repo.one(
                    "select id from model_registry where name=? and status='APPROVED'"
                    ' order by created_at desc limit 1', (default_alias,)):
                warnings.append('default_alias_not_in_approved_model_registry:' + default_alias)
            return {'valid': True, 'errors': [], 'warnings': warnings,
                    'agent_id': agent_id, 'alias': default_alias}

        alias = (binding.get('gateway_model_alias') or '').strip()
        if not alias:
            errors.append('gateway_model_alias_empty')
            return {'valid': False, 'errors': errors, 'warnings': warnings,
                    'agent_id': agent_id, 'alias': ''}

        # Check alias registered in model_registry as APPROVED (AT-14 pre-flight)
        if not self.repo.one(
                "select id from model_registry where name=? and status='APPROVED'"
                ' order by created_at desc limit 1', (alias,)):
            errors.append('alias_not_in_approved_model_registry:' + alias)

        # AT-07 capability check
        if agent:
            try:
                self._check_capability(default_alias, alias)
            except AgentLLMBindingError as e:
                errors.append(str(e))

        # AT-12 licence policy check
        licence = (binding.get('licence_id') or '').strip().lower()
        if licence and licence in _APACHE2_RESTRICTED_LICENCES:
            errors.append('licence_policy_violation:' + licence + '_not_permitted_under_apache2_only_policy')

        # AT-13: also warn about the default not being in registry
        if default_alias and not self.repo.one(
                "select id from model_registry where name=? and status='APPROVED'"
                ' order by created_at desc limit 1', (default_alias,)):
            warnings.append('default_alias_not_in_approved_model_registry:' + default_alias)

        return {'valid': len(errors) == 0, 'errors': errors, 'warnings': warnings,
                'agent_id': agent_id, 'alias': alias}

    # ----------------------------------------------------------------- write

    def save(self, agent_id, binding_mode, gateway_model_alias, model_id, actor,
             model_revision='', provider_type='litellm_alias', reason_for_change='',
             capabilities='', licence_id='', data_region='',
             data_classification_allowed='', context_limit=0, output_limit=0):
        """Create or update the binding row.

        DEFAULT activates immediately. AGENT_OVERRIDE saves as DRAFT.
        """
        if binding_mode not in VALID_MODES:
            raise AgentLLMBindingError('invalid_binding_mode')
        if binding_mode == 'AGENT_OVERRIDE' and not (gateway_model_alias or '').strip():
            raise AgentLLMBindingError('gateway_model_alias_required_for_override')
        # AT-08: reject raw credentials in model_id
        self._check_no_raw_secret((model_id or '').strip(), 'model_id')
        new_status = 'ACTIVE' if binding_mode == 'DEFAULT' else 'DRAFT'
        ts = now()
        existing = self.get(agent_id)
        if existing:
            with self.repo.connect() as c:
                c.execute(
                    'update agent_llm_bindings set binding_mode=?,gateway_model_alias=?,'
                    'model_id=?,model_revision=?,provider_type=?,reason_for_change=?,'
                    'capabilities=?,licence_id=?,data_region=?,data_classification_allowed=?,'
                    'context_limit=?,output_limit=?,'
                    'status=?,created_by=?,updated_at=? where agent_id=?',
                    (binding_mode,
                     (gateway_model_alias or '').strip(),
                     (model_id or '').strip(),
                     (model_revision or '').strip(),
                     (provider_type or 'litellm_alias').strip(),
                     (reason_for_change or '').strip(),
                     (capabilities or '').strip(),
                     (licence_id or '').strip(),
                     (data_region or '').strip(),
                     (data_classification_allowed or '').strip(),
                     int(context_limit or 0),
                     int(output_limit or 0),
                     new_status, actor or '', ts, agent_id))
        else:
            with self.repo.connect() as c:
                self.repo.insert(c, 'agent_llm_bindings', {
                    'id': uid(),
                    'agent_id': agent_id,
                    'binding_mode': binding_mode,
                    'gateway_model_alias': (gateway_model_alias or '').strip(),
                    'model_id': (model_id or '').strip(),
                    'model_revision': (model_revision or '').strip(),
                    'provider_type': (provider_type or 'litellm_alias').strip(),
                    'reason_for_change': (reason_for_change or '').strip(),
                    'capabilities': (capabilities or '').strip(),
                    'licence_id': (licence_id or '').strip(),
                    'data_region': (data_region or '').strip(),
                    'data_classification_allowed': (data_classification_allowed or '').strip(),
                    'context_limit': int(context_limit or 0),
                    'output_limit': int(output_limit or 0),
                    'status': new_status,
                    'created_by': actor or '',
                    'activated_by': '',
                    'created_at': ts,
                    'updated_at': ts,
                })
        result = self.get(agent_id)
        self._record_history(agent_id, 'SAVED', result, actor)
        return result

    def activate(self, agent_id, actor):
        """Promote a DRAFT AGENT_OVERRIDE binding to ACTIVE.

        Runs AT-07 capability, AT-12 licence policy and AT-14 registry checks
        before promoting. Idempotent if already ACTIVE.
        """
        binding = self.get(agent_id)
        if not binding:
            raise AgentLLMBindingError('no_binding_to_activate')
        if binding['binding_mode'] != 'AGENT_OVERRIDE':
            raise AgentLLMBindingError('only_agent_override_bindings_can_be_activated')
        if binding['status'] == 'ACTIVE':
            return binding

        alias = (binding.get('gateway_model_alias') or '').strip()

        # AT-14: verify alias is currently APPROVED in model_registry
        if alias and not self.repo.one(
                "select id from model_registry where name=? and status='APPROVED'"
                ' order by created_at desc limit 1', (alias,)):
            raise AgentLLMBindingError(
                'activation_denied:alias_not_in_approved_model_registry:' + alias)

        # AT-07: capability guard
        agent = self.repo.one(
            'select default_model_alias from agent_registry where agent_id=?', (agent_id,))
        if agent:
            self._check_capability(agent.get('default_model_alias') or '', alias)

        # AT-12: licence policy — reject non-Apache licences under Apache-2.0-only policy
        licence = (binding.get('licence_id') or '').strip().lower()
        if licence and licence in _APACHE2_RESTRICTED_LICENCES:
            raise AgentLLMBindingError(
                'activation_denied:licence_policy_violation:'
                + licence + '_not_permitted_under_apache2_only_policy')

        ts = now()
        with self.repo.connect() as c:
            c.execute(
                "update agent_llm_bindings set status='ACTIVE',activated_by=?,updated_at=?"
                ' where agent_id=?',
                (actor or '', ts, agent_id))
        result = self.get(agent_id)
        self._record_history(agent_id, 'ACTIVATED', result, actor)
        return result

    def revert(self, agent_id, actor):
        """Delete the binding row so the agent falls back to its catalogue default."""
        existing = self.get(agent_id)
        self._record_history(agent_id, 'REVERTED', existing, actor)
        with self.repo.connect() as c:
            c.execute('delete from agent_llm_bindings where agent_id=?', (agent_id,))

    # --------------------------------------------------------------- private

    def _check_capability(self, default_alias, override_alias):
        """AT-07: reject mismatch between embedding and chat capability types."""
        if not override_alias:
            return
        override_is_embedding = override_alias in _EMBEDDING_ALIASES
        override_is_known_chat = override_alias in _CHAT_ALIASES
        if not (override_is_embedding or override_is_known_chat):
            return  # unknown alias — cannot check, allow through
        default_needs_embedding = default_alias in _EMBEDDING_ALIASES
        if default_needs_embedding and not override_is_embedding:
            raise AgentLLMBindingError(
                'capability_mismatch:agent_requires_embedding_but_alias_provides_chat')
        if not default_needs_embedding and override_is_embedding:
            raise AgentLLMBindingError(
                'capability_mismatch:agent_requires_chat_but_alias_provides_embedding')

    def _check_no_raw_secret(self, value, field_name):
        """AT-08: reject values whose prefix matches a known credential pattern."""
        if not value:
            return
        for prefix in _SECRET_PREFIXES:
            if value.startswith(prefix):
                raise AgentLLMBindingError(
                    f'raw_secret_rejected:{field_name}'
                    ' — store credentials in vault, not in agent config')

    def _record_history(self, agent_id, action, binding, actor):
        """Append one immutable row to the binding change log."""
        with self.repo.connect() as c:
            self.repo.insert(c, 'agent_llm_binding_history', {
                'id': uid(),
                'agent_id': agent_id,
                'action': action,
                'binding_mode': (binding or {}).get('binding_mode', ''),
                'gateway_model_alias': (binding or {}).get('gateway_model_alias', ''),
                'model_id': (binding or {}).get('model_id', ''),
                'model_revision': (binding or {}).get('model_revision', ''),
                'provider_type': (binding or {}).get('provider_type', ''),
                'reason_for_change': (binding or {}).get('reason_for_change', ''),
                'status': (binding or {}).get('status', ''),
                'actor': actor or '',
                'created_at': now(),
            })
