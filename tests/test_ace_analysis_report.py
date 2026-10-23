"""The ACE analysis HTML report: same template as the hand-over document, different data.

The renderer is pure -- semantic model and repository files in, one HTML string out -- so most of
this needs no database. One test runs the real agent to prove the report is written beside the
pipeline's other reports and that a failure to write it cannot fail the analysis.
"""
import re
from html.parser import HTMLParser

import pytest

from app.services import ace_analysis_report as R
from app.services import ace_report_facts as F
from app.services.enterprise_v5 import ACEParser
from tests import ace_report_fixture as fx


@pytest.fixture
def repo_dir(tmp_path):
    return fx.write(tmp_path / 'getDispositionReferenceData')


@pytest.fixture
def report(repo_dir):
    parser = ACEParser()
    files = [(str(f.relative_to(repo_dir)), f.stat().st_size) for f in sorted(repo_dir.rglob('*')) if f.is_file()]
    return F.Report(parser.parse(repo_dir), parser.sources(repo_dir), files, {'date': '22 Oct 2026', 'branch': 'Path1'})


@pytest.fixture
def page(report):
    return R.render_html(report)


def text_of(page):
    class Grab(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)
    grab = Grab()
    grab.feed(page)
    return ' '.join(' '.join(grab.parts).split())


# ---- the template -----------------------------------------------------------------------------

def test_the_page_follows_the_template_section_order(page):
    positions = [page.index(h) for h in (
        'IBM Integration Bus Application Analysis', 'Evidence legend used throughout', '>Contents<', 'Executive summary',
        'Application structure and file inventory', 'Flow 1: getDispositionReferenceData', 'Review findings register',
        'Behavioural contract for the microservice team', 'Application continuity tracker')]
    assert positions == sorted(positions)


def test_every_flow_chapter_has_the_fifteen_numbered_sections_in_order(page):
    found = [int(m.group(1)) for m in re.finditer(r'<h3 id="sec-1-(\d+)">(\d+)\. ', page)]
    assert found == list(range(1, 16))
    titles = re.findall(r'<h3 id="sec-1-\d+">\d+\. ([^<]+)</h3>', page)
    assert titles == list(R.SECTIONS)


def test_the_contents_links_to_every_section(page):
    for k in range(1, 16):
        assert 'href="#sec-1-%d"' % k in page and 'id="sec-1-%d"' % k in page
    assert 'href="#findings"' in page and 'id="findings"' in page


def test_cover_carries_the_same_rows_and_legend_as_the_template(page):
    text = text_of(page)
    for label in ('Repository', 'Branch analysed', 'Analysis date', 'Purpose', 'Runtime assumed'):
        assert label in text
    assert '[C] Confirmed' in text and '[I] Inferred' in text and '[N] Not in repo' in text
    assert 'Flow 1 – getDispositionReferenceData (REST API to SOAP facade)' in text


def test_the_palette_and_fonts_are_the_templates(page):
    for token in ('#14325c', '#13315b', '#edf2f9', '#b8c4d6', '#2a5db0', 'Consolas', 'Arial'):
        assert token in page


# ---- the data ---------------------------------------------------------------------------------

def test_executive_summary_is_read_from_the_flow(page):
    text = text_of(page)
    assert '/pbmpharmacy_24/api/v1/edb/opportunitydispositioncode*' in text
    assert 'client time-out 55s' in text
    assert 'additionalInstances: 10' in text
    assert '4 BAR override files: DEV, QA, UAT, PROD' in text
    assert 'com.cvscm.esd.common.util.ESDExceptionListUtil' in text
    assert 'getBrokerErrorShortDescription' not in text.split('Hidden dependency')[1].split('things to know')[0]


def test_the_bar_override_wins_over_the_design_time_out(page):
    text = text_of(page)
    assert 'server time-out 30 s (BAR override; design value 60 s)' in text


def test_esql_is_explained_statement_by_statement_with_line_numbers(report):
    esql = report.esqls[0]
    assert set(esql.modules) == {'getDispositionReferenceData_Request', 'getDispositionReferenceData_Response',
                                 'getDispositionReferenceData_Failure', 'getDispositionReferenceData_Error'}
    by_text = {s['text']: s for s in esql.statements}
    external = by_text["DECLARE EOMSurl EXTERNAL CHARACTER '';"]
    assert external['start'] == 1 and 'user-defined property' in external['meaning']
    # a module header has no semicolon and must not swallow the statement after it
    header = next(s for s in esql.statements if s['text'].startswith('CREATE COMPUTE MODULE getDispositionReferenceData_Request'))
    assert header['text'] == 'CREATE COMPUTE MODULE getDispositionReferenceData_Request'
    assert header['kind'] == 'MODULE'


def test_local_environment_written_as_output_is_one_name(report):
    esql = report.esqls[0]
    targets = {t for _, t, _ in esql.sets()}
    assert 'LocalEnvironment.Destination.HTTP.RequestURL' in targets
    assert not any(t.startswith('OutputLocalEnvironment') for t in targets)


def test_compute_nodes_are_bound_to_their_esql_modules(report):
    chapter = report.chapters[0]
    assert {n['label'] for _, n in chapter.bindings['getDispositionReferenceData_Request']} == {'DIMtoHEE'}
    # the three handler subflows and ComposeHEE_Failure all run the same module
    assert len(chapter.bindings['getDispositionReferenceData_Failure']) == 4


def test_the_example_response_is_derived_from_the_esql(page):
    text = text_of(page)
    assert '"codeTables": [' in text and '"productId": "<PresentationChannelProductIdentifier>"' in text
    assert '"httpMessage": "Service Error"' in text


def test_flow_walkthrough_lists_every_node_with_its_configuration(page):
    text = text_of(page)
    for label in ('HTTP Input', 'Route To Label', 'HTTP Reply', 'DIMtoHEE', 'InvokeHEE', 'HEEtoDIM'):
        assert label in text
    assert 'timeoutForClient: 55' in text and 'sslProtocol: TLSv1.2' in text


def test_paths_follow_the_wired_terminals(page):
    text = text_of(page)
    assert 'HTTP Input catch terminal' in text and 'InvokeHEE error terminal' in text
    assert 'ComposeHEE_Error' in text


def test_the_bar_overrides_and_environment_matrix_come_from_the_properties_files(page):
    text = text_of(page)
    assert 'getDispositionReferenceData#InvokeHEE.timeoutForServer' in text
    assert 'dev1-eoms.caremark.com' in text and 'eoms.caremark.com' in text
    assert 'Identical to UAT' in text


def test_what_is_not_in_the_repository_is_named(page):
    text = text_of(page)
    assert 'Not in the repository [N]' in text
    assert 'the Java jar containing com.cvscm.esd.common.util.ESDExceptionListUtil' in text
    assert 'integration-server configuration' in text


# ---- findings ---------------------------------------------------------------------------------

def findings_by_text(report, needle):
    return [f for f in report.findings if needle in f['finding']]


def test_a_stub_url_in_production_is_a_high_finding(report):
    found, = findings_by_text(report, 'test-harness / stub URL')
    assert found['sev'] == 'High' and 'PROD' in found['finding']


def test_an_environment_pointing_at_another_tier_is_flagged(report):
    found = findings_by_text(report, 'looks like another environment')
    assert any('UAT' in f['finding'] and 'sit1-eoms' in f['finding'] for f in found)


def test_a_java_dependency_outside_the_repository_is_flagged(report):
    found, = findings_by_text(report, 'Runtime depends on Java class')
    assert found['sev'] == 'Med' and 'ESDExceptionListUtil' in found['finding']


def test_dead_code_is_flagged_without_false_positives_on_local_environment(report):
    unused = findings_by_text(report, 'Unused variables')[0]['finding']
    assert 'neverUsed' in unused
    dead = findings_by_text(report, 'Written but never read')[0]['finding']
    assert 'Environment.Variables.orphan' in dead
    assert 'RequestURL' not in dead and 'ReplyStatusCode' not in dead


def test_identical_handler_subflows_are_reported_once(report):
    found = findings_by_text(report, 'Failure terminal not wired')
    assert len(found) == 1 and '3 subflows' in found[0]['finding']


def test_findings_are_numbered_in_order_and_open_questions_are_carried(repo_dir):
    parser = ACEParser()
    model = parser.parse(repo_dir)
    model['ambiguities'].append({'status': 'OPEN', 'question': 'Is levelNumber a string?', 'owner': 'ACE_SME',
                                 'impact': 'manual mapping required', 'evidenceReference': 'esql L102'})
    rep = F.Report(model, parser.sources(repo_dir), [], {})
    assert [f['id'] for f in rep.findings] == ['F%d' % i for i in range(1, len(rep.findings) + 1)]
    question, = [f for f in rep.findings if 'levelNumber' in f['finding']]
    assert question['sev'] == 'Med' and question['evidence'] == 'esql L102'


def test_unsupported_constructs_become_findings(repo_dir):
    parser = ACEParser()
    rep = F.Report(parser.parse(repo_dir), parser.sources(repo_dir), [], {}, unsupported=[
        {'construct_type': 'VendorCustomNode', 'classification': 'UNSUPPORTED', 'evidence_ref': 'V.msgflow#n1',
         'impact': 'Manual mapping or plug-in development required'}])
    found, = [f for f in rep.findings if 'VendorCustomNode' in f['finding']]
    assert found['evidence'] == 'V.msgflow#n1'


# ---- safety and shape of the output -------------------------------------------------------------

def test_values_from_the_repository_are_escaped(tmp_path):
    root = tmp_path / 'evil'
    root.mkdir()
    (root / 'X.msgflow').write_text(
        '<flow><node id="n1" type="HTTPInput" URLSpecifier="&lt;script&gt;alert(1)&lt;/script&gt;"/></flow>')
    (root / 'X.esql').write_text("CREATE COMPUTE MODULE <img src=x onerror=alert(1)>;\n")
    parser = ACEParser()
    page = R.build(parser.parse(root), parser.sources(root), [('<b>x</b>.msgflow', 1)], {'repository': '<i>r</i>'})
    assert '<script' not in page.lower().replace('<style>', '')
    assert '<img src=x' not in page and '<b>x</b>' not in page and '<i>r</i>' not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page


def test_the_page_loads_nothing_and_runs_nothing(page):
    assert '<script' not in page.lower()
    assert not re.search(r'(?i)(src|href)="(https?:)?//', page)
    assert 'default-src \'none\'' in R.CONTENT_SECURITY_POLICY


def test_the_running_footer_text_cannot_inject_css(tmp_path):
    root = tmp_path / 'r'
    root.mkdir()
    (root / 'X.msgflow').write_text('<flow><node id="n" type="HTTPInput"/></flow>')
    parser = ACEParser()
    page = R.build(parser.parse(root), parser.sources(root), [], {'title': 'x"; } body{display:none} /*'})
    foot = re.search(r'--foot:"([^"]*)"', page).group(1)
    assert '"' not in foot and '}' not in foot and ';' not in foot


def test_an_empty_analysis_still_renders_the_template(tmp_path):
    root = tmp_path / 'empty'
    root.mkdir()
    page = R.build(ACEParser().parse(root), [], [], {'date': 'today'})
    text = text_of(page)
    assert 'IBM Integration Bus Application Analysis' in text
    assert 'No message flow was found' in text


def test_a_second_flow_gets_its_own_chapter(repo_dir):
    (repo_dir / 'Other.msgflow').write_text(fx.flow(
        [fx.node('i', 'ComIbmMQInput.msgnode:FCMComposite_1', 'Orders In', queueName='ORDERS.IN',
                 queueManagerName='QM1', transactionMode='yes'),
         fx.node('o', 'ComIbmMQOutput.msgnode:FCMComposite_1', 'Orders Out', queueName='ORDERS.OUT')],
        [fx.wire('i', 'out', 'o')]), encoding='utf-8')
    parser = ACEParser()
    page = R.build(parser.parse(repo_dir), parser.sources(repo_dir), [], {})
    assert 'Flow 2: Other' in text_of(page)
    assert 'ORDERS.IN' in page and 'ORDERS.OUT' in page


def test_source_without_the_xml_still_reports_what_the_model_holds():
    model = {'flows': [{'path': 'A.msgflow', 'nodes': [{'id': 'in', 'type': 'HTTPInput', 'properties': {'url': '/a'}},
                                                       {'id': 'out', 'type': 'HTTPReply', 'properties': {}}],
                        'connections': [{'from': 'in', 'to': 'out'}]}],
             'subflows': [], 'esql': [], 'maps': [], 'schemas': [], 'policies': [], 'interfaces': [],
             'dependencies': [], 'behaviour': {}, 'ambiguities': []}
    page = R.build(model, [], [], {})
    assert 'Flow 1: A' in text_of(page) and 'HTTP Input' in page


# ---- a model-authored analysis -------------------------------------------------------------------
#
# A real run's semantic model is written by the model, file by file. It keeps structure (nodes, wires,
# modules) but not the files' text, and it names a node in properties.name. The deterministic parse used by
# the tests above keeps the text, which hid this: the first real report had `FCMComposite_1_1` for every
# node, "0 lines" for every ESQL file and no wiring analysis at all.

def model_authored(model):
    """The same model as a model-authored one is stored: no raw ``source`` text anywhere."""
    out = {k: v for k, v in model.items()}
    for area in ('flows', 'subflows', 'esql', 'maps', 'schemas'):
        out[area] = [{k: v for k, v in item.items() if k != 'source'} for item in model.get(area, [])]
    return out


@pytest.fixture
def stripped(repo_dir):
    parser = ACEParser()
    model = parser.parse(repo_dir)
    # a model names its nodes in properties.name and wires terminals as sourceTerminal, as the real one did
    for flow in model['flows'] + model['subflows']:
        for node in flow['nodes']:
            node['properties'].setdefault('name', node['id'])
    return model_authored(model), parser.sources(repo_dir)


def test_without_the_model_text_the_repository_files_supply_it(stripped):
    model, sources = stripped
    rep = F.Report(model, sources, [], {})
    esql = rep.esqls[0]
    assert len(esql.statements) > 20 and esql.java == ['com.cvscm.esd.common.util.ESDExceptionListUtil']
    chapter = rep.chapters[0]
    assert 'HTTP Input' in [n['label'] for n in chapter.graph.nodes]
    assert chapter.graph.terminals_known
    assert {f['finding'] for f in rep.findings if 'Failure terminal not wired' in f['finding']}


def test_the_repository_text_is_matched_by_path_whatever_the_separator(stripped):
    model, sources = stripped
    for item in model['esql'] + model['flows'] + model['subflows']:
        item['path'] = item['path'].replace('/', '\\').upper()
    rep = F.Report(model, sources, [], {})
    assert rep.esqls[0].statements and rep.chapters[0].graph.terminals_known


def test_with_no_files_at_all_the_model_alone_still_names_nodes_and_wires(repo_dir):
    model = ACEParser().parse(repo_dir)
    for flow in model['flows'] + model['subflows']:
        flow.pop('source', None)
        for node in flow['nodes']:
            node['properties']['name'] = 'Named ' + node['id']
        for wire_ in flow['connections']:
            wire_['sourceTerminal'] = 'OutTerminal.out'
    model = model_authored(model)
    rep = F.Report(model, [], [], {})
    labels = [n['label'] for n in rep.chapters[0].graph.nodes]
    assert labels and all(label.startswith('Named ') for label in labels)
    assert rep.chapters[0].graph.terminals_known


def test_the_same_unsupported_construct_is_one_finding_with_a_count(repo_dir):
    parser = ACEParser()
    rows = [{'construct_type': 'eflow:FCMSource', 'classification': 'UNSUPPORTED', 'impact': 'manual',
             'evidence_ref': 'sub%d#in' % i, 'source_path': 'sub%d' % i} for i in range(4)]
    rep = F.Report(parser.parse(repo_dir), parser.sources(repo_dir), [], {}, unsupported=rows)
    found, = [f for f in rep.findings if 'eflow:FCMSource' in f['finding']]
    assert '4 occurrences' in found['finding'] and found['evidence'].endswith('and 1 more')


def test_the_environment_matrix_shows_the_values_that_differ(repo_dir):
    for env, suffix in (('DEV', '.P1'), ('QA', '.P2')):
        path = repo_dir / ('getDispositionReferenceData.%s.properties' % env)
        path.write_text(path.read_text() + 'GET#toRxClaim.queueName=ESL.REQ%s\n' % suffix)
    parser = ACEParser()
    page = R.build(parser.parse(repo_dir), parser.sources(repo_dir), [], {})
    text = text_of(page)
    assert 'Values that differ between environments' in text
    assert 'toRxClaim.queueName = ESL.REQ.P1' in text and 'toRxClaim.queueName = ESL.REQ.P2' in text
