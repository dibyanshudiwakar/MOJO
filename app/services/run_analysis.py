"""The overall run analysis behind the Metrics page.

Three views of the same runs: headline totals, a matrix of pipelines against the agent
steps they ran (each cell the step's status and time), and a per-agent summary. The
per-agent figures come from ``learning.overview`` so the two pages cannot disagree.
"""
from datetime import datetime

from app.services import learning

MAX_PIPELINES = 25


def _seconds(started, finished):
    try:
        a = datetime.fromisoformat(str(started).replace('Z', '+00:00'))
        b = datetime.fromisoformat(str(finished).replace('Z', '+00:00'))
        return max(0, round((b - a).total_seconds()))
    except (TypeError, ValueError):
        return None


def overview(repo):
    pipelines = repo.rows('select id, name, workspace_dir, status, started_at, created_at from pipelines '
                          'order by created_at desc limit ?', (MAX_PIPELINES,))
    by_status = {}
    for row in repo.rows('select status, count(*) n from pipelines group by status'):
        by_status[row['status']] = row['n']
    ids = [p['id'] for p in pipelines]
    steps = repo.rows('select pipeline_id, agent_id, kind, sequence, status, started_at, completed_at '
                      'from pipeline_steps where pipeline_id in (%s) order by sequence'
                      % ','.join('?' * len(ids)), tuple(ids)) if ids else []
    columns, cells = [], {}
    for s in steps:
        name = s['agent_id'] or s['kind']
        if name not in columns:
            columns.append(name)
        cells.setdefault(s['pipeline_id'], {})[name] = {
            'status': s['status'], 'seconds': _seconds(s['started_at'], s['completed_at'])
            if s['completed_at'] else None}
    matrix = [{'id': p['id'], 'name': p['name'] or p['workspace_dir'] or p['id'], 'status': p['status'],
               'started_at': p['started_at'] or p['created_at'], 'steps': cells.get(p['id'], {})}
              for p in pipelines]
    agents = learning.overview(repo)['agents']
    runs = sum(a['runs'] for a in agents)
    completed = sum(a['completed'] or 0 for a in agents)
    timed = [a for a in agents if a['avg_seconds'] is not None]
    avg = round(sum(a['avg_seconds'] * a['runs'] for a in timed) / max(1, sum(a['runs'] for a in timed)), 1) \
        if timed else None
    open_questions = (repo.one("select count(*) n from clarifications where status='OPEN'") or {}).get('n', 0)
    return {'summary': {'pipelines': sum(by_status.values()), 'by_status': by_status, 'runs': runs,
                        'completed': completed, 'failed': runs - completed,
                        'success_rate': round(100 * completed / runs) if runs else None,
                        'avg_seconds': avg, 'open_clarifications': open_questions},
            'columns': columns, 'pipelines': matrix, 'agents': agents}
