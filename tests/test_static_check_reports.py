"""The HTML presentation of the parity and hardcoding reports.

The skills return JSON and leave presentation to the server. These pin what that presentation owes:
every model-supplied value is escaped, the page runs nothing and loads nothing, the figures shown come
from the rows rather than from the model's own tally, and a malformed report degrades instead of crashing.
"""
from html.parser import HTMLParser

import pytest

from app.services import static_check_reports as reports

PARITY = {
    'action': 'parity_check', 'application_name': 'CarrierNameSearch', 'iib_component': 'CarrierNameSearch',
    'ms_component': 'carriernamesearch-oci', 'iib_file_count': 4, 'ms_file_count': 9,
    'executive_summary': ['Mostly ready.', 'Two gaps remain.'],
    'counts': {'match': 2, 'partial': 1, 'gap': 1},
    'severity_counts': {'critical': 1, 'high': 0, 'medium': 0, 'low': 0},
    'rows': [
        {'area': 'Error Handling', 'iib_source': 'E.esql:9', 'iib_value': 'THROW 408', 'ms_source': 'Svc.java:30',
         'ms_value': 'return 408;', 'status': 'MATCH', 'remarks': 'same'},
        {'area': 'Field Mappings', 'iib_source': 'C.esql:42', 'iib_value': 'facilityID = pharmacy.storeNumber',
         'ms_source': 'CarrierSvc.java:— (field not produced)', 'ms_value': '— not implemented —',
         'status': 'GAP', 'remarks': 'facilityID never populated'},
        {'area': 'Field Mappings', 'iib_source': 'C.esql:88', 'iib_value': 'carrierName = MQMD.carrierName',
         'ms_source': 'CarrierSvc.java:112 (mapResponse)', 'ms_value': 'r.setCarrierName(q.getCarrierName());',
         'status': 'MATCH', 'remarks': 'preserved'},
        {'area': 'Integration Points', 'iib_source': 'M.msgflow', 'iib_value': 'timeout 30000',
         'ms_source': 'application.properties:12', 'ms_value': 'timeout=15000', 'status': 'PARTIAL',
         'remarks': 'shorter timeout'},
    ],
    'findings': [
        {'id': 'P-02', 'severity': 'LOW', 'status': 'PARTIAL', 'area': 'Integration Points',
         'iib_source': 'M.msgflow', 'ms_source': 'application.properties:12', 'description': 'timeout differs',
         'fix_hint': 'set 30000'},
        {'id': 'P-01', 'severity': 'CRITICAL', 'status': 'GAP', 'area': 'Field Mappings',
         'iib_source': 'C.esql:42', 'ms_source': 'CarrierSvc.java:—', 'description': 'facilityID missing',
         'fix_hint': 'map storeNumber'}],
    'suggestions': [{'priority': 'LOW', 'issue': 'timeout', 'recommendation': 'align it'},
                    {'priority': 'HIGH', 'issue': 'facilityID missing', 'recommendation': 'populate it'}],
}

HARDCODING = {
    'action': 'hardcoding_check', 'application_name': 'Orders', 'iib_component': 'Orders', 'ms_component': 'orders-oci',
    'iib_file_count': 3, 'ms_file_count': 5, 'counts': {'match': 1, 'partial': 1, 'gap': 0},
    'rows': [
        {'area': 'Field Mappings', 'category': 'Boolean field encoding', 'status': 'PARTIAL',
         'remarks': 'string instead of native boolean'},
        {'area': 'Transaction Flow', 'category': 'standaloneIndicator routing', 'status': 'MATCH',
         'remarks': 'identical'}],
    'suggestions': [{'priority': 'HIGH', 'issue': 'boolean encoding', 'recommendation': 'emit native booleans'}],
}


class Balanced(HTMLParser):
    """Fails on a mismatched close tag; void elements are not tracked."""
    VOID = {'meta', 'col', 'br', 'hr', 'img', 'input', 'link'}

    def __init__(self):
        super().__init__()
        self.stack, self.problems = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.problems.append(tag)


def render(report, **meta):
    return reports.render_html(report, meta or None)


# ---- content ------------------------------------------------------------------------------------

def test_a_parity_report_shows_both_sides_the_code_values_and_the_summary():
    page = render(PARITY)
    for text in ('Parity report', 'CarrierNameSearch', 'carriernamesearch-oci', '4 IIB files', '9 migrated files',
                 'Mostly ready.', 'Two gaps remain.', 'facilityID = pharmacy.storeNumber',
                 'r.setCarrierName(q.getCarrierName());', 'CarrierSvc.java:112 (mapResponse)',
                 'IIB (source · value)', 'Microservice (class · line)'):
        assert text in page, text


def test_areas_follow_the_skills_order_and_matches_come_before_gaps_within_an_area():
    page = render(PARITY)
    areas = [page.index('<h2>' + a) for a in ('Field Mappings', 'Integration Points', 'Error Handling')]
    assert areas == sorted(areas)
    assert page.index('preserved') < page.index('facilityID never populated')      # MATCH row, then GAP row


def test_findings_are_listed_most_severe_first_and_suggestions_by_priority():
    page = render(PARITY)
    assert page.index('P-01') < page.index('P-02')
    assert page.index('populate it') < page.index('align it')


def test_a_gap_with_no_migrated_value_is_shown_muted_rather_than_blank():
    assert '<code class="none">— not implemented —</code>' in render(PARITY)


def test_a_hardcoding_report_has_the_three_areas_and_no_findings_section():
    page = render(HARDCODING)
    assert 'Hardcoding report' in page and 'Boolean field encoding' in page and 'emit native booleans' in page
    assert page.index('<h2>Transaction Flow') < page.index('<h2>Field Mappings')
    assert '<h2>Findings' not in page and 'IIB (source' not in page


# ---- the figures --------------------------------------------------------------------------------

def test_the_counts_shown_come_from_the_rows_and_a_disagreement_is_stated():
    wrong = dict(PARITY, counts={'match': 9, 'partial': 0, 'gap': 0})
    page = render(wrong)
    assert 'do not match its rows' in page and 'Match 9' in page
    assert '<b>2</b><span>Match</span>' in page                                 # recomputed: two MATCH rows


def test_a_consistent_report_carries_no_mismatch_banner():
    assert 'do not match its rows' not in render(PARITY)


# ---- safety -------------------------------------------------------------------------------------

def test_model_text_is_escaped_everywhere():
    hostile = '<script>alert(1)</script>'
    attack = dict(PARITY, application_name=hostile, executive_summary=[hostile],
                  rows=[{'area': hostile, 'iib_source': hostile, 'iib_value': hostile, 'ms_source': '"><img src=x onerror=alert(1)>',
                         'ms_value': hostile, 'status': hostile, 'remarks': hostile}],
                  findings=[{'id': hostile, 'severity': hostile, 'description': hostile, 'fix_hint': hostile}],
                  suggestions=[{'priority': hostile, 'issue': hostile, 'recommendation': hostile}])
    page = render(attack, report_id=hostile, skill=hostile, model=hostile)
    assert '<script' not in page.replace('<script>alert', '')            # none of ours; the payload only as text
    assert '<script>alert(1)</script>' not in page and '&lt;script&gt;alert(1)&lt;/script&gt;' in page
    assert '<img' not in page and 'onerror=alert(1)>' not in page.replace('onerror=alert(1)&gt;', '')


def test_the_page_loads_nothing_and_runs_nothing():
    for report in (PARITY, HARDCODING):
        page = render(report)
        for forbidden in ('<script', 'src=', 'href=', 'http://', 'https://', '@import', 'url(', '<link', '<iframe'):
            assert forbidden not in page, forbidden


def test_the_csp_for_this_page_allows_only_its_inline_style():
    csp = reports.CONTENT_SECURITY_POLICY
    assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp
    assert 'script-src' not in csp and "'unsafe-eval'" not in csp


def test_the_page_is_well_formed():
    for report in (PARITY, HARDCODING):
        parser = Balanced()
        parser.feed(render(report))
        assert parser.problems == [] and parser.stack == []


# ---- robustness ---------------------------------------------------------------------------------

def test_a_malformed_report_degrades_instead_of_crashing():
    messy = {'action': 'parity_check', 'rows': ['not a dict', None, 7, {'status': 'weird', 'area': None,
                                                                         'iib_value': {'nested': [1, 2]}}],
             'findings': 'nope', 'suggestions': [None], 'counts': 'bad', 'executive_summary': 42}
    page = render(messy)
    assert 'Parity report' in page and 'weird'.upper() in page
    assert '{&quot;nested&quot;: [1, 2]}' in page                              # a nested value shown as text
    assert 'Unrecognised status' in page


def test_an_empty_report_still_renders():
    page = render({'action': 'hardcoding_check'})
    assert 'The report contains no comparison rows.' in page and 'No actions suggested.' in page


@pytest.mark.parametrize('bad', [None, [], 'text', {}, {'action': 'something_else'}])
def test_an_unsupported_report_is_refused(bad):
    with pytest.raises(ValueError, match='unsupported_report_action'):
        reports.render_html(bad)


# ---- provenance ---------------------------------------------------------------------------------

def test_the_footer_names_where_the_report_came_from():
    page = render(PARITY, report_id='rep-1', job_id='job-1', generated_at='2026-10-16T10:00:00+00:00',
                  mode='MODEL_GATEWAY', model='claude-opus-5-5', prompt='agent/variance-analysis/parity-check v1',
                  skill='iib-to-ms-variance-analysis#parity-skill.md @ abcdef123456')
    for text in ('rep-1', 'job-1', 'MODEL_GATEWAY', 'claude-opus-5-5', 'parity-check v1', 'abcdef123456',
                 'advisory'):
        assert text in page, text


def test_meta_is_built_from_a_stored_report():
    row = {'id': 'r1', 'job_id': 'j1', 'created_at': 'T'}
    stored = {'provenance': {'mode': 'MODEL_GATEWAY', 'provider_model': 'm', 'prompt_name': 'p', 'prompt_version': '1',
                             'skill_name': 's#parity-skill.md', 'skill_hash': 'a' * 64}}
    meta = reports.meta_from_stored(stored, row)
    assert meta == {'report_id': 'r1', 'job_id': 'j1', 'generated_at': 'T', 'mode': 'MODEL_GATEWAY', 'model': 'm',
                    'prompt': 'p v1', 'skill': 's#parity-skill.md @ ' + 'a' * 12}
    assert reports.meta_from_stored({}, {})['mode'] == ''


def test_each_check_has_its_own_filename():
    assert reports.filename('parity_check') == 'parity-report.html'
    assert reports.filename('hardcoding_check') == 'hardcoding-report.html'
