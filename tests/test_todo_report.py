"""The TODO report: what a person still has to do by hand after code generation.

Read from the generated project on disk, the code-generation run's action items and the remediation
report, stored like the other reports and opened through the same route.
"""
import json

import pytest

from app.repository import Repository, now, uid
from app.service import FactoryService
from app.services import todo_report


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_WORKSPACE_ROOT', str(tmp_path / 'workspaces'))
    repo = Repository(str(tmp_path / 'todo.db'))
    job = FactoryService(repo).create_job({'service': 'Orders', 'owner': 'Retail', 'sourceRevision': 'abc'}, 'tester')
    return repo, job, tmp_path


def _project(root):
    files = {
        'svc-web/src/main/java/com/x/OrderService.java':
            'class OrderService {\n    // TODO externalise the address in: SET OutputRoot.URL = \'http://h\'\n    String url = null;\n'
            '    // TODO Translate the ACE routing condition for node Route1\n    boolean ok() { return true; }\n}\n',
        'svc-web/pom.xml': '<project>\n  <!-- TODO pin the framework version -->\n</project>\n',
        'svc-web/src/main/resources/application-local.properties': '# TODO set the AS/400 host\nhost=\n',
        'config/deploy-config/values.qa.yaml': '# TODO fill in CONTEXT_ROOT\nCONTEXT_ROOT: ""\n',
        'Dockerfile': '# TODO confirm the base image\nFROM x\n',
        # Not scanned: documentation, and build output.
        'README.md': 'TODO read me\n',
        'svc-web/target/classes/Old.java': '// TODO stale build output\n',
        'svc-web/src/main/java/com/x/Done.java': 'class Done { }\n',
    }
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding='utf-8')
    return root


def test_scan_finds_todos_in_java_xml_properties_yaml_and_dockerfile_only(tmp_path):
    todos, scanned, truncated = todo_report.scan(_project(tmp_path / 'EIPMS_x'))
    assert {t['file'] for t in todos} == {
        'svc-web/src/main/java/com/x/OrderService.java', 'svc-web/pom.xml',
        'svc-web/src/main/resources/application-local.properties',
        'config/deploy-config/values.qa.yaml', 'Dockerfile'}
    assert not truncated and scanned == 6              # the five above plus the TODO-free Java file


def test_scan_reports_line_text_and_the_line_beside_it(tmp_path):
    todos, _, _ = todo_report.scan(_project(tmp_path / 'EIPMS_x'))
    first = next(t for t in todos if t['text'].startswith('externalise'))
    assert first['line'] == 2 and first['beside'] == 'String url = null;'
    assert next(t for t in todos if t['file'] == 'svc-web/pom.xml')['text'] == 'pin the framework version'


def test_categories(tmp_path):
    todos, _, _ = todo_report.scan(_project(tmp_path / 'EIPMS_x'))
    cat = {t['file'].rsplit('/', 1)[-1]: t['category'] for t in todos}
    assert cat['application-local.properties'] == 'Configuration'      # a config file is configuration
    assert cat['values.qa.yaml'] == 'Configuration' and cat['Dockerfile'] == 'Configuration'
    assert todo_report.category('A.java', 'Translate the ACE routing condition') == 'Business logic'
    assert todo_report.category('A.java', 'externalise the address in: ESQL') == 'Configuration'
    assert todo_report.category('A.java', 'something unusual') == 'Other'


def test_a_project_with_no_todos_says_so(tmp_path):
    root = tmp_path / 'EIPMS_x'
    (root / 'A.java').parent.mkdir(parents=True)
    (root / 'A.java').write_text('class A { }\n')
    page = todo_report.render({'project_root': str(root), 'todos': [], 'action_items': [], 'left_open': []},
                              {'report_id': 'r', 'job_id': 'j', 'generated_at': 'now'})
    assert 'Nothing to do' in page


def test_the_page_escapes_everything_it_shows(tmp_path):
    data = {'project_root': '/p', 'files_scanned': 1, 'todos': [
        {'file': 'A.java', 'line': 3, 'text': '<script>alert(1)</script>', 'beside': '<b>x</b>', 'category': 'Other'}],
        'action_items': ['<img src=x onerror=1>'], 'left_open': []}
    page = todo_report.render(data, {'report_id': 'r', 'job_id': 'j', 'generated_at': 'now'})
    assert '<script>alert' not in page and '&lt;script&gt;' in page and '<img src=x' not in page


def test_collect_gathers_code_generation_items_and_what_remediation_left_open(env):
    repo, job, tmp_path = env
    root = _project(tmp_path / 'EIPMS_x')
    (root / 'provenance-manifest.json').write_text(json.dumps({'action_items': ['Decide the CONTEXT_ROOT', 'Decide X']}))
    with repo.connect() as c:
        c.execute('insert into quality_runs values(?,?,?,?,?,?,?,?)',
                  (uid(), job['id'], 'plan', 'PASS', '{}', 'h', now(), str(root)))
        c.execute('insert into pipeline_context(id,pipeline_id,step_id,agent_id,job_id,metrics,created_at) values(?,?,?,?,?,?,?)',
                  (uid(), 'p', 's', 'code-generation', job['id'],
                   json.dumps({'action_items': ['Decide X', 'Confirm the AS/400 values']}), now()))
    remediation = {'causes': [
        {'unfixed': [{'ref': 'A.java', 'reason': 'ambiguous edit'}], 'eligible': True, 'hypothesis': 'h1'},
        {'unfixed': [], 'eligible': False, 'hypothesis': 'h2', 'confidence': 0.4, 'threshold': 0.6, 'source': 'PARITY'}]}
    data = todo_report.collect(repo, job['id'], remediation)
    assert len(data['todos']) == 6 and data['project_root'] == str(root)
    assert data['action_items'] == ['Decide X', 'Confirm the AS/400 values', 'Decide the CONTEXT_ROOT']
    assert [x['what'] for x in data['left_open']] == ['A.java', 'h2']
    assert 'below the confidence bar' in data['left_open'][1]['why']


def test_record_stores_a_report_the_route_can_render_and_latest_finds_it(env):
    repo, job, tmp_path = env
    root = _project(tmp_path / 'EIPMS_x')
    with repo.connect() as c:
        c.execute('insert into quality_runs values(?,?,?,?,?,?,?,?)',
                  (uid(), job['id'], 'plan', 'PASS', '{}', 'h', now(), str(root)))
    out = todo_report.record(repo, job['id'])
    assert out['todo_count'] == 6 and out['todo_html_url'] == '/api/v2/reports/%s/html' % out['todo_report_id']
    row = repo.one('select * from validation_reports where id=?', (out['todo_report_id'],))
    assert row['report_type'] == todo_report.TODO and row['job_id'] == job['id']
    page = todo_report.render_row(row)
    assert 'TODO report' in page and 'OrderService.java' in page and 'Business logic' in page
    assert todo_report.latest(repo, job['id']) == {'report_id': out['todo_report_id'], 'html_url': out['todo_html_url'],
                                                   'todo_count': 6}
    assert todo_report.latest(repo, 'no-such-job') is None


def test_record_never_raises(env, monkeypatch):
    repo, job, _ = env
    monkeypatch.setattr(todo_report, 'collect', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    assert todo_report.record(repo, job['id']) == {'todo_error': 'RuntimeError:boom'}
