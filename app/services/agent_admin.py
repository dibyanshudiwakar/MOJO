"""Live agent-registry administration.

Registry mutation was previously a hard 405 (``registry_is_governed_configuration``),
backed by a startup check (``Repository._seed_agent_catalogue``) that raises
``agent_contract_drift`` and refuses to boot if the database and the
version-controlled catalogue file ever disagree -- both deliberate. This
module is the one place allowed to move both together: a write here takes
effect on the running server immediately (a real, live change, no restart
required), and it best-effort mirrors the same row into
``config/agent-catalogue.v1.json`` so a later restart's drift check still
finds agreement, on a filesystem that permits the write.

Every row this module writes is marked ``origin='RUNTIME'``.
``Repository._seed_agent_catalogue`` leaves a RUNTIME row alone on restart
rather than re-seeding or drift-checking it against the file -- editing the
file by hand and restarting without going through here still trips the
original drift check for every CATALOGUE-origin row exactly as before.

Creating a brand-new ``agent_id`` registers its governed contract only.
``AgentRegistry.dispatch`` still refuses to run it until a matching
``app/agents/<agent_id>.py`` implementation exists (reported as
``NOT_IMPLEMENTED`` by ``conformance()``) -- this manages configuration, not
code generation.
"""
import json
from pathlib import Path
from app.repository import now

REQUIRED = ('agent_id', 'display_name', 'version', 'description', 'required_role')
LIST_FIELDS = ('accepted_inputs', 'produced_outputs', 'tool_allowlist', 'dependencies')
STATUSES = ('APPROVED', 'DRAFT', 'RETIRED')
# The one agent_id this scheduler and the orchestrator agent both treat
# specially (app/services/pipelines.py: SELECTABLE_AGENTS_EXCLUDED). Letting
# an admin re-register it under a different contract would be confusing at
# best; the id is reserved.
RESERVED_AGENT_ID = 'migration-orchestrator'


class AgentAdminError(ValueError):
    pass


class AgentAdminService:
    def __init__(self, repo):
        self.repo = repo
        self.catalogue_path = Path(__file__).resolve().parents[2] / 'config' / 'agent-catalogue.v1.json'

    def _validate(self, payload):
        missing = [k for k in REQUIRED if not str(payload.get(k, '')).strip()]
        if missing:
            raise AgentAdminError('missing_' + ','.join(missing))
        agent_id = str(payload['agent_id']).strip().lower()
        if agent_id == RESERVED_AGENT_ID:
            # Reserved unconditionally, not just against creating a duplicate:
            # this id also has hardcoded special handling in
            # PipelineService.SELECTABLE_AGENTS_EXCLUDED and in the
            # orchestrator agent's own state-machine stepping, so even
            # editing its existing contract live through generic CRUD could
            # desynchronise those from what the registry now says.
            raise AgentAdminError('agent_id_reserved:' + RESERVED_AGENT_ID)
        out = dict(payload)
        out['agent_id'] = agent_id
        for key in LIST_FIELDS:
            value = out.get(key, [])
            if not isinstance(value, list):
                raise AgentAdminError(key + '_must_be_a_list')
            out[key] = value
        out['required_role'] = str(out['required_role']).strip().upper()
        out['enabled'] = bool(out.get('enabled', True))
        out['status'] = str(out.get('status') or 'APPROVED').upper()
        if out['status'] not in STATUSES:
            raise AgentAdminError('invalid_status')
        out.setdefault('effective_from', now())
        out.setdefault('approval_evidence_id', '')
        out.setdefault('supersedes_version', '')
        return out

    def list(self):
        return self.repo.rows('select * from agent_registry order by agent_id,version')

    def get(self, agent_id, version):
        row = self.repo.one('select * from agent_registry where agent_id=? and version=?', (agent_id, version))
        if not row:
            raise ValueError('agent_registry_row_not_found')
        return row

    def create_or_update(self, payload, actor):
        existing = self.repo.one('select name from agent_registry where agent_id=? and version=?',
                                 (str(payload.get('agent_id', '')).strip().lower(), payload.get('version')))
        a = self._validate(payload)
        # approved_by always comes from the authenticated caller, never the
        # request body -- section 13's "an approval is never self-asserted"
        # rule, applied to who gets credited with approving a contract.
        a['approved_by'] = actor
        h = self.repo.contract_hash(a)
        name = existing['name'] if existing else a['display_name']
        values = {
            'name': name, 'version': a['version'], 'accepted_inputs': json.dumps(a['accepted_inputs']),
            'produced_outputs': json.dumps(a['produced_outputs']), 'required_role': a['required_role'],
            'enabled': 1 if a['enabled'] else 0, 'agent_id': a['agent_id'], 'display_name': a['display_name'],
            'description': a['description'], 'tool_allowlist': json.dumps(a['tool_allowlist']),
            'dependencies': json.dumps(a['dependencies']), 'contract_hash': h, 'status': a['status'],
            'effective_from': a['effective_from'], 'approved_by': actor,
            'approval_evidence_id': a['approval_evidence_id'], 'supersedes_version': a['supersedes_version'],
            'origin': 'RUNTIME',
        }
        with self.repo.connect() as c:
            if existing:
                sets = ','.join(k + '=?' for k in values)
                c.execute('update agent_registry set ' + sets + ' where agent_id=? and version=?',
                          tuple(values.values()) + (a['agent_id'], a['version']))
            else:
                self.repo.insert(c, 'agent_registry', {**values, 'last_health_status': 'UNKNOWN', 'last_health_at': ''})
        file_synced = self._mirror_to_file(a)
        result = self.get(a['agent_id'], a['version'])
        return {**result, 'catalogue_file_synced': file_synced}

    def _mirror_to_file(self, a):
        """Best-effort. Must never fail the request that made the live change.

        On the hardened deployment (the bundled container runs with a
        read-only filesystem) this simply can't persist, and the live row
        stays RUNTIME-origin -- correct until someone applies the same change
        to the file through a real deploy.
        """
        try:
            data = json.loads(self.catalogue_path.read_text(encoding='utf-8')) if self.catalogue_path.exists() else {'agents': []}
            agents = data.get('agents', [])
            entry = {k: a[k] for k in ('agent_id', 'display_name', 'version', 'description', 'accepted_inputs',
                                       'produced_outputs', 'required_role', 'tool_allowlist', 'dependencies',
                                       'status', 'effective_from', 'approved_by', 'approval_evidence_id',
                                       'supersedes_version', 'enabled')}
            for i, existing in enumerate(agents):
                if existing.get('agent_id') == a['agent_id'] and existing.get('version') == a['version']:
                    agents[i] = entry
                    break
            else:
                agents.append(entry)
            data['agents'] = agents
            self.catalogue_path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
            return True
        except OSError:
            return False
