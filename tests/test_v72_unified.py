from pathlib import Path

def test_v72_unified_ui_contains_governance():
    root=Path(__file__).parents[1]
    js=(root/'app/static/app.js').read_text()
    html=(root/'app/static/index.html').read_text()
    server=(root/'app/server.py').read_text()
    assert 'approval-governance' in js
    assert '/api/v7/gates/configure' in js
    assert "'version':'7.2'" in server
    # The Integration Administration Portal page was removed from the UI: GitHub
    # connectivity now has its own dedicated per-user flow, and the connector
    # admin surface it exposed was reachable only through this page. The
    # ConnectorRegistry service and its /api/v2/connectors, /api/v3/connectors
    # and /api/v3/connectors/test endpoints are unaffected -- only this page's
    # UI wiring is gone, which these assertions pin so it is not reintroduced
    # by accident.
    assert 'integration-admin' not in js
    assert 'connectorDialog' not in html
