"""HTML reports for the static comparison checks (parity and hardcoding).

The two skills in ``agent-skills/iib-to-ms-variance-analysis/references`` return one JSON object
and say so explicitly: *"No HTML ... The server owns presentation -- your job is only the data."*
This module is that presentation. It turns the stored report into one self-contained HTML page: no
script, no external resource, a single inline stylesheet, so the file can be opened from disk,
mailed, or served by the API under a strict Content-Security-Policy.

Everything in the report came from a model, so every value is escaped and nothing in it is ever
interpreted as markup. The report's own ``counts`` are not trusted either: the figures shown are
recomputed from the rows, and a disagreement is stated on the page instead of being smoothed over.
"""
import html
import json
from collections import Counter

#: action -> (page title, the order its areas are listed in, short note on what it covers)
CHECKS = {
    'parity_check': (
        'Parity report',
        ['Field Mappings', 'Integration Points', 'Validation', 'Transaction Flow', 'Error Handling'],
        'Row-by-row comparison of the IIB source against every migrated file, with the code or '
        'configuration value cited on each side.'),
    'hardcoding_check': (
        'Hardcoding report',
        ['Transaction Flow', 'Integration Points', 'Field Mappings'],
        'Comparison of the IIB source against the migrated Java source only: transaction flow, '
        'integration points and field mappings. Configuration files are out of scope.'),
}
FILENAMES = {'parity_check': 'parity-report.html', 'hardcoding_check': 'hardcoding-report.html'}
STATUSES = ('MATCH', 'PARTIAL', 'GAP')
STATUS_RANK = {s: i for i, s in enumerate(STATUSES)}
SEVERITIES = ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')
PRIORITIES = ('HIGH', 'MEDIUM', 'LOW')

#: What the API sends with this page: nothing may load and nothing may run; only the inline style.
CONTENT_SECURITY_POLICY = ("default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                           "form-action 'none'; frame-ancestors 'none'")

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2430;--muted:#5d6b7c;--line:#dde2e8;--match:#1a7f4b;--match-bg:#e3f4ea;
--partial:#9a6700;--partial-bg:#fdf1d6;--gap:#b42318;--gap-bg:#fde7e4;--other:#475467;--other-bg:#eaecf0;--code:#f1f3f6}
@media (prefers-color-scheme:dark){:root{--bg:#0f141a;--card:#171e27;--ink:#e6eaf0;--muted:#97a3b3;--line:#2a3441;
--match:#5ad19a;--match-bg:#10301f;--partial:#f0c05a;--partial-bg:#33280b;--gap:#ff8a7d;--gap-bg:#3a1713;
--other:#b3bcc9;--other-bg:#252d38;--code:#0f141a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1280px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}h2 small{color:var(--muted);font-weight:400;margin-left:8px}
.sub{color:var(--muted);margin:0 0 16px}.note{color:var(--muted);font-size:13px;margin:4px 0 0}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 18px;min-width:120px}
.tile b{display:block;font-size:26px;line-height:1.1}.tile span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tile.MATCH b{color:var(--match)}.tile.PARTIAL b{color:var(--partial)}.tile.GAP b{color:var(--gap)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 18px}
.card p{margin:.4em 0}
.banner{border:1px solid var(--partial);background:var(--partial-bg);color:var(--ink);border-radius:8px;padding:10px 14px;margin:12px 0}
table{width:100%;border-collapse:collapse;table-layout:fixed;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);overflow-wrap:anywhere;word-break:break-word}
th{background:var(--other-bg);font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:0}
code{display:block;background:var(--code);border-radius:6px;padding:4px 6px;margin-top:4px;font:12px/1.4 ui-monospace,Consolas,monospace;white-space:pre-wrap}
code.none{color:var(--muted);font-style:italic}
.src{color:var(--muted);font-size:12px}
.badge{display:inline-block;border-radius:999px;padding:1px 10px;font-size:12px;font-weight:600}
.MATCH{background:var(--match-bg);color:var(--match)}.PARTIAL{background:var(--partial-bg);color:var(--partial)}
.GAP,.CRITICAL{background:var(--gap-bg);color:var(--gap)}.HIGH{background:var(--gap-bg);color:var(--gap)}
.MEDIUM{background:var(--partial-bg);color:var(--partial)}.LOW,.OTHER{background:var(--other-bg);color:var(--other)}
.empty{color:var(--muted);font-style:italic}
footer{margin-top:32px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
footer dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;margin:6px 0}footer dt{font-weight:600}footer dd{margin:0;overflow-wrap:anywhere}
@media print{body{background:#fff}.tile,.card,table{break-inside:avoid}}
""".strip()


def _text(value):
    """Any value a model might put in a field, as plain text."""
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _e(value):
    return html.escape(_text(value), quote=True)


def _badge(value, allowed=STATUSES + SEVERITIES):
    label = _text(value).strip().upper()
    css = label if label in allowed else 'OTHER'
    return '<span class="badge %s">%s</span>' % (css, _e(label or '—'))


def _code(value, muted_if=('— not implemented —', '')):
    text = _text(value)
    if text.strip() in muted_if:
        return '<code class="none">%s</code>' % (_e(text) or '—')
    return '<code>%s</code>' % _e(text)


def _dicts(items):
    return [x for x in (items if isinstance(items, list) else []) if isinstance(x, dict)]


def _table(headers, rows, widths=None):
    cols = ''.join('<col style="width:%s">' % w for w in widths) if widths else ''
    head = ''.join('<th>%s</th>' % _e(h) for h in headers)
    body = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % c for c in r) for r in rows)
    return '<table>%s<thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (
        '<colgroup>%s</colgroup>' % cols if cols else '', head, body)


def _grouped(rows, area_order):
    """Rows by area, in the skill's area order (unknown areas after), MATCH then PARTIAL then GAP."""
    groups = {}
    for r in rows:
        groups.setdefault(_text(r.get('area')).strip() or 'Unspecified', []).append(r)
    known = [a for a in area_order if a in groups]
    rest = sorted(a for a in groups if a not in area_order)
    for area in known + rest:
        ordered = sorted(groups[area], key=lambda r: STATUS_RANK.get(_text(r.get('status')).upper(), 9))
        yield area, ordered


def _tile(label, value, css=''):
    return '<div class="tile %s"><b>%s</b><span>%s</span></div>' % (_e(css), _e(value), _e(label))


def _counts_section(report, rows):
    computed = Counter(_text(r.get('status')).strip().upper() for r in rows)
    shown = {s: computed.get(s, 0) for s in STATUSES}
    claimed = report.get('counts') if isinstance(report.get('counts'), dict) else {}
    claimed = {s: claimed.get(s.lower()) for s in STATUSES}
    tiles = [_tile('Rows compared', len(rows))] + [_tile(s.title(), shown[s], s) for s in STATUSES]
    other = sum(v for k, v in computed.items() if k not in STATUSES)
    if other:
        tiles.append(_tile('Unrecognised status', other))
    banner = ''
    if any(claimed[s] is not None and claimed[s] != shown[s] for s in STATUSES):
        banner = ('<div class="banner">The report\'s own counts (%s) do not match its rows. '
                  'The figures above are recomputed from the rows.</div>'
                  % ', '.join('%s %s' % (s.title(), _e(claimed[s])) for s in STATUSES))
    return '<div class="tiles">%s</div>%s' % (''.join(tiles), banner)


def _summary(report):
    paragraphs = report.get('executive_summary')
    if not isinstance(paragraphs, list):                 # a lone string, or anything else a model might send
        paragraphs = [paragraphs]
    paragraphs = [p for p in paragraphs if _text(p).strip()]
    if not paragraphs:
        return ''
    return '<h2>Summary</h2><div class="card">%s</div>' % ''.join('<p>%s</p>' % _e(p) for p in paragraphs)


def _parity_rows(rows, area_order):
    out = []
    for area, group in _grouped(rows, area_order):
        trs = [[
            '<div class="src">%s</div>%s' % (_e(r.get('iib_source')), _code(r.get('iib_value'))),
            '<div class="src">%s</div>%s' % (_e(r.get('ms_source')), _code(r.get('ms_value'))),
            _badge(r.get('status')),
            _e(r.get('remarks'))] for r in group]
        out.append('<h2>%s<small>%d</small></h2>%s' % (_e(area), len(group), _table(
            ['IIB (source · value)', 'Microservice (class · line)', 'Status', 'Remarks'], trs,
            ['30%', '30%', '9%', '31%'])))
    return ''.join(out) or '<p class="empty">The report contains no comparison rows.</p>'


def _hardcoding_rows(rows, area_order):
    out = []
    for area, group in _grouped(rows, area_order):
        trs = [[_e(r.get('category')), _badge(r.get('status')), _e(r.get('remarks'))] for r in group]
        out.append('<h2>%s<small>%d</small></h2>%s' % (_e(area), len(group), _table(
            ['Category', 'Status', 'Remarks'], trs, ['24%', '10%', '66%'])))
    return ''.join(out) or '<p class="empty">The report contains no comparison rows.</p>'


def _findings(report):
    findings = _dicts(report.get('findings'))
    if not findings:
        return ''
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    findings = sorted(findings, key=lambda f: rank.get(_text(f.get('severity')).upper(), 9))
    by_severity = Counter(_text(f.get('severity')).strip().upper() for f in findings)
    tiles = ''.join(_tile(s.title(), by_severity.get(s, 0), s) for s in SEVERITIES)
    trs = [[_e(f.get('id')), _badge(f.get('severity')), _badge(f.get('status')), _e(f.get('area')),
            '<div class="src">%s</div><div class="src">%s</div>' % (_e(f.get('iib_source')), _e(f.get('ms_source'))),
            _e(f.get('description')), _e(f.get('fix_hint'))] for f in findings]
    return '<h2>Findings<small>%d</small></h2><div class="tiles">%s</div>%s' % (
        len(findings), tiles, _table(['ID', 'Severity', 'Status', 'Area', 'Where', 'Description', 'Fix'], trs,
                                     ['6%', '9%', '8%', '11%', '22%', '24%', '20%']))


def _suggestions(report):
    items = _dicts(report.get('suggestions'))
    if not items:
        return '<h2>Suggestions</h2><p class="empty">No actions suggested.</p>'
    rank = {p: i for i, p in enumerate(PRIORITIES)}
    items = sorted(items, key=lambda s: rank.get(_text(s.get('priority')).upper(), 9))
    trs = [[_badge(s.get('priority'), PRIORITIES), _e(s.get('issue')), _e(s.get('recommendation'))] for s in items]
    return '<h2>Suggestions<small>%d</small></h2>%s' % (
        len(items), _table(['Priority', 'Issue', 'Recommendation'], trs, ['9%', '39%', '52%']))


def _footer(meta):
    labels = [('report_id', 'Report'), ('job_id', 'Job'), ('generated_at', 'Generated'), ('mode', 'Produced by'),
              ('model', 'Model'), ('prompt', 'Prompt'), ('skill', 'Guidance')]
    items = ''.join('<dt>%s</dt><dd>%s</dd>' % (_e(label), _e(meta[key])) for key, label in labels if meta.get(key))
    return ('<footer><div>Generated by a model from the job\'s own IIB source and migrated code. It is advisory: '
            'it is not recorded as a variance and does not open or close one.</div><dl>%s</dl></footer>' % items)


def render_html(report, meta=None):
    """One self-contained HTML page for a ``parity_check`` or ``hardcoding_check`` report.

    ``meta`` is optional provenance for the footer (report_id, job_id, generated_at, mode, model,
    prompt, skill); the report itself is never required to carry any.
    """
    if not isinstance(report, dict) or report.get('action') not in CHECKS:
        raise ValueError('unsupported_report_action:%s' % (report.get('action') if isinstance(report, dict) else type(report).__name__))
    action = report['action']
    title, area_order, covers = CHECKS[action]
    meta = meta or {}
    rows = _dicts(report.get('rows'))
    app = _text(report.get('application_name')).strip()
    pair = ' → '.join(x for x in (_text(report.get('iib_component')).strip(), _text(report.get('ms_component')).strip()) if x)
    files = ' · '.join('%s %s files' % (_e(report.get(k)), label) for k, label in
                       (('iib_file_count', 'IIB'), ('ms_file_count', 'migrated')) if report.get(k) not in (None, ''))
    sub = ' · '.join(x for x in (_e(pair), files) if x)
    rows_html = (_parity_rows if action == 'parity_check' else _hardcoding_rows)(rows, area_order)
    body = [
        '<h1>%s%s</h1>' % (_e(title), ' — ' + _e(app) if app else ''),
        '<p class="sub">%s</p>' % (sub or '&nbsp;'),
        '<p class="note">%s</p>' % _e(covers),
        _counts_section(report, rows),
        _summary(report),
        rows_html,
        _findings(report) if action == 'parity_check' else '',
        _suggestions(report),
        _footer(meta),
    ]
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex"><title>%s%s</title><style>%s</style></head>'
            '<body><main>%s</main></body></html>' % (_e(title), ' — ' + _e(app) if app else '', CSS, ''.join(body)))


def filename(action):
    return FILENAMES[action]


def meta_from_stored(stored, row):
    """Footer provenance from a stored ``validation_reports`` row (``stored`` is its parsed payload)."""
    prov = stored.get('provenance') or {}
    prompt = prov.get('prompt_name', '')
    if prompt and prov.get('prompt_version'):
        prompt += ' v%s' % prov['prompt_version']
    skill = prov.get('skill_name', '')
    if skill and prov.get('skill_hash'):
        skill += ' @ %s' % str(prov['skill_hash'])[:12]
    return {'report_id': row.get('id', ''), 'job_id': row.get('job_id', ''), 'generated_at': row.get('created_at', ''),
            'mode': prov.get('mode', ''), 'model': prov.get('provider_model') or prov.get('model_id', ''),
            'prompt': prompt, 'skill': skill}
