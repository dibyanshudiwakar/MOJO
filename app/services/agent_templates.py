import json
from app.repository import Repository, uid, now


class AgentTemplateService:
    def __init__(self, repo: Repository):
        self.repo = repo

    def list(self):
        return [self._decode(r) for r in
                self.repo.rows('select * from agent_templates order by created_at desc')]

    def get(self, template_id):
        row = self.repo.one('select * from agent_templates where id=?', (template_id,))
        return self._decode(row) if row else None

    def create(self, data, identity):
        name = str(data.get('name') or '').strip()
        if not name:
            raise ValueError('name_required')
        agent_ids = data.get('agent_ids') or []
        if not isinstance(agent_ids, list) or not agent_ids:
            raise ValueError('agent_ids_required')
        self._validate_agent_ids(agent_ids)
        if self.repo.one('select id from agent_templates where name=?', (name,)):
            raise ValueError('template_name_already_exists')
        tid = uid(); ts = now()
        with self.repo.connect() as c:
            self.repo.insert(c, 'agent_templates', {
                'id': tid,
                'name': name,
                'description': str(data.get('description') or ''),
                'agent_ids': json.dumps(agent_ids),
                'created_by': identity,
                'created_at': ts,
                'updated_at': ts,
            })
        return self.get(tid)

    def update(self, template_id, data):
        row = self.repo.one('select * from agent_templates where id=?', (template_id,))
        if not row:
            return None
        name = str(data.get('name') or row['name']).strip()
        if not name:
            raise ValueError('name_required')
        agent_ids = data.get('agent_ids')
        if agent_ids is None:
            agent_ids = json.loads(row['agent_ids'] or '[]')
        if not isinstance(agent_ids, list) or not agent_ids:
            raise ValueError('agent_ids_required')
        self._validate_agent_ids(agent_ids)
        if name != row['name'] and self.repo.one(
                'select id from agent_templates where name=? and id!=?', (name, template_id)):
            raise ValueError('template_name_already_exists')
        description = str(data.get('description', row['description']) or '')
        with self.repo.connect() as c:
            c.execute(
                'update agent_templates set name=?,description=?,agent_ids=?,updated_at=? where id=?',
                (name, description, json.dumps(agent_ids), now(), template_id),
            )
        return self.get(template_id)

    def delete(self, template_id):
        with self.repo.connect() as c:
            c.execute('delete from agent_templates where id=?', (template_id,))

    def _validate_agent_ids(self, agent_ids):
        # One query for all IDs rather than N individual lookups
        for aid in agent_ids:
            if not isinstance(aid, str) or not aid.strip():
                raise ValueError('invalid_agent_id_value')
        ph = ','.join('?' * len(agent_ids))
        enabled = {r['agent_id'] for r in self.repo.rows(
            f'select agent_id from agent_registry where agent_id in ({ph}) and enabled=1',
            agent_ids,
        )}
        missing = [aid for aid in agent_ids if aid not in enabled]
        if missing:
            raise ValueError('unknown_or_disabled_agents:' + ','.join(missing))

    def _decode(self, row):
        if row is None:
            return None
        out = dict(row)
        try:
            out['agent_ids'] = json.loads(out.get('agent_ids') or '[]')
        except (ValueError, TypeError):
            out['agent_ids'] = []
        return out
