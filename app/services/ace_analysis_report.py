"""The ACE analysis report as one self-contained HTML page.

Same template as the hand-over document the migration team already reads (``IIB Application
Analysis``): cover and evidence legend, contents, executive summary, file inventory, then for each
flow the fifteen sections -- business purpose through summary -- followed by the review findings
register, the behavioural contract and the continuity tracker. Only the data differs; it comes from
``ace_report_facts``, which reads it from the analysis itself.

The page has no script and loads nothing: one inline stylesheet, so the file opens from disk, mails
cleanly and can be served under a strict Content-Security-Policy. Everything in it came from
repository files or a model, so every value is escaped and nothing is ever interpreted as markup.
"""
import html
import re
from collections import Counter

from app.services import ace_report_facts as F

FILENAME = 'ace-analysis-report.html'
CONTENT_SECURITY_POLICY = ("default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                           "form-action 'none'; frame-ancestors 'none'")

SECTIONS = ('Business purpose', 'Flow walkthrough', 'Message lifecycle', 'Queues', 'Database interaction',
            'Cache usage', 'External integrations', 'ESQL explanation', 'Configuration', 'Error handling',
            'Runtime behaviour', 'Production considerations', 'Dependency mapping', 'Visual representation',
            'Summary')

CSS = """
@page{size:Letter;margin:.7in .7in .9in;@bottom-left{content:var(--foot);font:9.5px Arial,sans-serif;color:#6b778c}
@bottom-right{content:"Page " counter(page);font:9.5px Arial,sans-serif;color:#6b778c}}
:root{--navy:#14325c;--head:#13315b;--blue:#2a5db0;--zebra:#edf2f9;--grid:#b8c4d6;--muted:#6b778c;--ink:#000;--paper:#fff;--desk:#e9edf3}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--navy:#9db8e6;--head:#1f3d6b;--blue:#7aa6f0;--zebra:#1a2433;
--grid:#34445c;--muted:#8d9ab0;--ink:#e6eaf0;--paper:#121a26;--desk:#0b1119}}
*{box-sizing:border-box}
body{margin:0;background:var(--desk);color:var(--ink);font:12.5px/1.45 Arial,Helvetica,sans-serif}
.sheet{max-width:816px;margin:24px auto;background:var(--paper);padding:.7in .7in .9in;box-shadow:0 1px 6px rgba(0,0,0,.25)}
.sheet>section{break-before:page}.sheet>section:first-child{break-before:auto}
.foot{display:none}
code,pre,.mono{font-family:Consolas,"Cascadia Mono","Courier New",monospace}
code{font-size:11px}
pre{font-size:8.8px;line-height:1.25;margin:6px 0 10px;white-space:pre;overflow-x:auto;tab-size:4}
h1{font-size:34.7px;color:var(--navy);margin:120px 0 4px;line-height:1.1}
.subtitle{font-size:17.3px;color:var(--blue);margin:0 0 20px}
h2{font-size:22.7px;color:var(--navy);margin:0 0 14px}
h2.sub{font-size:20px;margin:22px 0 10px;color:var(--blue)}
h3{font-size:16.7px;color:var(--blue);margin:20px 0 8px}
h4{font-size:13.3px;color:var(--navy);margin:14px 0 6px}
p{margin:6px 0}
table{width:100%;border-collapse:collapse;margin:8px 0 14px;font-size:10.7px;table-layout:fixed}
th{background:var(--head);color:#fff;text-align:left;font-weight:bold;padding:5px 7px;border:.5px solid var(--grid)}
td{padding:5px 7px;vertical-align:top;border:.5px solid var(--grid);overflow-wrap:anywhere}
tr:nth-child(even) td{background:var(--zebra)}
td code{font-size:10px}
ul{margin:6px 0 10px;padding-left:22px}li{margin:5px 0}
h2,h3,h4{break-after:avoid}tr,pre{break-inside:avoid}
.toc{list-style:none;padding:0;margin:0}
.toc li{display:flex;align-items:baseline;gap:6px;margin:0}
.toc a{color:var(--ink);text-decoration:none}
.toc .l1{font-weight:bold;font-size:12.5px;margin:15px 0 0}
.toc .l2{font-size:10.7px;margin:5px 0 0 14px}
.toc .dots{flex:1;border-bottom:1.5px dotted var(--ink);opacity:.55;transform:translateY(-3px)}
.toc .n{font-weight:bold}.toc .l2 .n{font-weight:normal}
.note{color:var(--muted);font-size:11px}
.sev-High{font-weight:bold;color:#b42318}.sev-Med{font-weight:bold;color:#9a6700}.sev-Low{color:var(--muted)}
@media print{body{background:#fff}.sheet{margin:0;padding:0;box-shadow:none;max-width:none}
a{color:inherit}}
@media (max-width:760px){.sheet{margin:0;padding:18px 16px}h1{margin-top:40px;font-size:28px}}
"""


class H(str):
    """Text already safe to put in the page."""


def e(value):
    return value if isinstance(value, H) else html.escape(str(value if value is not None else ''), quote=True)


def code(value):
    return H('<code>%s</code>' % e(value))


def bold(value):
    return H('<b>%s</b>' % e(value))


def join(*parts):
    return H(''.join(e(p) for p in parts))


def lines(*parts):
    return H('<br>'.join(e(p) for p in parts if p not in ('', None)))


def table(headers, rows, widths=None):
    cols = ''.join('<col style="width:%s">' % w for w in widths) if widths else ''
    head = ''.join('<th>%s</th>' % e(h) for h in headers)
    body = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % e(c) for c in row) for row in rows)
    return H('<table>%s<thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (cols, head, body))


def kv(rows, head=('Item', 'Value'), widths=('22%', '78%')):
    return table(head, rows, widths)


def bullets(items):
    return H('<ul>%s</ul>' % ''.join('<li>%s</li>' % e(i) for i in items))


def pre(text):
    return H('<pre>%s</pre>' % e(text))


def para(*parts):
    return H('<p>%s</p>' % ''.join(e(p) for p in parts))


def anchor(*ids):
    return 'sec-' + '-'.join(str(i) for i in ids)


def cfg(node):
    keys = F.KEYS.get(node['kind'])
    props = node['props']
    if keys:
        pairs = [(k, F.prop(props, k)) for k in keys if F.prop(props, k)]
    else:
        pairs = [(k, v) for k, v in sorted(props.items()) if v][:6]
    return lines(*['%s: %s' % (k, F.short(v, 80)) for k, v in pairs]) if pairs else 'none'


def role(node, chapter=None, graph=None):
    kind = node['kind']
    if kind in ('Compute', 'Java Compute') and chapter and graph:
        mod = F.module_for(node, graph, chapter.modules)
        return ('Runs ESQL module %s: builds or transforms the message tree.' % mod if mod
                else 'Runs custom code to build or transform the message tree.')
    if kind == 'Subflow':
        return 'Runs subflow %s.' % F.stem(str(node['type']).split(':')[0])
    return F.ROLE.get(kind, 'Node of type %s.' % node['type'])


def effective(r, node, name, unit=''):
    """A node property as it runs: the BAR override if there is one, with the design value noted when it differs.

    Override keys read ``<flow or subflow>#<node label>.<property>``.
    """
    design = F.prop(node['props'], name)
    found = {v for key, per_env in r.table.items() if key.lower().endswith('#%s.%s' % (node['label'].lower(), name.lower()))
             for v in per_env.values()}
    if not found:
        return design + unit if design else ''
    value = next(iter(found)) if len(found) == 1 else 'varies by environment'
    unit_ = unit if len(found) == 1 else ''
    return ('%s%s (BAR override; design value %s%s)' % (value, unit_, design, unit) if design and design != value
            else value + unit_)


def numbered(rows):
    return [[str(i)] + list(r) for i, r in enumerate(rows, 1)]


def listed(items, empty='none'):
    items = [str(i) for i in items if i]
    return ', '.join(items) if items else empty


def count_kinds(chapter):
    return Counter(n['kind'] for _, n in chapter.nodes)


# ---- ascii diagrams ---------------------------------------------------------------------------

def box(label, sub=''):
    a, b = F.short(label, 26), F.short(sub, 26)
    w = max(len(a), len(b), 8)
    out = ['+' + '-' * (w + 2) + '+', '| ' + a.ljust(w) + ' |']
    if sub:
        out.append('| ' + b.ljust(w) + ' |')
    out.append('+' + '-' * (w + 2) + '+')
    return out


def row_of(boxes, arrow='--->'):
    height = max(len(b) for b in boxes)
    out = []
    for i in range(height):
        parts = []
        for j, b in enumerate(boxes):
            cell = b[i] if i < len(b) else ' ' * len(b[0])
            parts.append(cell.ljust(len(b[0])))
            if j < len(boxes) - 1:
                parts.append((' ' + arrow + ' ') if i == 1 else ' ' * (len(arrow) + 2))
        out.append(''.join(parts).rstrip())
    return out


def chain(graph, per_row=3):
    path = graph.main_path()
    if not path:
        return '(no wired nodes)'
    boxes = [box(graph.label(n), graph.by_id[n]['kind'] if n in graph.by_id and graph.by_id[n]['kind'] != graph.label(n) else '')
             for n in path]
    out = []
    for i in range(0, len(boxes), per_row):
        out += row_of(boxes[i:i + per_row])
        if i + per_row < len(boxes):
            out += ['   |', '   v']
    side = [(e_['from'], e_['out'], e_['to']) for e_ in graph.edges
            if graph.terminals_known and e_['out'] not in ('out', '') and not (e_['from'] in path and e_['to'] in path
                                                                                 and path.index(e_['to']) == path.index(e_['from']) + 1)]
    if side:
        out += ['', 'Other terminals:']
        out += ['  %s --%s--> %s' % (graph.label(a), t, graph.label(b)) for a, t, b in side]
    return '\n'.join(out)


def context(r, c):
    inbound = c.inbound()
    left = box(inbound.get('kind', 'Caller') and 'Consumer / gateway', 'caller [I]')
    mid = box('Integration server', c.name)
    calls = [(n['kind'], F.prop(n['props'], 'URLSpecifier', 'webServiceURL', 'queueName', 'topicName') or '(set at runtime)')
             for _, n in c.calls][:4] or [('', '')]
    right = []
    for kind, where in calls:
        right += box(kind or 'No outbound call', F.short(where, 26)) + ['']
    right = right[:-1]
    h = max(len(left), len(mid), len(right))
    pad = lambda b: b + [' ' * len(b[0])] * (h - len(b))
    left, mid, right = pad(left), pad(mid), pad(right)
    mark = h // 2
    out = []
    for i in range(h):
        a = ' ---> ' if i == mark else '      '
        b = ' ---> ' if i == mark else '      '
        out.append((left[i] + a + mid[i] + b + right[i]).rstrip())
    return '\n'.join(out)


def timeline(c):
    out = ['Client            Integration server                          Backend', '   |']
    for _, n in c.graph.expanded_path(c.subs):
        if n['kind'] in F.INPUT_KINDS:
            out.append('   |  request ----> | %s' % F.short(n['label'], 40))
        elif n['kind'] in F.CALL_KINDS:
            out.append('   |                | %s ---- call ------>|' % F.short(n['label'], 24))
            out.append('   |                |<--------- reply ----|')
        elif n['kind'].endswith('Reply'):
            out.append('   |<--- response -- | %s' % F.short(n['label'], 40))
        else:
            out.append('   |                | %s' % F.short(n['label'], 40))
    return '\n'.join(out)


# ---- the sections -----------------------------------------------------------------------------

def shape_value(source):
    """What to print for the right-hand side of a SET: the literal, or the field it is copied from."""
    source = (source or '').strip()
    if re.fullmatch(r"'[^']*'", source):
        return source.strip("'")
    if re.fullmatch(r'[\w.:*"\[\]-]+', source):
        return '<%s>' % re.sub(r'\[[^\]]*\]|[*:"]', '', source.split('.')[-1])
    return '<computed>'


def json_shape(c):
    """A response skeleton from the ESQL's SETs into OutputRoot.JSON (or the XML body if it builds no JSON).

    An ``Item[n]`` step is how ESQL addresses an array element, so it is drawn as a one-element list.
    """
    for domain in ('JSON', 'XMLNSC'):
        tree, leaves = {}, 0
        for e_ in c.esqls:
            for s_, target, source in e_.sets():
                parts = target.split('.')
                if len(parts) > 2 and parts[0] == 'OutputRoot' and parts[1] == domain:
                    node = tree
                    for p in parts[2:-1]:
                        node = node.setdefault(p, {})
                        if not isinstance(node, dict):
                            break
                    else:
                        if parts[-1] not in node:
                            node[parts[-1]] = shape_value(source)
                            leaves += 1
        if leaves:
            return tree
    return {}


def render_shape(tree, depth=0):
    pad = '  ' * depth
    out = []
    items = list(tree.items())
    for i, (k, v) in enumerate(items):
        comma = ',' if i < len(items) - 1 else ''
        if isinstance(v, dict) and set(v) == {'Item'} and isinstance(v['Item'], dict):
            out += ['%s"%s": [ {' % (pad, k)] + render_shape(v['Item'], depth + 1) + ['%s} ]%s' % (pad, comma)]
        elif isinstance(v, dict):
            out += ['%s"%s": {' % (pad, k)] + render_shape(v, depth + 1) + ['%s}%s' % (pad, comma)]
        else:
            out.append('%s"%s": "%s"%s' % (pad, k, v, comma))
    return out


def sec_purpose(r, c, i):
    ib = c.inbound()
    outs = c.calls
    headers = []
    for e_ in c.esqls:
        headers += e_.headers()
    headers = list(dict.fromkeys(headers))
    out_roots = []
    for e_ in c.esqls:
        for _, t, _ in e_.sets():
            if t.startswith('OutputRoot.') and t.split('.')[1] not in ('Properties', 'HTTPReplyHeader', 'MQMD'):
                out_roots.append('.'.join(t.split('.')[:3]))
    first = ('%s %s' % (ib['kind'], ib['url']) if ib.get('url') else ib.get('kind', 'a trigger'))
    body = [H('<h3 id="%s">1. Business purpose</h3>' % anchor(i, 1)),
            para(bold('What the code does. '), '%s is a %s flow. It starts at %s and %s. The repository does not state '
                 'the business intent, so confirm it with the owning team [I].'
                 % (c.name, c.pattern(), first,
                    'calls %s' % listed(['%s %s' % (n['kind'], F.prop(n['props'], 'URLSpecifier', 'webServiceURL', 'queueName', 'topicName'))
                                         for _, n in outs]) if outs else 'makes no outbound call')),
            table(('Question', 'Answer'), [
                ['Input', join(ib.get('kind', 'none'), ' ', code(ib['url']) if ib.get('url') else '',
                               ' over HTTPS' if ib.get('https') else '',
                               '; message domain %s' % ib['domain'] if ib.get('domain') else '', ' [C]')],
                ['Optional input headers read', listed(headers, 'None read by the ESQL') + (' [C]' if headers else ' [C]')],
                ['Output', ('Message built by the ESQL under %s [C]' % listed(sorted(set(out_roots))[:6]))
                 if out_roots else 'Whatever the reply node serialises [I]'],
                ['Upstream', 'Callers of %s%s [I]' % (ib.get('url') or ib.get('kind', 'the trigger'),
                                                      ', normally through an API gateway' if ib.get('kind') == 'HTTP Input' else '')],
                ['Downstream', listed(['%s %s' % (n['kind'], F.prop(n['props'], 'URLSpecifier', 'webServiceURL', 'queueName', 'topicName'))
                                       for _, n in outs], 'none') + ' [C]'],
            ], ('22%', '78%'))]
    shape = json_shape(c)
    if shape:
        body += [H('<h4>Example response shape (derived from the ESQL assignments to OutputRoot)</h4>'),
                 pre('{\n' + '\n'.join('  ' + x for x in render_shape(shape)) + '\n}'),
                 para(H('<span class="note">Values in angle brackets are the source field each one is copied from; plain '
                        'values are literals set by the ESQL.</span>'))]
    return H(''.join(e(b) for b in body))


def sec_walkthrough(r, c, i):
    g = c.graph
    out = [H('<h3 id="%s">2. Flow walkthrough</h3>' % anchor(i, 2)),
           para('This flow is %s%s. Each file is a set of nodes joined by terminals.'
                % (F.basename(g.path), (' plus %d subflow%s (%s)' % (len(c.subs), '' if len(c.subs) == 1 else 's',
                                                                    listed([F.basename(s.path) for s in c.subs]))) if c.subs else ''))]
    out.append(H('<h4>2a. Top-level flow (%s)</h4>' % e(F.basename(g.path))))
    out.append(table(('#', 'Node', 'Key configuration [C]', 'What it does / how the message changes'),
                     numbered([[n['label'], cfg(n), role(n, c, g)] for n in g.nodes]), ('4%', '20%', '30%', '46%')))
    for s in c.subs:
        out.append(H('<h4>2b. Subflow (%s)</h4>' % e(F.basename(s.path))))
        rows = []
        for n in s.nodes:
            mod = F.module_for(n, s, c.modules) if n['kind'] in F.CODE_KINDS else ''
            conf = cfg(n) if n['kind'] not in F.CODE_KINDS | {'Subflow Input', 'Subflow Output'} else ''
            rows.append([n['label'], lines(n['kind'] + ('; ' + mod + '.Main' if mod else ''), conf if conf != 'none' else ''),
                         role(n, c, s)])
        out.append(table(('#', 'Node (label)', 'Type / ESQL', 'What it does'), numbered(rows), ('4%', '24%', '30%', '42%')))
        if s.udps:
            out.append(para('User-defined properties: ', code(listed(sorted(s.udps))), ' [C]'))
    if c.unwired_groups():
        out.append(para('The failure terminal is not connected on: %s. An exception there propagates to the enclosing '
                        'catch handler. [C]' % F.describe_unwired(c.unwired_groups())))
    rows = []
    for mod, users in c.bindings.items():
        e_ = c.modules.get(mod)
        if not e_:
            continue
        body, local_env, env = set(), set(), set()
        for s_, t, _ in e_.sets():
            if s_['module'] != mod:
                continue
            if t.startswith('OutputRoot.'):
                body.add('.'.join(t.split('.')[1:3]))
            elif t.startswith('LocalEnvironment.'):
                local_env.add('.'.join(t.split('.')[1:4]))
            elif t.startswith('Environment.'):
                env.add('.'.join(t.split('.')[1:3]))
        names = list(dict.fromkeys(n['label'] for _, n in users))
        rows.append(['After ' + (names[0] if len(names) == 1 else '%s (+%d more node%s running %s)' % (
            names[0], len(names) - 1, '' if len(names) == 2 else 's', mod)),
            listed(sorted(body)[:6], '—'), listed(sorted(local_env)[:5], '—'), listed(sorted(env)[:6], '—')])
    if rows:
        out += [H('<h4>2c. What each Compute module writes</h4>'),
                table(('Stage', 'Message body', 'LocalEnvironment', 'Environment (shared scratch)'), rows)]
    out.append(H('<h4>2d. All paths a message can take</h4>'))
    routes = {}
    for gg in c.graphs:
        for path in gg.paths():
            hops = [(gg.label(a), t) for a, t in path]
            odd = [(a, t) for a, t in path if t and t not in F.NORMAL]
            last = gg.by_id.get(path[-1][0], {})
            route = ' > '.join(h[0] for h in hops)
            trigger = ('%s %s terminal' % (gg.label(odd[0][0]), odd[0][1])) if odd else 'Message arrives at %s' % hops[0][0]
            result = ('Reply to caller' if last.get('kind', '').endswith('Reply') else
                      'Message written by %s' % last.get('label') if last.get('kind') in F.CALL_KINDS else
                      'Leaves the %s' % ('subflow' if gg.is_sub else 'flow'))
            entry = routes.setdefault((route, trigger, result), {'odd': bool(odd), 'in': []})
            entry['in'].append(gg.name)
    prow = []
    for n_, ((route, trigger, result), v) in enumerate(routes.items(), 1):
        also = ' (same in %d subflows)' % len(v['in']) if len(v['in']) > 1 else ''
        prow.append(['P%d %s' % (n_, 'Error path' if v['odd'] else 'Normal'), trigger, route + also, result])
    out.append(table(('Path', 'Trigger', 'Route', 'Result to caller'), prow, ('13%', '27%', '38%', '22%'))
               if prow else para('No wired paths were found in the flow files [C].'))
    return H(''.join(e(b) for b in out))


def sec_lifecycle(r, c, i):
    q = [n for _, n in c.of('MQ Input', 'MQ Output', 'MQ Get', 'JMS Input', 'JMS Output')]
    t = [n for _, n in c.of('Kafka Consumer', 'Kafka Producer')]
    ib = c.inbound()
    ends = [n['label'] for _, n in c.nodes if n['kind'].endswith('Reply')] or \
           [n['label'] for _, n in c.of(*F.CALL_KINDS)]
    return H(e(H('<h3 id="%s">3. Message lifecycle</h3>' % anchor(i, 3))) + e(table(('Question', 'Answer'), [
        ['Where does it originate?', '%s%s [C]' % (ib.get('kind', 'unknown trigger'), ' at ' + ib['url'] if ib.get('url') else '')],
        ['Queues used', listed([F.prop(n['props'], 'queueName') or n['label'] for n in q], 'None') + (' [C]')],
        ['Topics', listed([F.prop(n['props'], 'topicName') or n['label'] for n in t], 'None for business data') + ' [C]'],
        ['Endpoints called', lines(*(['%s: %s' % (n['label'], F.prop(n['props'], 'URLSpecifier', 'webServiceURL', 'queueName') or 'set at runtime')
                                      for _, n in c.calls] or ['None']))],
        ['Where does it end?', ('Replies through %s.' % listed(ends)) if ends else 'At the last node of the flow.'],
    ], ('26%', '74%'))))


def sec_queues(r, c, i):
    nodes = [n for _, n in c.of('MQ Input', 'MQ Output', 'MQ Get', 'JMS Input', 'JMS Output')]
    if not nodes:
        rows = [['Input queue / output queue', 'None. There are no MQ or JMS nodes [C].'],
                ['Backout queue / Dead Letter Queue', 'Not applicable.'],
                ['Queue manager', 'None used by the flow.'],
                ['Transaction behaviour', 'No unit of work, no commit, no rollback [C].']]
    else:
        ins = [n for n in nodes if 'Input' in n['kind'] or n['kind'] == 'MQ Get']
        outs = [n for n in nodes if 'Output' in n['kind']]
        rows = [['Input queue', listed([F.prop(n['props'], 'queueName') for n in ins], 'none') + ' [C]'],
                ['Output queue', listed([F.prop(n['props'], 'queueName') for n in outs], 'none') + ' [C]'],
                ['Backout queue / Dead Letter Queue', 'Not set on the nodes; queue-manager defaults apply [I].'],
                ['Queue manager', listed(sorted({F.prop(n['props'], 'queueManagerName') for n in nodes}), 'default') + ' [C]'],
                ['Transaction behaviour', listed(sorted({'%s: %s' % (n['label'], F.prop(n['props'], 'transactionMode') or 'default')
                                                         for n in nodes})) + ' [C]']]
    return H(e(H('<h3 id="%s">4. Queues</h3>' % anchor(i, 4))) + e(kv(rows, ('Item', 'Status'), ('30%', '70%'))))


def sec_database(r, c, i):
    nodes = [n['label'] for _, n in c.of('Database', 'Database Route')]
    dbs = sorted({d for e_ in c.esqls for d in e_.databases})
    sql = [s for e_ in c.esqls for s in e_.statements if s['kind'] == 'SQL']
    head = H('<h3 id="%s">5. Database interaction</h3>' % anchor(i, 5))
    if not (nodes or dbs or sql):
        return H(e(head) + e(para('None [C]. The ESQL contains no PASSTHRU, SELECT, INSERT or stored-procedure call and the '
                                  'flows have no Database node. There are no tables, no commit and no rollback to consider.')))
    rows = [['Database nodes', listed(nodes) + ' [C]'], ['Data sources / tables referenced', listed(dbs) + ' [C]'],
            ['SQL statements in ESQL', lines(*['L%d: %s' % (s['start'], F.short(s['text'], 90)) for s in sql[:10]]) or 'none']]
    return H(e(head) + e(kv(rows, ('Item', 'Detail'), ('30%', '70%'))))


def sec_cache(r, c, i):
    shared = [x for e_ in c.esqls for x in e_.shared]
    gc = any('globalcache' in e_.text.lower() for e_ in c.esqls)
    head = H('<h3 id="%s">6. Cache usage</h3>' % anchor(i, 6))
    if not shared and not gc:
        return H(e(head) + e(para('None [C]. No DECLARE ... SHARED and no Global Cache calls. Every request does its work '
                                  'afresh. Environment is per-message scratch space and is discarded after the reply.')))
    return H(e(head) + e(kv([['Shared variables', listed(['%s %s' % (s['name'], s['declaration']) for s in shared]) + ' [C]'],
                             ['Global Cache', 'Referenced in ESQL [C]' if gc else 'Not used']], ('Item', 'Detail'), ('30%', '70%'))))


def sec_integrations(r, c, i):
    rows = []
    ib = c.inbound()
    if ib:
        rows.append(['Inbound ' + ib['kind'], join('%s listener%s. ' % (ib['kind'], ' (HTTPS)' if ib.get('https') else ''),
                                                   code(ib['url']) if ib.get('url') else '',
                                                   ' Client time-out %ss.' % ib['timeout'] if ib.get('timeout') else '',
                                                   ' Security profile %s.' % ib['security'] if ib.get('security')
                                                   else ' No security profile on the input node [C]; authentication presumed at the gateway [I].')])
    for g, n in c.calls:
        p = n['props']
        rows.append(['Outbound ' + n['kind'] + ' (' + n['label'] + ')', lines(
            'URL / destination: ' + (F.prop(p, 'URLSpecifier', 'webServiceURL', 'queueName', 'topicName') or 'taken from LocalEnvironment at run time [I]'),
            'Method: ' + (F.prop(p, 'httpMethod') or 'node default [I]') if n['kind'] == 'HTTP Request' else '',
            'TLS: ' + F.prop(p, 'sslProtocol') if F.prop(p, 'sslProtocol') else '',
            'Time-out: %s' % effective(r, n, 'timeoutForServer', ' s') if F.prop(p, 'timeoutForServer') else '',            'Validation: ' + F.prop(p, 'validateMaster') if F.prop(p, 'validateMaster') else '')])
    auth = [(n['label'], k) for _, n in c.nodes for k in n['props'] if re.search(r'(?i)security|identity|basicauth|cert', k)]
    rows.append(['Outbound authentication', ('Configured on: ' + listed(sorted({'%s (%s)' % a for a in auth})) + ' [C]') if auth
                 else 'None configured in the flow: no security profile, identity or certificate property [C].'])
    retry = [(n['label'], k) for _, n in c.nodes for k in n['props'] if 'retry' in k.lower()]
    rows.append(['Retry', ('Configured on: ' + listed(sorted({'%s (%s)' % a for a in retry}))) if retry
                 else 'None. A single attempt per request [C].'])
    java = sorted({j for e_ in c.esqls for j in e_.java})
    rows.append(['Java', ('Called from ESQL: ' + listed(java) + ' [C that it is called, N for the code]') if java else 'None [C]'])
    other = c.of('MQ Input', 'MQ Output', 'MQ Get', 'JMS Input', 'JMS Output', 'Kafka Consumer', 'Kafka Producer', 'SFTP', 'FTE')
    rows.append(['MQ / JMS / Kafka / SFTP', listed(sorted({n['kind'] for _, n in other}), 'Not used') + (' [C]' if other else '')])
    return H(e(H('<h3 id="%s">7. External integrations</h3>' % anchor(i, 7))) + e(kv(rows, ('Integration', 'Detail'), ('26%', '74%'))))


def purpose_of(e_, mod):
    sets = [(t, s) for s_, t, s in e_.sets() if s_['module'] == mod]
    text = ' '.join(s['text'] for s in e_.statements if s['module'] == mod)
    facets = []
    if any(t.startswith('OutputRoot.XMLNSC') for t, _ in sets):
        facets.append('builds an XML/SOAP message')
    if any(t.startswith('OutputRoot.JSON') for t, _ in sets):
        facets.append('builds a JSON message')
    if any('RequestURL' in t for t, _ in sets):
        facets.append('chooses the target URL')
    if any('ReplyStatusCode' in t or 'httpCode' in t for t, _ in sets):
        facets.append('sets the reply status')
    if 'InputExceptionList' in text:
        facets.append('reads the ExceptionList')
    if 'InputRoot.XMLNSC' in text or 'InputRoot.JSON' in text:
        facets.append('reads the incoming body')
    if any(t.startswith('Environment.') for t, _ in sets):
        facets.append('stores values in Environment')
    return ('Module that ' + ', '.join(facets)) if facets else 'Custom ESQL logic'


def sec_esql(r, c, i):
    out = [H('<h3 id="%s">8. ESQL explanation</h3>' % anchor(i, 8))]
    if not c.esqls:
        return H(e(out[0]) + e(para('No ESQL files are used by this flow [C]. The nodes carry no custom code.')))
    out.append(para(H('<span class="note">The Meaning column is generated from each statement mechanically: it says what the '
                      'statement does, not why. Have an ESQL owner confirm anything that matters for behaviour.</span>')))
    for e_ in c.esqls:
        total = len(e_.text.splitlines())
        out.append(para(bold('File: '), '%s (%d lines). %d module%s, each bound to a node by esql://routine/#<module>.Main. '
                        'Line numbers refer to this file.' % (F.basename(e_.path), total, len(e_.modules),
                                                              '' if len(e_.modules) == 1 else 's')))
        out.append(table(('Module', 'Used by node(s)', 'Purpose'), [
            [m, F.describe_nodes(c.bindings.get(m, [])) or 'not bound to a node in this flow', purpose_of(e_, m)]
            for m in e_.modules], ('34%', '30%', '36%')))
        top = [s for s in e_.statements if not s['module']]
        sub = 1
        if top:
            out.append(H('<h4>8.%d Schema-level declarations</h4>' % sub))
            out.append(statement_table(top))
            sub += 1
        for m in e_.modules:
            body = [s for s in e_.statements if s['module'] == m]
            if not body:
                continue
            out.append(H('<h4>8.%d Module %s (lines %d-%d)</h4>' % (sub + 0, e(m), body[0]['start'], body[-1]['end'])))
            out.append(statement_table(body))
            sub += 1
        out.append(H('<h4>8.%d Variables, references, procedures, dynamic SQL</h4>' % sub))
        rows = []
        seen = {}
        for s_, t, _ in e_.sets():
            if t.startswith(('Environment', 'LocalEnvironment')) and t not in seen:
                seen[t] = s_
        for t, s_ in seen.items():
            used = [x for x in e_.statements if t in F.norm_path(x['text'])
                    and not (x['kind'] == 'SET' and F.norm_path(F.split_set(x['text'])[0]) == t)]
            rows.append([code(t), '%s L%d' % (s_['module'] or 'schema', s_['start']),
                         ('; '.join('%s L%d' % (x['module'] or 'schema', x['start']) for x in used[:5])
                          or ('Consumed by the next node, not visible in ESQL [I]' if t.startswith('LocalEnvironment') else 'Never read'))])
        rows += [[code(p), '—', 'Procedure/function; %s' % ('called' if p in e_.calls else 'never called')]
                 for p in e_.procedures + e_.functions if p.split('.')[-1].lower() != 'main']
        sqls = [s for s in e_.statements if s['kind'] == 'SQL']
        rows.append(['Dynamic SQL / PASSTHRU / SHARED', '—', 'None' if not sqls and not e_.shared
                     else '%d SQL statement(s), %d shared variable(s)' % (len(sqls), len(e_.shared))])
        rows.append(['DECLARE HANDLER / THROW', '—', 'None. Error handling is done only through node terminals'
                     if not any(s['kind'] in ('THROW', 'HANDLER') for s in e_.statements) else
                     '%d THROW, %d handler' % (sum(s['kind'] == 'THROW' for s in e_.statements),
                                               sum(s['kind'] == 'HANDLER' for s in e_.statements))])
        out.append(table(('Item', 'Set at', 'Used at / notes'), rows, ('40%', '20%', '40%')))
    return H(''.join(e(b) for b in out))


def statement_table(items, cap=400):
    rows = [[('%d' % s['start']) if s['start'] == s['end'] else '%d-%d' % (s['start'], s['end']),
             code(F.short(s['text'], 160)), s['meaning']] for s in items[:cap]]
    if len(items) > cap:
        rows.append(['…', '%d more statements not shown' % (len(items) - cap), ''])
    return table(('Line', 'Statement', 'Meaning'), rows, ('9%', '45%', '46%'))


def sec_config(r, c, i):
    out = [H('<h3 id="%s">9. Configuration</h3>' % anchor(i, 9))]
    udps = c.udps()
    cfg_props = sorted({'%s.%s' % (n['label'], k) for _, n in c.nodes for k in n['props']
                        if re.search(r'(?i)policy|configurableservice|datasource', k)})
    out.append(kv([
        ['Policy projects', (listed([F.basename(p.get('path', '')) for p in r.model.get('policies') or []]) + ' [C]')
         if r.model.get('policies') else 'None found among the analysed files [C]; no node references a policy.'],
        ['Shared libraries', 'Not visible in the analysed files; check the build definition [N]'],
        ['Configurable services', listed(cfg_props, 'None referenced by the flow') + ' [C]'],
        ['User-defined properties', listed(['%s (default %s)' % (k, v or "''") for k, v in sorted(udps.items())], 'None') + ' [C]'],
        ['Environment variables', 'None read by the flow [C]' if not any('getenv' in e_.text.lower() for e_ in c.esqls) else 'Read in ESQL [C]'],
        ['Server configuration', 'HTTPS listener, keystore/truststore, shared Java classes and node properties are server '
                                 'configuration and are not in the repository [N]'],
    ], ('Area', 'Finding'), ('26%', '74%')))
    if r.table:
        out.append(H('<h4>BAR override keys</h4>'))
        rows = []
        for key, vals in sorted(r.table.items()):
            distinct = sorted(set(vals.values()))
            value = distinct[0] if len(distinct) == 1 and len(vals) == len(r.envs) else 'varies: see environment matrix'
            rows.append([code(key), code(value) if len(distinct) == 1 and len(vals) == len(r.envs) else value, F.key_meaning(key)])
        out.append(table(('Key', 'Value (all envs unless noted)', 'Meaning'), rows, ('40%', '30%', '30%')))
        out.append(para(H('<span class="note">Key anatomy: &lt;flow or subflow name&gt;#&lt;node label&gt;.&lt;property&gt;.</span>')))
        out.append(H('<h4>Environment matrix</h4>'))
        stub = [k for k in r.table if 'testharness' in k.lower()]
        url_keys = [k for k in r.table if k not in stub and (k.lower().endswith('url') or 'urlspecifier' in k.lower()
                                                               or 'webserviceurl' in k.lower())]
        rows = []
        for env in r.envs:
            hosts = sorted({F.host_of(r.table[k].get(env, '')) for k in url_keys if F.host_of(r.table[k].get(env, ''))})
            same = [o for o in r.envs if o != env and r.env_files.get(o) == r.env_files.get(env)]
            shared = [o for o in r.envs if o != env and hosts and
                      hosts == sorted({F.host_of(r.table[k].get(o, '')) for k in url_keys if F.host_of(r.table[k].get(o, ''))})]
            notes = [('Identical to ' + ', '.join(same)) if same else ('Same hosts as ' + ', '.join(shared)) if shared else '',
                     'Stub URL set' if any(r.table[k].get(env) and 'dummy' not in r.table[k][env].lower() for k in stub) else '']
            varying = [lines(*['%s = %s' % (k.rsplit('#', 1)[-1], F.short(r.table[k][env], 60)) for k in sorted(r.table)
                               if len(set(r.table[k].values())) > 1 and env in r.table[k] and k not in url_keys + stub])]
            rows.append([env, listed(hosts, '—'), varying[0] if varying[0] else '—', '; '.join(x for x in notes if x)])
        out.append(table(('Environment', 'Backend hosts', 'Values that differ between environments', 'Note'), rows,
                         ('12%', '22%', '46%', '20%')))
    else:
        out.append(para('No <flow>.<ENV>.properties override files were found in the analysed files [C]. Environment '
                        'values are therefore whatever the flows default to, or are applied outside this repository [N].'))
    return H(''.join(e(b) for b in out))


def sec_errors(r, c, i):
    rows, grouped = [], {}
    for g in c.graphs:
        if not g.terminals_known:
            continue
        wired = []
        for n in g.nodes:
            for t in ('catch', 'failure', 'timeout', 'error'):
                dest = g.wired(n['id'], t)
                if dest:
                    wired.append('%s.%s > %s' % (n['label'], t, ', '.join(g.label(x['to']) for x in dest)))
        loose = [n['label'] for n in g.nodes if n['kind'] in F.FAILURE_KINDS and not g.wired(n['id'], 'failure')]
        grouped.setdefault((tuple(wired), tuple(loose)), []).append(g)
    for (wired, loose), graphs in grouped.items():
        who = ', '.join(F.basename(g.path) for g in graphs) if len(graphs) <= 2 else '%d identical subflows (%s, ...)' % (
            len(graphs), F.basename(graphs[0].path))
        rows.append([who + ': error terminals', listed(wired, 'No catch, failure, timeout or error terminal is connected') + ' [C]'])
        if loose:
            rows.append([who + ': unconnected failure terminals', listed(loose) + ' [C]'])
    walker = any('InputExceptionList' in e_.text for e_ in c.esqls)
    traces = len(c.of('Trace'))
    rows += [['ExceptionList', 'Walked by ESQL to build the error text [C]' if walker else 'Not read by any ESQL [C]'],
             ['Retry', 'None [C]'],
             ['Rollback', 'Not applicable (no transactional resource) [C]' if not c.of('MQ Input', 'MQ Output', 'Database')
              else 'Governed by the transaction mode on the MQ/database nodes [I]'],
             ['Logging', '%s Trace node(s); %d THROW statement(s); %d monitoring event(s) defined in the flows [C]'
              % (traces, sum(s['kind'] == 'THROW' for e_ in c.esqls for s in e_.statements), sum(g.monitoring for g in c.graphs))]]
    out = [H('<h3 id="%s">10. Error handling</h3>' % anchor(i, 10)), kv(rows, ('Mechanism', 'Detail'), ('26%', '74%'))]
    status = []
    for e_ in c.esqls:
        for s_, t, src in e_.sets():
            if re.search(r'(?i)ReplyStatusCode|httpCode|httpMessage|StatusCode$', t):
                status.append([s_['module'] or '—', 'L%d' % s_['start'], t.split('.')[-1], src])
    if status:
        out += [H('<h4>Status values the ESQL sets</h4>'), table(('Module', 'Line', 'Field', 'Value'), status[:40], ('34%', '10%', '24%', '32%'))]
    return H(''.join(e(b) for b in out))


def sec_runtime(r, c, i):
    ai = [v for k, vals in r.table.items() if k.lower().endswith('additionalinstances') for v in vals.values()] + \
         [F.prop(n['props'], 'additionalInstances') for _, n in c.nodes if F.prop(n['props'], 'additionalInstances')]
    threads = None
    try:
        threads = max(int(x) for x in ai) + 1 if ai else None
    except ValueError:
        pass
    order = ' > '.join(n['label'] for _, n in c.graph.expanded_path(c.subs)) or c.graph.name
    items = ['First resource touched: %s. %s' % (c.inbound().get('kind', 'the trigger'),
                                                  'No queue and no database [C].' if not c.of('MQ Input', 'Database') else ''),
             'Order: strictly sequential per request: %s [C].' % order,
             'Parallelism: only across requests. %s' % (('additionalInstances gives %d concurrent threads per integration server [C].' % threads)
                                                        if threads else 'No additionalInstances override found, so the product default of one thread applies [I].'),
             'Transaction boundaries / commit points: %s' % ('none [C].' if not c.of('MQ Input', 'MQ Output', 'Database')
                                                           else 'set by the transaction mode on the MQ/database nodes [C].')]
    # The override is what runs, so it wins over the value the node was designed with.
    t = [v for k, vals in r.table.items() if k.lower().endswith('timeoutforserver') for v in vals.values()] or \
        [F.prop(n['props'], 'timeoutForServer') for _, n in c.calls if F.prop(n['props'], 'timeoutForServer')]
    if threads and t:
        try:
            worst = max(int(x) for x in t)
            items.append('Capacity rule of thumb: a thread is blocked for the whole backend call. At %d threads and a %ds '
                         'backend time-out, worst-case throughput falls to about %.2f requests per second. Normal latency '
                         'is what matters, so measure it.' % (threads, worst, threads / worst))
        except ValueError:
            pass
    return H(e(H('<h3 id="%s">11. Runtime behaviour</h3>' % anchor(i, 11))) + e(bullets(items)))


def sec_production(r, c, i):
    rows = [['Common failures', lines(*[x for x in [
        'Backend down or slow; certificate or truststore change' if c.calls else '',
        'Placeholder URL deployed where no override exists' if any(re.search(r'(?i)dummy', str(v)) for v in c.udps().values()) else '',
        'Java class missing from the classpath' if any(e_.java for e_ in c.esqls) else '',
        'Unhandled exception: %s' % ('propagates to the catch handler' if c.of('HTTP Input') else 'rolls the message back')]
        if x]), 'Monitoring events; error body and transaction id; deployed overrides applied'],
            ['Performance', 'Each request blocks a thread for the backend call; no caching' if c.calls else 'In-memory transformation only',
             'Response size, backend latency, thread utilisation'],
            ['Queue depth', 'No queues; the equivalent symptom is listener backlog when all threads are busy'
             if not c.of('MQ Input') else 'Input queue backs up when the flow is slower than arrivals', 'Queue depth and oldest-message age'],
            ['Database connectivity', 'Not applicable' if not c.of('Database') else 'Connection loss rolls the unit of work back', '—'],
            ['Endpoint failures', 'Connection or read time-out; non-2xx replies; non-XML error pages' if c.calls else 'Not applicable',
             'Backend health, network path, load-balancer errors'],
            ['Cache failures', 'Not applicable', '—'],
            ['Monitoring points', '%d monitoring event(s) defined in the flows' % sum(g.monitoring for g in c.graphs),
             'Confirm which collector subscribes [N]']]
    out = [H('<h3 id="%s">12. Production considerations</h3>' % anchor(i, 12)),
           table(('Area', 'What can go wrong', 'What to check'), rows, ('18%', '48%', '34%'))]
    ib = c.inbound()
    if ib.get('kind') == 'HTTP Input' and ib.get('url'):
        out += [H('<h4>Quick smoke test (replace placeholders)</h4>'),
                pre('curl -k -H "X-Correlation-Id: smoke-001" \\\n  "http%s://<iib-host>:<port>%s"' % ('s' if ib.get('https') else '', ib['url'].rstrip('*')))]
    return H(''.join(e(b) for b in out))


def sec_dependencies(r, c, i):
    ib = c.inbound()
    rows = [['Source system', 'Callers of %s [I]' % (ib.get('url') or ib.get('kind', 'the trigger'))],
            ['Input queue', listed([F.prop(n['props'], 'queueName') for _, n in c.of('MQ Input')], 'None (%s)' % ib.get('kind', 'trigger'))],
            ['Message flow', listed([F.basename(g.path) for g in c.graphs])],
            ['Mapping / schema mapping nodes', (listed([n['label'] for _, n in c.of('Mapping', 'XSLT')]) if c.of('Mapping', 'XSLT')
                                               else 'None. No Mapping or XSLT node') + ('; ' + listed([F.basename(s.get('path', '')) for s in r.model.get('schemas') or []]) if r.model.get('schemas') else '')],
            ['Compute nodes', F.describe_nodes(c.of('Compute', 'Java Compute', 'Filter'))],
            ['Database', listed(sorted({d for e_ in c.esqls for d in e_.databases} | {n['label'] for _, n in c.of('Database')}), 'None')],
            ['Cache', 'None' if not any(e_.shared for e_ in c.esqls) else 'Shared variables'],
            ['External APIs', listed([F.prop(n['props'], 'URLSpecifier', 'webServiceURL', 'queueName') or n['label'] for _, n in c.calls]
                                     + sorted({j for e_ in c.esqls for j in e_.java}), 'None')],
            ['Output queue', listed([F.prop(n['props'], 'queueName') for _, n in c.of('MQ Output')], 'None')],
            ['Target system', 'The original caller' if c.of('HTTP Reply', 'SOAP Reply') else 'The downstream systems above']]
    return H(e(H('<h3 id="%s">13. Dependency mapping</h3>' % anchor(i, 13))) + e(kv(rows, ('Layer', 'Component'), ('26%', '74%'))))


def sec_visual(r, c, i):
    out = [H('<h3 id="%s">14. Visual representation</h3>' % anchor(i, 14)), H('<h4>14a. System context</h4>'), pre(context(r, c)),
           H('<h4>14b. Top-level flow</h4>'), pre(chain(c.graph))]
    drawn = {}
    for s in c.subs:
        drawn.setdefault(chain(s), []).append(s.name)
    for text, names in drawn.items():
        out += [H('<h4>14c. Subflow%s %s</h4>' % ('s' if len(names) > 1 else '', e(', '.join(names)))), pre(text)]
    out += [H('<h4>14d. Request timeline (main path, subflows expanded)</h4>'), pre(timeline(c))]
    return H(''.join(e(b) for b in out))


def sec_summary(r, c, i):
    kinds = count_kinds(c)
    comps = ', '.join('%d %s' % (n, k) for k, n in kinds.most_common() if not k.startswith('Subflow '))
    rows = [['Overall purpose', 'A %s flow%s.' % (c.pattern(), ' that calls ' + listed([n['label'] for _, n in c.calls]) if c.calls else '')],
            ['Components used', '%s; %d ESQL module(s)' % (comps or 'none', len(c.modules))],
            ['Databases', listed(sorted({d for e_ in c.esqls for d in e_.databases} | {n['label'] for _, n in c.of('Database')}), 'None') + '.'],
            ['Queues', listed([F.prop(n['props'], 'queueName') for _, n in c.of('MQ Input', 'MQ Output', 'MQ Get')], 'None') + '.'],
            ['Endpoints', 'In: %s. Out: %s.' % (c.inbound().get('url') or c.inbound().get('kind', 'n/a'),
                                                  listed([F.prop(n['props'], 'URLSpecifier', 'webServiceURL', 'queueName') or n['label'] for _, n in c.calls], 'none'))],
            ['Configuration dependencies', '%d property file(s); user-defined properties %s; server configuration and Java jars are [N].'
             % (len(r.envs), listed(sorted(c.udps()), 'none'))],
            ['Important files', listed([F.basename(g.path) for g in c.graphs] + [F.basename(e_.path) for e_ in c.esqls]
                                       + ['*.properties'] if r.envs else [])]]
    checks = []
    if any(re.search(r'(?i)do not edit|automatically generated', ' '.join(g.notes)) for g in c.graphs):
        checks.append('Never edit the generated flow; changes belong in the subflow or ESQL.')
    shared = {m: u for m, u in c.bindings.items() if len(u) > 1}
    for m, u in shared.items():
        checks.append('ESQL module %s is shared by %d nodes; a change affects all of them.' % (m, len(u)))
    if any(re.search(r'(?i)url', k) for k in c.udps()):
        checks.append('Change URLs through the user-defined properties (%s), not only through node properties.'
                      % listed([k for k in c.udps() if re.search(r'(?i)url', k)]))
    if any(e_.java for e_ in c.esqls):
        checks.append('Confirm the Java class is available on the target server before deploying.')
    checks += ['Capture a real success and a real error response (headers and body) in a lower environment before and after any change.',
               'Check the API contract and gateway policy before changing any status code or field type.']
    return H(e(H('<h3 id="%s">15. Summary</h3>' % anchor(i, 15))) + e(kv(rows, ('Topic', 'Summary'), ('26%', '74%')))
             + e(H('<h4>Check before making any change</h4>')) + e(bullets(checks)))


SECTION_FNS = (sec_purpose, sec_walkthrough, sec_lifecycle, sec_queues, sec_database, sec_cache, sec_integrations,
               sec_esql, sec_config, sec_errors, sec_runtime, sec_production, sec_dependencies, sec_visual, sec_summary)


# ---- the page ---------------------------------------------------------------------------------

def day_one(r, c):
    """Things to know on day one: each one a [lead, rest] pair, from what the flow actually does."""
    out = []
    handlers = [n for _, n in c.nodes if n['kind'] == 'Subflow' and re.search(r'(?i)catch|failure|timeout|error', n['label'])]
    shared = {m: u for m, u in c.bindings.items() if len(u) > 1}
    for m, u in shared.items():
        out.append(('One ESQL module serves %d nodes.' % len(u), '%s runs for %s, so a change to it changes all of them [C].'
                    % (m, F.describe_nodes(u))))
    if handlers:
        out.append(('%d error-handler subflow%s.' % (len(handlers), '' if len(handlers) == 1 else 's'),
                    '%s. See the error handling section [C].' % listed([n['label'] for n in handlers])))
    if c.unwired_groups():
        out.append(('Unconnected failure terminals.', '%s. Exceptions there travel to the enclosing catch handler [C].'
                    % F.describe_unwired(c.unwired_groups())))
    for _, n in c.calls:
        urlprop = F.prop(n['props'], 'URLSpecifier', 'webServiceURL')
        if any('RequestURL' in t for e_ in c.esqls for _, t, _ in e_.sets()) and urlprop:
            out.append(('The effective backend URL is set in ESQL.', 'LocalEnvironment.Destination.HTTP.RequestURL overrides the '
                        'node property on %s, so overrides of %s alone are superseded at run time [I].' % (n['label'], 'URLSpecifier / webServiceURL')))
            break
    if any('testharness' in k.lower() for k in r.table):
        out.append(('A test-harness URL is configured.', 'Stub routing properties exist in the override files%s [C].'
                    % (' (including production)' if any('testharness' in k.lower() and any(tier_prod(e) and v for e, v in vals.items()) for k, vals in r.table.items()) else '')))
    dummy = [k for k, v in c.udps().items() if re.search(r'(?i)dummy|localhost|example\.com', str(v))]
    if dummy:
        out.append(('Defaults are placeholder values.', '%s default to placeholders; an environment with no override would run with them [C].' % listed(dummy)))
    mon = sum(g.monitoring for g in c.graphs)
    out.append(('Observability.', ('%d flow-monitoring event(s) are defined [C].' % mon) if mon else
                'No Trace nodes and no flow-monitoring events are defined in the flows [C].' if not c.of('Trace') else '%d Trace node(s) [C].' % len(c.of('Trace'))))
    if c.esqls and not any(s['kind'] in ('THROW', 'HANDLER') for e_ in c.esqls for s in e_.statements):
        out.append(('No ESQL exception handling.', 'There is no THROW or DECLARE HANDLER; errors travel through node terminals only [C].'))
    return out


def tier_prod(env):
    return F.tier_of(env) == 'PROD'


def exec_summary(r):
    c = r.chapters[0] if r.chapters else None
    out = [H('<h2 id="exec">Executive summary</h2>')]
    if not c:
        return H(e(out[0]) + e(para('No message flow was found in the analysed files [C]. See the review findings register.')))
    ib = c.inbound()
    reach = (join('A consumer reaches ', code(ib.get('url') or ib.get('kind')),
                  ' over %s. ' % ('HTTPS' if ib.get('https') else ib.get('kind', 'the trigger'))) if ib
             else 'The trigger is not recognised. ')
    out.append(para(bold('What it is. '), '%s%s flow. ' % (
        'A ' if len(r.chapters) == 1 else '%d flows in this application; the first is a ' % len(r.chapters), c.pattern()),
        reach, '%d ESQL module%s transform%s the message%s.' % (
            len(c.modules), '' if len(c.modules) == 1 else 's', 's' if len(c.modules) == 1 else '',
            '; the flow then calls %s' % listed([n['label'] for _, n in c.calls]) if c.calls else '')))
    call = c.calls[0][1]['props'] if c.calls else {}
    rows = [
        ['Pattern', c.pattern() + ('. No orchestration.' if len(c.calls) <= 1 else '. Fans out to %d calls.' % len(c.calls))],
        ['Inbound', (join(ib['kind'], ', ', code(ib['url']) if ib.get('url') else '', ', HTTPS' if ib.get('https') else '',
                          ', client time-out %ss' % ib['timeout'] if ib.get('timeout') else '', ' [C]')) if ib else 'None found'],
        ['Outbound', (join(c.calls[0][1]['kind'], ' ', code(c.calls[0][1]['label']),
                           ', %s' % F.prop(call, 'sslProtocol') if F.prop(call, 'sslProtocol') else '',
                           ', server time-out %s' % effective(r, c.calls[0][1], 'timeoutForServer', ' s')
                           if F.prop(call, 'timeoutForServer') else '', ' [C]'))
                      if c.calls else 'None'],
        ['Transformation', ('%d Compute/Filter node(s) in %d ESQL module(s); %d Mapping/XSLT node(s) [C]'
                            % (len(c.of('Compute', 'Java Compute', 'Filter')), len(c.modules), len(c.of('Mapping', 'XSLT'))))],
        ['MQ / JMS / Kafka / SFTP', listed(sorted({n['kind'] for _, n in c.of('MQ Input', 'MQ Output', 'MQ Get', 'JMS Input', 'JMS Output', 'Kafka Consumer', 'Kafka Producer', 'SFTP', 'FTE')}), 'None') + ' [C]'],
        ['Database', listed(sorted({d for e_ in c.esqls for d in e_.databases} | {n['label'] for _, n in c.of('Database', 'Database Route')}), 'None') + ' [C]'],
        ['Cache', 'None (no SHARED variables, no Global Cache) [C]' if not any(e_.shared for e_ in c.esqls) else 'Shared variables in ESQL [C]'],
        ['Transactions', 'None. Nothing to commit or roll back [C]' if not c.of('MQ Input', 'MQ Output', 'Database') else 'Governed by MQ/database node transaction modes [C]'],
        ['Authentication', ('Security profile %s [C]' % ib['security']) if ib.get('security') else
         'None configured in the flow. Expected to be enforced by the API gateway [I]'],
        ['Retry', 'None anywhere [C]' if not any('retry' in k.lower() for _, n in c.nodes for k in n['props']) else 'Configured on some nodes [C]'],
        ['Concurrency', ('additionalInstances: %s' % listed(sorted({v for k, vals in r.table.items() if k.lower().endswith('additionalinstances') for v in vals.values()}))) if
         any(k.lower().endswith('additionalinstances') for k in r.table) else 'Product default (no override found) [I]'],
        ['Environments', ('%d BAR override files: %s [C]' % (len(r.envs), ', '.join(r.envs))) if r.envs else 'No override files in the analysed files [C]'],
        ['Hidden dependency', listed(sorted({j for e_ in c.esqls for j in e_.java}), 'None') + (' (not in this repository) [C that it is called, N for the code]' if any(e_.java for e_ in c.esqls) else '')]]
    out.append(kv(rows, ('Aspect', 'Finding'), ('22%', '78%')))
    items = day_one(r, c)
    out.append(H('<h4>%s things to know on day one</h4>' % e(len(items))))
    out.append(H('<ul>%s</ul>' % ''.join('<li><b>%s</b> %s</li>' % (e(a), e(b)) for a, b in items)))
    return H(''.join(e(b) for b in out))


def inventory(r):
    inv = r.files
    role = {'.msgflow': 'Message flow', '.subflow': 'Subflow', '.esql': 'ESQL', '.properties': 'BAR override file',
            '.map': 'Map', '.xslt': 'XSLT', '.xsd': 'Schema', '.wsdl': 'WSDL', '.json': 'JSON', '.yaml': 'YAML', '.yml': 'YAML',
            '.xml': 'XML', '.md': 'Documentation', '.descriptor': 'REST API descriptor', '.project': 'Eclipse project'}
    what = {}
    for c in r.chapters:
        for g in c.graphs:
            what[g.path] = '%d node%s: %s' % (len(g.nodes), '' if len(g.nodes) == 1 else 's', listed([k for k, _ in Counter(n['kind'] for n in g.nodes).most_common(5)]))
    for e_ in r.esqls:
        what[e_.path] = '%d module%s (%s)%s' % (len(e_.modules), '' if len(e_.modules) == 1 else 's', listed(e_.modules[:4]),
                                               '; ' + str(len(e_.java)) + ' Java routine(s)' if e_.java else '')
    rows = []
    for rel, size in inv[:300]:
        rel = rel.replace('\\', '/')
        ext = '.' + rel.rsplit('.', 1)[-1].lower() if '.' in rel else ''
        detail = what.get(rel) or next((v for k, v in what.items() if k.replace('\\', '/') == rel), '')
        if ext == '.properties' and not detail:
            detail = 'Per-environment overrides (%d keys)' % sum(1 for k, v in r.table.items() if F.stem(rel).rsplit('.', 1)[-1] in v)
        rows.append([code(rel), role.get(ext, ext.lstrip('.').upper() or 'File'), detail or '—'])
    if len(inv) > 300:
        rows.append(['…', '', '%d more files not listed' % (len(inv) - 300)])
    out = [H('<h2 id="structure">Application structure and file inventory</h2>'),
           para('The repository holds %d file%s; %d flow%s, %d subflow%s and %d ESQL file%s were analysed.' % (
               len(inv), '' if len(inv) == 1 else 's', len(r.chapters), '' if len(r.chapters) == 1 else 's',
               len(r.subs), '' if len(r.subs) == 1 else 's', len(r.esqls), '' if len(r.esqls) == 1 else 's')),
           table(('File', 'Type', 'Role'), rows, ('46%', '16%', '38%')) if rows else para('No file inventory was supplied.')]
    missing = r.not_in_repo()
    out.append(para(bold('Not in the repository [N]: '), '; '.join(missing), '. Request these from the platform team before migration sign-off.'))
    return H(''.join(e(b) for b in out))


def findings_page(r):
    rows = [[f['id'], H('<span class="sev-%s">%s</span>' % (f['sev'], e(f['sev']))), f['finding'], f['evidence'], f['action']]
            for f in r.findings]
    return H(e(H('<h2 id="findings">Review findings register</h2>'))
             + e(para('Observations made while reading the code. None were tested against a running server; severity reflects likely impact.'))
             + e(table(('ID', 'Sev.', 'Finding', 'Evidence', 'Suggested action'), rows, ('6%', '7%', '41%', '20%', '26%'))
                 if rows else para('No findings were raised.')))


def contract_page(r):
    out = [H('<h2 id="contract">Behavioural contract for the microservice team</h2>'),
           para('What the replacement service must reproduce to be functionally equivalent.')]
    for c in r.chapters:
        ib = c.inbound()
        out.append(H('<h4>%s: request mapping</h4>' % e(c.name)))
        rows = [['Inbound', '%s %s%s' % (ib.get('kind', 'unknown'), ib.get('url', ''), ', HTTPS' if ib.get('https') else '')]]
        corr = [(s_, re.findall(r'"([\w-]+)"', s_['text'])) for e_ in c.esqls for s_ in e_.statements
                if s_['kind'] in ('SET', 'DECLARE') and 'COALESCE' in s_['text'].upper()
                and len(re.findall(r'"([\w-]+)"', s_['text'])) >= 2]
        if corr:
            rows.append(['Correlation id', 'First non-null of %s (ESQL L%d); otherwise the ESQL falls back to its own default.'
                         % (listed(corr[0][1]), corr[0][0]['start'])])
        for _, n in c.calls:
            p = n['props']
            rows.append(['Backend call: ' + n['label'], '%s %s. Time-out %s. No retry.' % (
                F.prop(p, 'httpMethod') or n['kind'], F.prop(p, 'URLSpecifier', 'webServiceURL', 'queueName') or 'URL from LocalEnvironment',
                effective(r, n, 'timeoutForServer', ' s') if F.prop(p, 'timeoutForServer') else 'at node default')])
        if any('testharness' in k.lower() for k in r.table):
            rows.append(['Test mode', 'A stub URL property exists; decide whether to keep it.'])
        out.append(kv(rows, ('Aspect', 'Behaviour'), ('22%', '78%')))
        maps = [(src, t) for e_ in c.esqls for _, t, src in e_.sets()
                if t.startswith('OutputRoot.') and re.search(r'InputRoot|\binRef\b|Ref\b', src) and t.split('.')[1] != 'Properties']
        if maps:
            out += [H('<h4>Response mapping (source &gt; target)</h4>'),
                    table(('Source element (namespace ignored)', 'Target field'),
                          [[code(re.sub(r'\*:', '', F.norm_path(s))), code(t)] for s, t in maps[:60]], ('55%', '45%')),
                    para('Order is preserved by the ESQL loops where present. Behaviour for missing optional elements is not '
                         'visible in the code; test it [I].')]
        out.append(H('<h4>Status and error behaviour</h4>'))
        out.append(para('See the status values and wiring in the error handling section of %s.' % c.name))
    return H(''.join(e(b) for b in out))


def tracker_page(r):
    rows = []
    for n, c in enumerate(r.chapters, 1):
        deps = [F.prop(x['props'], 'URLSpecifier', 'webServiceURL', 'queueName') or x['label'] for _, x in c.calls] + \
               sorted({j for e_ in c.esqls for j in e_.java})
        rows.append([str(n), c.name + ' (' + c.pattern() + ')', 'Analysed', listed(deps, 'None'), 'This document'])
    for item in r.not_in_repo():
        rows.append(['-', item[0].upper() + item[1:], 'Please send', '-', 'Needed to close the items marked [N]'])
    rows.append(['-', 'Sample real request/response captures (success, 4xx, 5xx)', 'Please send', '-', 'Resolves items marked [I]'])
    return H(e(H('<h2 id="tracker">Application continuity tracker</h2>')) + e(para('This section grows as you send more flows.'))
             + e(table(('#', 'Flow / artefact', 'Status', 'Depends on', 'Notes'), rows, ('4%', '36%', '12%', '28%', '20%'))))


def toc(r):
    def l1(href, text):
        return '<li class="l1"><a href="#%s">%s</a><span class="dots"></span><a class="n" href="#%s">›</a></li>' % (href, e(text), href)

    def l2(href, text):
        return '<li class="l2"><a href="#%s">%s</a><span class="dots"></span><a class="n" href="#%s">›</a></li>' % (href, e(text), href)
    rows = [l1('contents', 'Contents'), l1('exec', 'Executive summary'), l1('structure', 'Application structure and file inventory')]
    for c in r.chapters:
        rows.append(l1(anchor(c.index), 'Flow %d: %s' % (c.index, c.name)))
        rows += [l2(anchor(c.index, k), '%d. %s' % (k, s)) for k, s in enumerate(SECTIONS, 1)]
    rows += [l1('findings', 'Review findings register'), l1('contract', 'Behavioural contract for the microservice team'),
             l1('tracker', 'Application continuity tracker')]
    return H('<ul class="toc">%s</ul>' % ''.join(rows))


def css_string(text):
    return re.sub(r'[^\w .()–-]', '', str(text))[:120]


def render_html(report):
    """The whole page for one ``ace_report_facts.Report``."""
    r, m = report, report.meta
    name = m.get('title') or (r.chapters[0].name if r.chapters else 'ACE analysis')
    first = r.chapters[0] if r.chapters else None
    foot = 'IIB Application Analysis – %s%s' % (name, ' (Flow 1)' if len(r.chapters) == 1 else '')
    cover_rows = [
        ['Repository', '%s (%d file%s, %d analysed)' % (m.get('repository') or name, len(r.files), '' if len(r.files) == 1 else 's',
                                                        len(r.sources) or len(r.files))],
        ['Branch analysed', m.get('branch') or 'Not recorded'],
        ['Analysis date', m.get('date', '')],
        ['Purpose', 'Ownership hand-over and functional-parity input for the microservice migration team'],
        ['Runtime assumed', 'IBM Integration Bus / ACE toolkit project (%d flow%s, %d subflow%s, %d ESQL file%s)' % (
            len(r.chapters), '' if len(r.chapters) == 1 else 's', len(r.subs), '' if len(r.subs) == 1 else 's',
            len(r.esqls), '' if len(r.esqls) == 1 else 's')]]
    if m.get('analysis_id'):
        cover_rows.append(['Analysis record', '%s (content hash %s…, coverage %s%%)' % (
            m['analysis_id'], str(m.get('content_hash', ''))[:12], m.get('coverage_score', ''))])
    parts = ['<section id="cover"><h1>IBM Integration Bus Application Analysis</h1><p class="subtitle">%s</p>' % e(
        'Flow 1 – %s (%s)' % (first.name, first.pattern()) if first and len(r.chapters) == 1 else
        '%s (%d flows)' % (name, len(r.chapters))),
        e(kv(cover_rows, ('Item', 'Value'), ('22%', '78%'))), '<h4>Evidence legend used throughout</h4>',
        e(table(('Tag', 'Meaning'), [
            ['[C] Confirmed', 'Directly visible in a file in this repository (file and line given where useful).'],
            ['[I] Inferred', 'Follows from standard IIB behaviour or naming, but is not visible in the repo. Verify before relying on it.'],
            ['[N] Not in repo', 'Needed to fully understand the app but lives elsewhere (server config, Java jar, backend WSDL ...).']],
            ('22%', '78%'))), '</section>',
        '<section id="contents"><h2>Contents</h2>%s</section>' % e(toc(r)),
        '<section>%s</section>' % e(exec_summary(r)),
        '<section>%s</section>' % e(inventory(r))]
    for c in r.chapters:
        body = [e(H('<h2 id="%s">Flow %d: %s</h2>' % (anchor(c.index), c.index, e(c.name))))]
        body += [e(fn(r, c, c.index)) for fn in SECTION_FNS]
        parts.append('<section>%s</section>' % ''.join(body))
    parts += ['<section>%s</section>' % e(findings_page(r)), '<section>%s</section>' % e(contract_page(r)),
              '<section>%s</section>' % e(tracker_page(r))]
    style = CSS + ':root{--foot:"%s"}' % css_string(foot)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body><main class="sheet">%s</main></body></html>'
            % (e('IIB Application Analysis – %s' % name), style, ''.join(parts)))


def build(model, sources=(), files=(), meta=None, unsupported=()):
    """Render the report for a semantic model. ``sources`` are ``(rel, text, ext)``; ``files`` are ``(rel, size)``."""
    return render_html(F.Report(model, sources, files, meta, unsupported))


def build_for_analysis(repo, analysis_id, root):
    """The report for a stored analysis, with the repository files read from ``root``.

    One place gathers everything the page needs -- the stored model, what the intake recorded about the repository,
    the unsupported constructs, the file list and the files' text -- so the agent that writes the report after an
    analysis and the command that regenerates one later cannot disagree.
    """
    import json
    from datetime import datetime
    from pathlib import Path
    from app.services.enterprise_v5 import ACEParser
    row = repo.one('select * from ace_analyses where id=?', (analysis_id,))
    if not row:
        raise ValueError('analysis_not_found:' + str(analysis_id))
    root = Path(root)
    files = [(str(f.relative_to(root)), f.stat().st_size) for f in sorted(root.rglob('*'))
             if f.is_file() and '.git' not in f.parts]
    manifest = repo.one('select repository_uri,branch from intake_manifests where job_id=? order by created_at desc limit 1',
                        (row['job_id'],)) or {}
    coverage = repo.one("select payload from validation_reports where reference_id=? and report_type='PARSER_COVERAGE'",
                        (analysis_id,))
    meta = {'repository': Path(str(manifest.get('repository_uri') or root)).name or root.name,
            'branch': manifest.get('branch') or '', 'date': datetime.now().strftime('%d %b %Y').lstrip('0'),
            'analysis_id': analysis_id, 'content_hash': row['content_hash'],
            'coverage_score': json.loads(coverage['payload']).get('score', '') if coverage else ''}
    unsupported = repo.rows('select * from unsupported_constructs where analysis_id=?', (analysis_id,))
    return build(json.loads(row['semantic_model']), ACEParser().sources(root), files, meta, unsupported)


def main(argv=None):
    """``python -m app.services.ace_analysis_report <analysis id> <repository folder> [output.html]``

    Rebuilds the report from a stored analysis without running the analysis again.
    """
    import os
    import sys
    from pathlib import Path
    from app.repository import Repository
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (2, 3):
        print(main.__doc__)
        return 2
    default_db = Path(__file__).resolve().parents[2] / 'factory-v2.db'
    page = build_for_analysis(Repository(os.getenv('FACTORY_DB', str(default_db))), args[0], args[1])
    out = Path(args[2] if len(args) == 3 else FILENAME).resolve()
    out.write_text(page, encoding='utf-8')
    print(out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
