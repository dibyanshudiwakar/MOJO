"""The TODO report: everything a person still has to do by hand after code generation.

Generated code is never complete. What cannot be derived from the ACE source is left as a ``TODO``
beside the ACE statement it came from, the code-generation run lists the decisions it could not
make, and the remediation run leaves some findings open. Those were scattered across source files,
a README and step metrics. This gathers them into one page that opens like the variance-analysis and
ACE-analysis reports do, so the hand-over is one list rather than a search.

Three sources, each read from records rather than asserted:

* ``TODO`` markers in the generated project's ``.java``, ``.xml``, ``.properties``, ``.yml``/``.yaml`` files and
  ``Dockerfile`` (file, line, the text, and the line beside it).
* The code-generation run's ``action_items`` and the project's ``provenance-manifest.json``.
* What the remediation run could not fix, and the causes it was not confident enough to act on.

Stored as a ``validation_reports`` row (``TODO_REPORT``) and rendered from its JSON on request, by the
same route that serves the other reports, so nothing in it is interpreted as markup. The page is also
written to ``<pipeline workspace>/reports/todo-report.html`` beside the other reports.
"""
import json
import re
from pathlib import Path

from app.repository import digest, now, uid
from app.services import failure_reports as F
from app.services import feedback, remediation_fix
from app.services import static_check_reports as H

TODO = 'TODO_REPORT'
FILENAME = 'todo-report.html'

#: Where a marker can be left for a person to act on: Java and XML sources (a TODO beside the ACE
#: statement it could not translate), and the configuration and build files (properties, YAML, the
#: Dockerfile) that carry values nobody could derive. Docs and build output are never scanned.
SUFFIXES = ('.java', '.xml', '.properties', '.yml', '.yaml')
NAMES = ('Dockerfile',)
CONFIG_SUFFIXES = ('.properties', '.yml', '.yaml')
SKIP_PARTS = ('target', '.git', 'node_modules')
#: A page of thousands of lines helps nobody; the cap is reported so the list is never silently short.
MAX_TODOS = 1500
MAX_TEXT = 400

_MARK = re.compile(r'\bTODO\b[\s:\-]*(.*)')
_COMMENT_LEAD = re.compile(r'^[\s/*#<!\-]+')
_COMMENT_TAIL = re.compile(r'[\s*/>\-]+$')

#: (category, words in the TODO text or the file that put it there). First match wins.
CATEGORIES = (
    ('Configuration', ('externalise', 'externalize', 'inject', 'hard-cod', 'hardcod', 'endpoint', 'address', 'url',
                       'credential', 'secret', 'context_root', 'context-root', 'placeholder', 'as/400')),
    ('Business logic', ('esql', 'translate', 'replace with', 'transformation', 'routing condition', 'assignment',
                        'condition', 'throws here')),
    ('Integration', ('parse', 'invoke', 'timeout', 'queue', 'mq', 'backend', 'file', 'message body', 'callers')),
)
ORDER = ('Configuration', 'Business logic', 'Integration', 'Other')


def html_url(report_id):
    return '/api/v2/reports/%s/html' % report_id


def category(path, text):
    low = str(text).lower()
    # A value in a properties/YAML file or the Dockerfile is configuration whatever the comment says.
    if str(path).lower().endswith(CONFIG_SUFFIXES) or str(path).rsplit('/', 1)[-1] in NAMES:
        return 'Configuration'
    for name, words in CATEGORIES:
        if any(w in low for w in words):
            return name
    return 'Other'


def _clean(line):
    return _COMMENT_TAIL.sub('', _COMMENT_LEAD.sub('', line)).strip()


def scan(root):
    """``(todos, files_scanned, truncated)`` for the generated project at ``root``.

    Walks through ``feedback.find`` and reads through ``feedback.lp`` so a file past the Windows
    260-character limit is listed and read like any other.
    """
    base = Path(root)
    todos, scanned, truncated = [], 0, False
    for f in feedback.find(root, '*'):
        rel = f.relative_to(base).as_posix()
        if any(part in SKIP_PARTS for part in rel.split('/')[:-1]):
            continue
        if not (f.suffix.lower() in SUFFIXES or f.name in NAMES) or not feedback.small(f):
            continue
        try:
            lines = feedback.lp(f).read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            continue
        scanned += 1
        for i, line in enumerate(lines):
            m = _MARK.search(line)
            if not m:
                continue
            if len(todos) >= MAX_TODOS:
                truncated = True
                return todos, scanned, truncated
            text = _clean(m.group(1)) or _clean(line)
            beside = next((_clean(x) for x in lines[i + 1:i + 4] if _clean(x) and not _MARK.search(x)), '')
            todos.append({'file': rel, 'line': i + 1, 'text': text[:MAX_TEXT], 'beside': beside[:MAX_TEXT],
                          'category': category(rel, text)})
    return todos, scanned, truncated


def _action_items(repo, job_id, root):
    """What code generation said it could not decide, from its run and the project's provenance manifest."""
    items = []
    # A step's metrics live in pipeline_context (agent_runs has no such column).
    run = repo.one("select metrics from pipeline_context where job_id=? and agent_id='code-generation' "
                   'order by created_at desc limit 1', (job_id,))
    try:
        items += (json.loads((run or {}).get('metrics') or '{}').get('action_items') or [])
    except (TypeError, ValueError):
        pass
    if root:
        try:
            manifest = json.loads(feedback.lp(Path(root) / 'provenance-manifest.json').read_text(encoding='utf-8'))
            items += manifest.get('action_items') or []
        except (OSError, ValueError, AttributeError):
            pass
    seen, out = set(), []
    for x in items:
        text = x if isinstance(x, str) else json.dumps(x, sort_keys=True, default=str)
        if text and text not in seen:
            seen.add(text)
            out.append(text[:MAX_TEXT * 2])
    return out


def _left_open(remediation):
    """What the remediation run did not close, from its own report."""
    out = []
    for c in (remediation or {}).get('causes') or []:
        for u in c.get('unfixed') or []:
            out.append({'what': u.get('ref') or c.get('area') or c.get('source') or '',
                        'why': u.get('reason') or 'not fixed', 'cause': c.get('hypothesis', '')})
        if not c.get('eligible'):
            out.append({'what': c.get('hypothesis', ''), 'cause': c.get('area') or c.get('source') or '',
                        'why': 'below the confidence bar (%s of %s)' % (c.get('confidence'), c.get('threshold'))})
    return out


def collect(repo, job_id, remediation=None):
    root, _plan = remediation_fix.locate_project(repo, job_id)
    todos, scanned, truncated = scan(root) if root else ([], 0, False)
    return {'job_id': job_id, 'project_root': root, 'files_scanned': scanned, 'truncated': truncated,
            'todos': todos, 'action_items': _action_items(repo, job_id, root), 'left_open': _left_open(remediation),
            'generated_at': now()}


def record(repo, job_id, remediation=None):
    """Collect, store and write the report. Returns the fields to add to the step's metrics.

    Never raises: the remediation it follows must not fail because a hand-over page could not be built.
    """
    try:
        data = collect(repo, job_id, remediation)
        rid, payload = uid(), json.dumps(data, sort_keys=True, default=str)
        with repo.connect() as c:
            repo.insert(c, 'validation_reports', {'id': rid, 'job_id': job_id, 'report_type': TODO, 'version': '1.0.0',
                                                  'payload': payload, 'content_hash': digest(payload),
                                                  'status': 'ANALYSED', 'created_at': now(), 'reference_id': ''})
        out = {'todo_report_id': rid, 'todo_html_url': html_url(rid), 'todo_count': len(data['todos']),
               'todo_action_items': len(data['action_items'])}
        out.update(_write_file(repo, job_id, rid, data))
        return out
    except Exception as exc:                                    # noqa: BLE001 -- see the docstring
        return {'todo_error': '%s:%s' % (type(exc).__name__, exc)}


def _write_file(repo, job_id, report_id, data):
    try:
        from app.services import workspace
        page = render(data, {'report_id': report_id, 'job_id': job_id, 'generated_at': data['generated_at']})
        pipeline = repo.one('select workspace_dir from pipelines where job_id=? order by created_at desc limit 1', (job_id,))
        base = (pipeline or {}).get('workspace_dir')
        folder = (str(base).rstrip('/\\') + '/reports') if base else 'reports/' + str(job_id)
        path = workspace.resolve(folder, must_exist=False, create=True) / FILENAME
        path.write_text(page, encoding='utf-8')
        return {'todo_html_path': path.relative_to(workspace.root()).as_posix()}
    except Exception as exc:                                    # noqa: BLE001
        return {'todo_html_error': '%s:%s' % (type(exc).__name__, exc)}


def latest(repo, job_id):
    """``{'report_id', 'html_url', 'todo_count'}`` for the job's newest TODO report, or ``None``."""
    row = repo.one('select id,payload from validation_reports where job_id=? and report_type=? order by created_at desc limit 1',
                   (job_id, TODO))
    if not row:
        return None
    try:
        count = len(json.loads(row['payload'] or '{}').get('todos') or [])
    except (TypeError, ValueError):
        count = 0
    return {'report_id': row['id'], 'html_url': html_url(row['id']), 'todo_count': count}


# -- the page ------------------------------------------------------------------------------------------------
def render(data, meta):
    todos = data.get('todos') or []
    items = data.get('action_items') or []
    left = data.get('left_open') or []
    by_cat = {c: [t for t in todos if t['category'] == c] for c in ORDER}
    files = len({t['file'] for t in todos})
    body = [
        '<p class="note">Generated code is not finished. What could not be derived from the ACE source is marked '
        '<b>TODO</b> beside the statement it came from, the decisions code generation could not make are listed '
        'below, and remediation left some findings open. Work through each section; this page does not change anything.</p>',
        '<div class="tiles">%s</div>' % ''.join([
            H._tile('TODOs in code', len(todos), 'PARTIAL' if todos else 'MATCH'),
            H._tile('Files with a TODO', files), H._tile('Decisions to make', len(items), 'PARTIAL' if items else 'MATCH'),
            H._tile('Left open by remediation', len(left), 'GAP' if left else 'MATCH')]),
    ]
    if not data.get('project_root'):
        body.append('<p class="note">No generated project was found on disk for this job, so the code could not be scanned.</p>')
    if data.get('truncated'):
        body.append('<p class="note">Showing the first %d TODOs; the project has more.</p>' % MAX_TODOS)
    for name in ORDER:
        rows = by_cat[name]
        if not rows:
            continue
        body.append('<h2>%s<small>%d</small></h2>%s' % (H._e(name), len(rows), H._table(
            ['File', 'Line', 'What to do', 'Beside it in the code'],
            [[F._pre(t['file'], 300), H._e(t['line']), H._e(t['text']) or '<span class="empty">see the code</span>',
              F._pre(t['beside'], 400) if t['beside'] else '<span class="empty">—</span>'] for t in rows],
            ['26%', '6%', '38%', '30%'])))
    if items:
        body.append('<h2>Decisions code generation could not make<small>%d</small></h2><div class="card"><ol>%s</ol></div>'
                    % (len(items), ''.join('<li>%s</li>' % H._e(x) for x in items)))
    if left:
        body.append('<h2>Left open by remediation<small>%d</small></h2>%s' % (len(left), H._table(
            ['What', 'Why it is still open', 'Related finding'],
            [[H._e(x['what']), H._e(x['why']), H._e(x['cause'])] for x in left], ['34%', '36%', '30%'])))
    if not (todos or items or left):
        body.append('<h2>Nothing to do</h2><div class="card"><p>No TODO markers, open decisions or unfixed findings '
                    'were recorded for this job.</p></div>')
    body.append(F._footer(dict(meta, reference='project %s · %d file(s) scanned' % (
        data.get('project_root') or '—', data.get('files_scanned', 0)))))
    return F._page('TODO report — what is left to do', H._e(data.get('project_root') or ''), body)


def render_row(row):
    """The HTML page for a stored ``TODO_REPORT`` row."""
    data = json.loads(row['payload'] or '{}')
    return render(data, {'report_id': row.get('id', ''), 'job_id': row.get('job_id', ''),
                         'generated_at': row.get('created_at', '')})
