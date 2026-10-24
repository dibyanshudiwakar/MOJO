"""The PARTIAL and GAP rows of the variance-analysis static reports, as findings.

Variance analysis can compare the IIB source against the migrated code (parity) and
look for hard-coded values in it (hardcoding). Each report stores one row per thing
compared, marked MATCH, PARTIAL or GAP. Those reports are advisory and are not written
to the variance store, so nothing downstream saw them. This reads the latest report of
each kind for a job and returns what is not a MATCH, so root-cause analysis can explain
it and remediation can fix it.
"""
import json

#: report_type -> the short name used on findings and in root-cause scopes.
REPORTS = {'PARITY_ANALYSIS': 'PARITY', 'HARDCODING_ANALYSIS': 'HARDCODING'}
OPEN_STATUSES = ('PARTIAL', 'GAP')
#: A row has no severity of its own; a missing behaviour is worse than a partly matching one.
SEVERITY = {'GAP': 'HIGH', 'PARTIAL': 'MEDIUM'}
#: Findings returned per report, so one enormous report cannot swamp a model request.
MAX_ROWS = 200


def _text(value):
    return '' if value is None else str(value).strip()


def load(repo, job_id):
    """Open rows from the newest report of each kind: ``[{source, area, status, ...}]``."""
    out = []
    for report_type, source in REPORTS.items():
        row = repo.one('select id,payload from validation_reports where job_id=? and report_type=? '
                       "and status='ANALYSED' order by created_at desc limit 1", (job_id, report_type))
        if not row:
            continue
        try:
            report = (json.loads(row['payload'] or '{}').get('report')) or {}
        except (TypeError, ValueError):
            continue
        hints = _hints(report)
        for r in (report.get('rows') or []):
            if not isinstance(r, dict) or _text(r.get('status')).upper() not in OPEN_STATUSES:
                continue
            status = _text(r.get('status')).upper()
            area = _text(r.get('area')) or 'Unspecified'
            out.append({
                'report_id': row['id'], 'source': source, 'area': area, 'status': status,
                'severity': SEVERITY[status], 'category': _text(r.get('category')),
                'iib_source': _text(r.get('iib_source')), 'iib_value': _text(r.get('iib_value')),
                'ms_source': _text(r.get('ms_source')), 'ms_value': _text(r.get('ms_value')),
                'remarks': _text(r.get('remarks')),
                'fix_hint': hints.get(area, '')})
            if len(out) >= MAX_ROWS:
                return out
    return out


def _hints(report):
    """The report's own suggested fix per area, from its findings and suggestions."""
    hints = {}
    for f in (report.get('findings') or []):
        if isinstance(f, dict) and _text(f.get('fix_hint')):
            hints.setdefault(_text(f.get('area')) or 'Unspecified', _text(f.get('fix_hint')))
    return hints


def groups(rows):
    """``{(source, area): [rows]}`` in a stable order."""
    out = {}
    for r in rows:
        out.setdefault((r['source'], r['area']), []).append(r)
    return dict(sorted(out.items()))


def label(source, area):
    """The kind shown for a group: ``PARITY · Field Mappings``."""
    return '%s · %s' % (source, area)
