"""What the ACE analysis report says, worked out from the analysis itself.

``ace_analysis_report`` only lays facts out; this module is where they come from. Everything here is
derived from the stored semantic model (which keeps the raw text of every flow, ESQL, map and schema
file) and from the repository's own files -- nothing is invented. Each fact carries the evidence tag
the report prints beside it: ``[C]`` confirmed in a file, ``[I]`` inferred from standard IIB
behaviour, ``[N]`` needed but not in the repository.

The ESQL reading is mechanical (a statement splitter and a few patterns), so the "meaning" it gives a
statement states what the statement does, not why. The report says so.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

NOISE = {'type', 'id', 'location', 'version', 'xsi', 'name'}

#: (substring of the lower-cased node type, label) -- the first match wins, so a longer name goes first.
KINDS = (
    ('.subflow', 'Subflow'),        # first: a subflow's own name can contain any other token ("...DatabaseHandler")
    ('fcmsource', 'Subflow Input'), ('fcmsink', 'Subflow Output'), ('stickynote', 'Note'),
    ('httpinput', 'HTTP Input'), ('wsinput', 'HTTP Input'), ('soapinput', 'SOAP Input'),
    ('httpreply', 'HTTP Reply'), ('wsreply', 'HTTP Reply'), ('soapreply', 'SOAP Reply'),
    ('httprequest', 'HTTP Request'), ('wsrequest', 'HTTP Request'), ('soaprequest', 'SOAP Request'),
    ('routetolabel', 'Route To Label'), ('comibmlabel', 'Label'),
    ('javacompute', 'Java Compute'), ('compute', 'Compute'), ('filter', 'Filter'),
    ('mqinput', 'MQ Input'), ('mqoutput', 'MQ Output'), ('mqget', 'MQ Get'),
    ('jmsinput', 'JMS Input'), ('jmsoutput', 'JMS Output'),
    ('kafkaconsumer', 'Kafka Consumer'), ('kafkaproducer', 'Kafka Producer'),
    ('fileinput', 'File Input'), ('fileoutput', 'File Output'), ('sftp', 'SFTP'), ('fte', 'FTE'),
    ('databaseroute', 'Database Route'), ('database', 'Database'),
    ('timeoutnotification', 'Timeout Notification'), ('timeoutcontrol', 'Timeout Control'),
    ('trycatch', 'TryCatch'), ('throw', 'Throw'), ('trace', 'Trace'), ('passthru', 'Passthrough'),
    ('msl', 'Mapping'), ('mapping', 'Mapping'), ('xsl', 'XSLT'), ('validate', 'Validate'),
    ('resetcontentdescriptor', 'Reset Content Descriptor'),
)
INPUT_KINDS = {'HTTP Input', 'SOAP Input', 'MQ Input', 'JMS Input', 'Kafka Consumer', 'File Input',
               'Timeout Notification', 'SFTP', 'FTE'}
CALL_KINDS = {'HTTP Request', 'SOAP Request', 'MQ Output', 'MQ Get', 'JMS Output', 'Kafka Producer',
              'File Output', 'Database', 'Database Route'}
CODE_KINDS = {'Compute', 'Java Compute', 'Filter', 'Mapping', 'XSLT'}
#: Nodes whose unwired failure terminal lets an exception travel up to the enclosing handler.
FAILURE_KINDS = CODE_KINDS | CALL_KINDS

ROLE = {
    'HTTP Input': 'Accepts the inbound HTTP request. Creates InputRoot.HTTPInputHeader (all request headers) and the '
                  'RequestIdentifier that ties the reply to this client connection.',
    'SOAP Input': 'Accepts the inbound SOAP request and parses the envelope.',
    'HTTP Reply': 'Serialises the message tree and sends the HTTP response to the waiting client.',
    'SOAP Reply': 'Serialises the SOAP response and sends it to the waiting client.',
    'HTTP Request': 'Calls the downstream HTTP service. 2xx leaves on out, non-2xx on error, transport or '
                    'time-out problems on failure.',
    'SOAP Request': 'Calls the downstream SOAP service. Faults and transport problems leave on failure.',
    'Route To Label': 'Reads the label placed in LocalEnvironment and sends the message to the matching Label node.',
    'Label': 'Entry point of a routed operation. Passes the message through unchanged.',
    'Subflow Input': 'Receives the message from the enclosing flow.',
    'Subflow Output': 'Hands the finished message back to the enclosing flow.',
    'MQ Input': 'Reads a message from the input queue.', 'MQ Output': 'Writes the message to the output queue.',
    'MQ Get': 'Reads a message from a queue on demand.',
    'JMS Input': 'Reads a message from a JMS destination.', 'JMS Output': 'Writes to a JMS destination.',
    'Kafka Consumer': 'Consumes records from a Kafka topic.', 'Kafka Producer': 'Publishes records to a Kafka topic.',
    'File Input': 'Reads a file.', 'File Output': 'Writes a file.',
    'Database': 'Runs the configured database operation.', 'Database Route': 'Routes on a database lookup.',
    'Timeout Notification': 'Starts the flow on a timer.', 'Throw': 'Raises an exception.',
    'TryCatch': 'Runs the try path and diverts to catch on an exception.', 'Trace': 'Writes a trace entry.',
    'Filter': 'Routes the message to true or false from an ESQL condition.',
    'Mapping': 'Transforms the message with a graphical map.', 'XSLT': 'Transforms the message with a stylesheet.',
}
KEYS = {
    'HTTP Input': ('URLSpecifier', 'useHTTPS', 'messageDomainProperty', 'timeoutForClient', 'faultFormat',
                   'parseQueryString', 'additionalInstances', 'securityProfileName'),
    'HTTP Request': ('URLSpecifier', 'webServiceURL', 'httpMethod', 'sslProtocol', 'timeoutForServer',
                     'validateMaster', 'messageDomainProperty'),
    'SOAP Request': ('webServiceURL', 'sslProtocol', 'timeoutForServer', 'wsdlFileName'),
    'Compute': ('computeExpression', 'computeMode', 'dataSource'),
    'Java Compute': ('javaClass', 'computeMode'),
    'Label': ('labelName',),
    'MQ Input': ('queueName', 'queueManagerName', 'transactionMode'),
    'MQ Output': ('queueName', 'queueManagerName', 'transactionMode'),
    'MQ Get': ('queueName', 'queueManagerName'),
    'Kafka Consumer': ('topicName', 'bootstrapServers'), 'Kafka Producer': ('topicName', 'bootstrapServers'),
    'File Input': ('inputDirectory', 'filePattern'), 'File Output': ('outputDirectory',),
    'Timeout Notification': ('timeoutInterval',),
}
ENV_TIERS = {'DEV': ('dev',), 'QA': ('qa', 'sit', 'test'), 'UAT': ('uat',),
             'STRESS': ('stress', 'perf', '-pt', 'pt-'), 'PROD': ()}
NON_PROD = ('dev', 'qa', 'sit', 'uat', 'stress', 'perf', 'test', 'dummy', 'mock', 'wiremock')
#: Terminals a message leaves by when nothing has gone wrong.
NORMAL = ('out', 'true', 'false', 'match', 'default', 'label', '')


def local(tag):
    return tag.split('}')[-1]


def attrs(element):
    return {k.split('}')[-1]: v for k, v in element.attrib.items()}


def prop(props, *names):
    """A property by any of ``names``, ignoring case -- IIB writes them lowerCamel but not consistently."""
    lowered = {k.lower(): v for k, v in (props or {}).items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] != '':
            return lowered[name.lower()]
    return ''


def kind_of(type_name):
    low = str(type_name or '').lower()
    for token, label in KINDS:
        if token in low:
            return label
    tail = re.split(r'[:.]', str(type_name or ''))[0]
    return re.sub(r'^ComIbm', '', tail) or 'Node'


def key_of(path):
    """A path as a lookup key: separators and case do not distinguish two files."""
    return str(path).replace('\\', '/').lower()


def stem(path):
    return PurePosixPath(str(path).replace('\\', '/')).name.rsplit('.', 1)[0]


def basename(path):
    return PurePosixPath(str(path).replace('\\', '/')).name


# ---- flows -----------------------------------------------------------------------------------

class Graph:
    """One ``.msgflow`` or ``.subflow`` as nodes and wired terminals."""

    def __init__(self, flow, is_sub):
        self.path = flow.get('path', '')
        self.name = stem(self.path)
        self.is_sub = is_sub
        self.nodes, self.edges, self.notes, self.udps = [], [], [], {}
        self.monitoring = 0
        self.terminals_known = False
        self.text = flow.get('source') or ''
        root = None
        if self.text:
            try:
                root = ET.fromstring(self.text)
            except ET.ParseError:
                root = None
        if root is not None:
            self._read(root)
        if not self.nodes:
            self._from_model(flow)
        self.by_id = {n['id']: n for n in self.nodes}

    def _read(self, root):
        flat = {}
        for e in root.iter():
            for k, v in attrs(e).items():
                flat.setdefault(k, v)
        for e in root.iter():
            tag, a = local(e.tag), attrs(e)
            if tag in ('nodes', 'node') and a.get('type'):
                label = next((attrs(c).get('string') for c in e if local(c.tag) == 'translation'
                              and attrs(c).get('string')), '') or a.get('name') or a.get('id', '')
                kind = kind_of(a['type'])
                self.nodes.append({'id': a.get('id') or label, 'label': label, 'type': a['type'], 'kind': kind,
                                   'props': {k: v for k, v in a.items() if k.lower() not in NOISE}})
            elif a.get('sourceNode') and a.get('targetNode'):
                self.edges.append({'from': a['sourceNode'], 'to': a['targetNode'],
                                   'out': a.get('sourceTerminalName', '').rsplit('.', 1)[-1],
                                   'into': a.get('targetTerminalName', '').rsplit('.', 1)[-1]})
                self.terminals_known = self.terminals_known or bool(a.get('sourceTerminalName'))
            elif tag.lower() == 'stickynote' or a.get('type', '').lower().endswith('stickynote'):
                self.notes.append(a.get('body') or ''.join(e.itertext()).strip())
            elif tag == 'propertyDescriptor' and a.get('propertyName'):
                self.udps[a['propertyName']] = flat.get(a['propertyName'], '')
            elif 'monitoringevent' in tag.lower():
                self.monitoring += 1
        self.nodes = [n for n in self.nodes if n['kind'] != 'Note']

    def _from_model(self, flow):
        for n in flow.get('nodes') or []:
            raw = n.get('properties') or {}
            props = {k: v for k, v in raw.items() if k.lower() not in NOISE}
            self.nodes.append({'id': n.get('id', ''), 'label': raw.get('name') or n.get('id', ''), 'type': n.get('type', ''),
                               'kind': kind_of(n.get('type')), 'props': props})
        self.edges = [{'from': c.get('from', ''), 'to': c.get('to', ''),
                       'out': str(c.get('sourceTerminal') or c.get('sourceTerminalName') or '').rsplit('.', 1)[-1],
                       'into': str(c.get('targetTerminal') or c.get('targetTerminalName') or '').rsplit('.', 1)[-1]}
                      for c in flow.get('connections') or []]
        self.terminals_known = any(e['out'] for e in self.edges)

    def label(self, node_id):
        return self.by_id.get(node_id, {}).get('label') or node_id

    def wired(self, node_id, terminal):
        return [e for e in self.edges if e['from'] == node_id and e['out'] == terminal]

    def targets(self, node_id):
        return [e for e in self.edges if e['from'] == node_id]

    def main_path(self):
        """The longest acyclic walk from an input node -- the spine the diagrams draw."""
        starts = [n['id'] for n in self.nodes if n['kind'] in INPUT_KINDS | {'Subflow Input'}] \
            or [n['id'] for n in self.nodes if not any(e['to'] == n['id'] for e in self.edges)][:1]
        best = []

        def walk(node, seen):
            nonlocal best
            seen = seen + [node]
            nxt = [e['to'] for e in self.targets(node)
                   if e['out'] in NORMAL or not self.terminals_known]
            nxt = [t for t in dict.fromkeys(nxt) if t not in seen]
            if not nxt and len(seen) > len(best):
                best = seen
            for t in nxt:
                if len(seen) < 40:
                    walk(t, seen)
        for s in starts:
            walk(s, [])
        return best

    def expanded_path(self, subs, depth=0):
        """The main path as ``(graph, node)`` pairs, each Subflow node replaced by the subflow's own path."""
        out = []
        for node_id in self.main_path():
            node = self.by_id.get(node_id)
            if not node:
                continue
            sub = sub_for(node, subs) if node['kind'] == 'Subflow' and depth < 4 else None
            if sub:
                out += [(g, n) for g, n in sub.expanded_path(subs, depth + 1) if n['kind'] not in ('Subflow Input', 'Subflow Output')]
            elif node['kind'] not in ('Subflow Input', 'Subflow Output'):
                out.append((self, node))
        return out

    def paths(self, limit=24):
        """Every route from an input node to a node with nothing after it, with the terminals taken."""
        out = []
        starts = [n['id'] for n in self.nodes if n['kind'] in INPUT_KINDS | {'Subflow Input'}]

        def walk(node, trail):
            outs = self.targets(node)
            if not outs:
                out.append(trail + [(node, '')])
                return
            for e in outs:
                if e['to'] in [t for t, _ in trail] or len(trail) > 30:
                    out.append(trail + [(node, e['out'])])
                    continue
                walk(e['to'], trail + [(node, e['out'])])
        for s in starts:
            walk(s, [])
        return out[:limit]


def sub_for(node, subs):
    """The subflow graph a Subflow node instantiates (type ``name.subflow:FCMComposite_1``)."""
    wanted = str(node['type']).split(':')[0].replace('\\', '/').split('/')[-1].lower()
    return next((g for g in subs if basename(g.path).lower() == wanted
                 or g.name.lower() == wanted.rsplit('.', 1)[0]), None)


# ---- ESQL ------------------------------------------------------------------------------------

MODULE = re.compile(r'(?i)^CREATE\s+(?:(?:COMPUTE|FILTER|DATABASE)\s+)?MODULE\s+([\w.]+)')
ROUTINE = re.compile(r'(?i)^CREATE\s+(?:EXTERNAL\s+)?(FUNCTION|PROCEDURE)\s+([\w.]+)')
JAVA = re.compile(r'(?i)LANGUAGE\s+JAVA.*?EXTERNAL\s+NAME\s+"?([\w.$]+)"?')


def code_lines(text):
    """``(line number, code)`` with ``--`` and ``/* */`` comments removed, quotes respected."""
    in_block = False
    for number, line in enumerate(text.splitlines(), 1):
        out, i, quote = [], 0, False
        while i < len(line):
            two = line[i:i + 2]
            if in_block:
                if two == '*/':
                    in_block, i = False, i + 2
                else:
                    i += 1
                continue
            ch = line[i]
            if ch == "'":
                quote = not quote
            if not quote and two == '--':
                break
            if not quote and two == '/*':
                in_block, i = True, i + 2
                continue
            out.append(ch)
            i += 1
        yield number, ''.join(out)


def statements(text):
    buf, start = [], None
    for number, code in code_lines(text):
        if not code.strip():
            continue
        if start is None:
            start = number
        buf.append(code.strip())
        # A MODULE header has no terminator of its own; without this it swallows the next statement.
        if (code.rstrip().endswith(';') or re.search(r'(?i)(?:\bTHEN|\bELSE|\bBEGIN|\bDO)\s*$', code.strip())
                or MODULE.match(code.strip())):
            yield start, number, ' '.join(buf)
            buf, start = [], None
    if buf:
        yield start, number, ' '.join(buf)


def short(text, n=110):
    text = re.sub(r'\s+', ' ', str(text)).strip()
    return text if len(text) <= n else text[:n - 1] + '…'


def norm_path(path):
    """A field path with indexes, type prefixes and namespace declarations dropped.

    A Compute node writes ``OutputLocalEnvironment`` and the next node reads it as ``LocalEnvironment``,
    so the two are one name here.
    """
    path = re.sub(r'\([^)]*\)', '', path)
    path = re.sub(r'\[[^\]]*\]|\{[^}]*\}', '', path)
    path = re.sub(r'\s+', '', path).strip('.')
    return re.sub(r'^(?:Output|Input)(LocalEnvironment)\b', r'\1', path)


def split_set(text):
    body = re.sub(r'(?i)^SET\s+', '', text).rstrip(';').strip()
    quote, depth = False, 0
    for i, ch in enumerate(body):
        if ch == "'":
            quote = not quote
        elif not quote and ch in '([{':
            depth += 1
        elif not quote and ch in ')]}':
            depth -= 1
        elif not quote and depth == 0 and ch == '=' and body[i - 1:i] not in '<>!':
            return body[:i].strip(), body[i + 1:].strip()
    return body, ''


def explain(text):
    """``(kind, meaning)`` for one statement. States what it does, not why."""
    text = text.strip().rstrip(';').strip()
    up = text.upper()
    if MODULE.match(text):
        return 'MODULE', 'Module entry. Bound to a node by esql://routine/#%s.' % MODULE.match(text).group(1)
    java = JAVA.search(text)
    if java:
        return 'JAVA', 'Routine implemented in Java (%s). The class must be on the integration server classpath.' % java.group(1)
    routine = ROUTINE.match(text)
    if routine:
        name = routine.group(2)
        if name.lower() == 'main':
            return 'ROUTINE', 'Entry point. Returning TRUE propagates the message to the Out terminal.'
        return 'ROUTINE', '%s %s.' % (routine.group(1).capitalize(), name)
    if up.startswith('DECLARE'):
        m = re.match(r'(?is)DECLARE\s+(\S+)\s+(?:(EXTERNAL|SHARED)\s+)?(.*?)\s*(?:;)?$', text)
        if not m:
            return 'DECLARE', 'Declaration.'
        name, qualifier, rest = m.group(1), (m.group(2) or '').upper(), m.group(3)
        if qualifier == 'EXTERNAL':
            return 'DECLARE', ('External variable. Its value is supplied by the user-defined property of the same '
                               'name (default %s).' % (rest.split(None, 1)[1] if ' ' in rest else "''"))
        if qualifier == 'SHARED':
            return 'DECLARE', 'Shared variable: one value held for every message in the flow.'
        if rest.upper().startswith('NAMESPACE'):
            return 'DECLARE', 'Namespace constant used as a prefix in path expressions.'
        if rest.upper().startswith('REFERENCE TO'):
            return 'DECLARE', 'Reference (pointer) to %s; no copy is made.' % short(rest[12:].strip(), 70)
        return 'DECLARE', 'Local variable %s.' % name
    if up.startswith('SET '):
        target, source = split_set(text)
        if not source:
            return 'SET', 'Assignment.'
        if re.match(r"^'[^']*'$", source):
            return 'SET', 'Sets %s to the literal %s.' % (short(target, 90), source)
        if re.match(r'(?i)^COALESCE\(', source):
            return 'SET', 'Sets %s to the first non-null of %s.' % (short(target, 90), short(source[9:-1], 70))
        if re.match(r'(?i)^CAST\(', source):
            return 'SET', 'Sets %s to a type conversion of %s.' % (short(target, 90), short(source[5:-1], 60))
        return 'SET', 'Sets %s from %s.' % (short(target, 90), short(source, 70))
    if re.match(r'(?i)^(ELSEIF|ELSE\s+IF)\b', up):
        return 'IF', 'Alternative branch: %s.' % short(re.sub(r'(?i)^ELSE\s*IF|ELSEIF|\bTHEN\s*$', '', text).strip(), 80)
    if up.startswith('IF '):
        return 'IF', 'Branch when %s.' % short(re.sub(r'(?i)^IF\s+|\s+THEN\s*$', '', text), 80)
    if up in ('ELSE', 'ELSE;'):
        return 'IF', 'Otherwise branch.'
    if up.startswith('WHILE '):
        return 'LOOP', 'Loop while %s.' % short(re.sub(r'(?i)^WHILE\s+|\s+DO\s*$', '', text), 80)
    if up.startswith('FOR '):
        return 'LOOP', 'Loop over %s.' % short(text[4:], 80)
    if up.startswith('CREATE '):
        return 'CREATE', 'Creates %s.' % short(text[7:], 90)
    if up.startswith('PROPAGATE'):
        return 'PROPAGATE', 'Sends the message on: %s.' % short(text[9:].strip() or 'to the Out terminal', 80)
    if up.startswith('RETURN'):
        verdict = up.rstrip(';').split()[-1] if len(up.split()) > 1 else ''
        return 'RETURN', {'TRUE': 'Returns TRUE: propagate to the Out terminal.',
                          'FALSE': 'Returns FALSE: the message is not propagated.'}.get(verdict, 'Returns %s.' % verdict)
    if up.startswith('CALL '):
        return 'CALL', 'Calls %s.' % short(text[5:], 90)
    if up.startswith('THROW'):
        return 'THROW', 'Raises an exception: %s.' % short(text[5:], 80)
    if up.startswith(('PASSTHRU', 'SELECT', 'INSERT', 'UPDATE', 'DELETE FROM')):
        return 'SQL', 'Database statement: %s.' % short(text, 80)
    if up.startswith(('MOVE ', 'DELETE ')):
        return 'MOVE', 'Moves or deletes in the message tree: %s.' % short(text, 80)
    if up.startswith('DECLARE HANDLER'):
        return 'HANDLER', 'Exception handler.'
    return 'OTHER', short(text, 90)


def java_class(name):
    """``pkg.Class.method`` -> ``pkg.Class``; ESQL declares the method, the jar holds the class."""
    head, _, tail = name.rpartition('.')
    return head if head and tail[:1].islower() and '.' in head else name


class Esql:
    """One ``.esql`` file read statement by statement."""

    def __init__(self, entry):
        self.path = entry.get('path', '')
        self.text = entry.get('source') or ''
        self.modules = list(entry.get('modules') or [])
        self.procedures = list(entry.get('procedures') or [])
        self.functions = list(entry.get('functions') or [])
        self.databases = list(entry.get('databases') or [])
        self.calls = list(entry.get('calls') or [])
        self.shared = list(entry.get('sharedVariables') or [])
        self.statements = []
        module = routine = ''
        for start, end, text in statements(self.text):
            m = MODULE.match(text)
            if m:
                module = m.group(1)
                if m.group(1) not in self.modules:
                    self.modules.append(m.group(1))
            r = ROUTINE.match(text)
            if r:
                routine = r.group(2)
            kind, meaning = explain(text)
            if re.match(r'(?i)^END\b', text):
                if re.match(r'(?i)^END\s+MODULE', text):
                    module = ''
                continue
            self.statements.append({'start': start, 'end': end, 'text': text, 'module': module,
                                    'routine': routine, 'kind': kind, 'meaning': meaning})
        self.java = sorted({java_class(m.group(1)) for s in self.statements if (m := JAVA.search(s['text']))})

    def sets(self):
        for s in self.statements:
            if s['kind'] == 'SET':
                target, source = split_set(s['text'])
                yield s, norm_path(target), source

    def headers(self):
        found = []
        for m in re.finditer(r'(?i)HTTP\w*Header\w*\s*\.\s*"?([\w-]+)"?|(?:HeaderRef|HdrRef)\s*\.\s*"([\w-]+)"', self.text):
            name = m.group(1) or m.group(2)
            if name and name not in found and name.lower() not in ('x',):
                found.append(name)
        return found


def module_for(node, graph, modules):
    """The ESQL module a Compute node runs: named in computeExpression, else IIB's default naming."""
    expr = prop(node['props'], 'computeExpression')
    m = re.search(r'#([\w.]+?)(?:\.Main)?$', expr) if expr else None
    if m and m.group(1) in modules:
        return m.group(1)
    default = re.sub(r'\W', '_', '%s_%s' % (graph.name, node['label']))
    return default if default in modules else (m.group(1) if m else '')


# ---- properties (BAR overrides) --------------------------------------------------------------

def read_properties(sources):
    """``{key: {env: value}}`` and the ordered list of environments, from ``<name>.<ENV>.properties``."""
    table, envs, files = {}, [], {}
    for rel, text, ext in sources:
        if ext != '.properties':
            continue
        name = stem(rel)
        env = name.rsplit('.', 1)[-1] if '.' in name else ''
        if not env:
            continue
        envs.append(env)
        files[env] = text
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                table.setdefault(k.strip(), {})[env] = v.strip()
    order = ('DEV', 'QA', 'UAT', 'STRESS', 'PROD')
    envs.sort(key=lambda e: (order.index(tier_of(e)) if tier_of(e) in order else len(order), e))
    return table, envs, files


def tier_of(env):
    up = env.upper()
    return next((t for t in ('STRESS', 'UAT', 'PROD', 'QA', 'DEV') if t in up), '')


def host_of(url):
    m = re.match(r'(?i)^[a-z]+://([^/:]+)', url or '')
    return m.group(1) if m else ''


def key_meaning(key):
    low = key.lower()
    if 'additionalinstances' in low:
        return 'Additional instances: threads for the flow = value + 1.'
    if 'timeoutforclient' in low:
        return 'Seconds the inbound node waits for the reply.'
    if 'timeoutforserver' in low:
        return 'Seconds the outbound node waits for the backend.'
    if 'testharness' in low:
        return 'Stub / test-harness URL (user-defined property).'
    if low.endswith('urlspecifier') or 'webserviceurl' in low:
        return 'Node URL property.'
    if low.endswith('url'):
        return 'URL (user-defined property).'
    if 'sslprotocol' in low:
        return 'Outbound TLS version.'
    if 'usehttps' in low:
        return 'HTTPS listener.'
    if 'validate' in low:
        return 'Message validation setting.'
    if 'queue' in low:
        return 'Queue name.'
    return 'Deploy-time override.'


# ---- the chapter ------------------------------------------------------------------------------

def describe_nodes(users):
    """``A, B (in 3 flows)`` for ``(graph, node)`` pairs: a label that repeats across files is said once."""
    seen = {}
    for g, n in users:
        seen.setdefault(n['label'], []).append(g.name)
    return ', '.join(l if len(gs) == 1 else '%s (in %d flows)' % (l, len(gs)) for l, gs in seen.items())


def describe_unwired(groups, limit=4):
    parts = []
    for labels, graphs in groups[:limit]:
        where = ', '.join(basename(g.path) for g in graphs) if len(graphs) <= 2 else '%d subflows' % len(graphs)
        parts.append('%s (%s)' % (', '.join(labels[:5]) + (' and %d more' % (len(labels) - 5) if len(labels) > 5 else ''), where))
    return '; '.join(parts) + (' and %d more groups' % (len(groups) - limit) if len(groups) > limit else '')


class Chapter:
    """One top-level flow with the subflows and ESQL it reaches."""

    def __init__(self, index, graph, subs, esqls, props):
        self.index, self.graph = index, graph
        self.name = graph.name
        self.subs = []
        queue, seen = [graph], {graph.path}
        while queue:
            g = queue.pop(0)
            for n in g.nodes:
                sub = sub_for(n, subs) if n['kind'] == 'Subflow' else None
                if sub and sub.path not in seen:
                    seen.add(sub.path)
                    self.subs.append(sub)
                    queue.append(sub)
        self.graphs = [graph] + self.subs
        self.nodes = [(g, n) for g in self.graphs for n in g.nodes]
        modules = {m: e for e in esqls for m in e.modules}
        self.modules = modules
        self.bindings = {}          # module -> [(graph, node)]
        for g, n in self.nodes:
            if n['kind'] in CODE_KINDS:
                mod = module_for(n, g, modules)
                if mod:
                    self.bindings.setdefault(mod, []).append((g, n))
        self.esqls = sorted({id(modules[m]): modules[m] for m in self.bindings if m in modules}.values(),
                            key=lambda e: e.path) or list(esqls)
        self.props = props

    def of(self, *kinds):
        return [(g, n) for g, n in self.nodes if n['kind'] in kinds]

    def unwired_groups(self):
        """``[(labels, [graph, ...])]``: nodes whose failure terminal goes nowhere, one entry per distinct set.

        The three error-handler subflows are copies of each other, so naming each separately says the same
        thing three times.
        """
        groups = {}
        for g in self.graphs:
            if g.terminals_known:
                labels = tuple(dict.fromkeys(n['label'] for n in g.nodes
                                             if n['kind'] in FAILURE_KINDS and not g.wired(n['id'], 'failure')))
                if labels:
                    groups.setdefault(labels, []).append(g)
        return list(groups.items())

    @property
    def inputs(self):
        return self.of(*INPUT_KINDS)

    @property
    def calls(self):
        return self.of(*CALL_KINDS)

    def udps(self):
        out = {}
        for g in self.graphs:
            out.update(g.udps)
        for e in self.esqls:
            for s in e.statements:
                m = re.match(r"(?is)DECLARE\s+(\S+)\s+EXTERNAL\s+\S+(?:\s+(.*?))?;?$", s['text'])
                if m:
                    out.setdefault(m.group(1), (m.group(2) or '').strip().strip("'"))
        return out

    def inbound(self):
        for g, n in self.inputs:
            p = n['props']
            return {'kind': n['kind'], 'node': n['label'], 'url': prop(p, 'URLSpecifier', 'queueName', 'topicName',
                                                                        'inputDirectory'),
                    'https': prop(p, 'useHTTPS').lower() in ('true', 'yes'),
                    'domain': prop(p, 'messageDomainProperty'), 'timeout': prop(p, 'timeoutForClient'),
                    'security': prop(p, 'securityProfileName')}
        return {}

    def protocols(self):
        proto = {'HTTP Input': 'HTTP', 'HTTP Request': 'HTTP', 'SOAP Input': 'SOAP', 'SOAP Request': 'SOAP',
                 'MQ Input': 'MQ', 'MQ Output': 'MQ', 'MQ Get': 'MQ', 'JMS Input': 'JMS', 'JMS Output': 'JMS',
                 'Kafka Consumer': 'Kafka', 'Kafka Producer': 'Kafka', 'File Input': 'File', 'File Output': 'File',
                 'Database': 'Database', 'Database Route': 'Database', 'Timeout Notification': 'Timer'}
        ins = [proto[n['kind']] for _, n in self.inputs if n['kind'] in proto]
        outs = [proto[n['kind']] for _, n in self.calls if n['kind'] in proto]
        return (ins[0] if ins else ''), list(dict.fromkeys(outs))

    def pattern(self):
        inbound, outs = self.protocols()
        xml_text = ' '.join(e.text for e in self.esqls).lower()
        soap = 'SOAP' in outs or ('soapenv' in xml_text and 'HTTP' in outs)
        json_in = inbound == 'HTTP' and self.inbound().get('domain', '').upper() in ('JSON', '')
        left = 'REST API' if json_in and inbound == 'HTTP' else inbound or 'Unknown trigger'
        right = ('SOAP' if soap else ' / '.join(outs)) if outs else ''
        return ('%s to %s facade' % (left, right) if soap and inbound == 'HTTP'
                else '%s to %s' % (left, right) if right else left)


class Report:
    """Everything the page prints, as plain data."""

    def __init__(self, model, sources=(), files=(), meta=None, unsupported=()):
        self.model, self.meta = model or {}, dict(meta or {})
        self.files = list(files)
        self.sources = list(sources)
        self.unsupported = list(unsupported)
        # A model-authored semantic model keeps structure but not the files' text; the deterministic parse keeps both.
        # Everything line-level in the report needs the text, so it is read from the repository files by path.
        raw = {key_of(rel): text for rel, text, _ in self.sources}
        def with_text(item):
            return item if item.get('source') else dict(item, source=raw.get(key_of(item.get('path', '')), ''))
        self.subs = [Graph(with_text(f), True) for f in self.model.get('subflows') or []]
        flows = [Graph(with_text(f), False) for f in self.model.get('flows') or []]
        if not flows and self.subs:             # a library of subflows: report them as the flows
            flows, self.subs = self.subs, []
        self.esqls = [Esql(with_text(e)) for e in self.model.get('esql') or []]
        self.table, self.envs, self.env_files = read_properties(self.sources)
        self.chapters = [Chapter(i + 1, g, self.subs, self.esqls, self.table) for i, g in enumerate(flows)]
        self.findings = []
        for c in self.chapters:
            self._detect(c)
        self._from_model()

    # -- findings -------------------------------------------------------------------------------

    def add(self, sev, finding, evidence, action):
        self.findings.append({'id': 'F%d' % (len(self.findings) + 1), 'sev': sev, 'finding': finding,
                              'evidence': evidence, 'action': action})

    def _detect(self, c):
        table = self.table
        for g in c.graphs:
            if not g.terminals_known:
                continue
            for n in g.nodes:
                if n['kind'] in INPUT_KINDS and n['kind'] != 'Timeout Notification':
                    missing = [t for t in ('catch', 'failure') if not g.wired(n['id'], t)]
                    if missing:
                        self.add('Med', '%s %s has no handler wired to its %s terminal%s, so those errors reach the '
                                 'caller as the product default.' % (n['kind'], n['label'], '/'.join(missing),
                                                                     's' if len(missing) > 1 else ''),
                                 'msgflow %s: %s' % (basename(g.path), n['label']),
                                 'Wire a handler that returns a controlled error body.')
        groups = c.unwired_groups()
        if groups:
            self.add('Low', 'Failure terminal not wired on %s. An exception there propagates to the enclosing catch '
                     'handler instead of being handled locally.' % describe_unwired(groups),
                     'msgflow/subflow terminals', 'Confirm the propagation is intended; otherwise wire the terminal.')
        for mod, users in c.bindings.items():
            if len(users) > 1:
                self.add('Low', 'ESQL module %s is shared by %d nodes (%s). A change to it affects every one of them.'
                         % (mod, len(users), describe_nodes(users)),
                         'esql %s' % basename(c.modules[mod].path) if mod in c.modules else 'esql',
                         'Change it once, then regression-test each path that uses it.')
        for e in c.esqls:
            for name in e.java:
                self.add('Med', 'Runtime depends on Java class %s, which is not in this repository.' % name,
                         'esql %s' % basename(e.path), 'Obtain the jar and version; document what it does.')
            for s in e.statements:
                if re.search(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*=\s*'[^']+'", s['text']) \
                        and s['kind'] == 'SET':
                    self.add('High', 'A credential-like value is set from a literal in ESQL.',
                             'esql %s L%d' % (basename(e.path), s['start']),
                             'Move it to a vault or policy; never ship it in source.')
                if s['kind'] == 'SQL' and '||' in s['text'] and 'PASSTHRU' in s['text'].upper():
                    self.add('High', 'PASSTHRU builds SQL by string concatenation.',
                             'esql %s L%d' % (basename(e.path), s['start']), 'Use ? parameter markers.')
            unused = []
            for s in e.statements:
                m = re.match(r'(?is)DECLARE\s+(\w+)\s+(?!EXTERNAL|NAMESPACE|SHARED)', s['text'])
                if m and len(re.findall(r'\b%s\b' % re.escape(m.group(1)), e.text, re.I)) == 1:
                    unused.append(m.group(1))
            never = [p for p in e.procedures + e.functions
                     if p.split('.')[-1].lower() != 'main' and p not in e.calls
                     and len(re.findall(r'\b%s\b' % re.escape(p), e.text, re.I)) == 1]
            if unused or never:
                self.add('Low', 'Unused %s.' % '; '.join(x for x in (
                    'variables ' + ', '.join(unused[:8]) if unused else '',
                    'procedures/functions ' + ', '.join(never[:8]) if never else '') if x),
                    'esql %s' % basename(e.path), 'Remove during migration.')
            # Environment is scratch space only ESQL reads, so a value nobody reads is dead. LocalEnvironment is
            # not judged: the next node (HTTP Request, HTTP Reply ...) consumes Destination.* without ESQL showing it.
            written, read = {}, {}
            for s, target, _ in e.sets():
                if target.startswith('Environment.'):
                    written.setdefault(target, s['start'])
            for target in written:
                refs = [s for other in c.esqls for s in other.statements if target in norm_path(s['text'])
                        and not (s['kind'] == 'SET' and norm_path(split_set(s['text'])[0]) == target)]
                if not refs:
                    read[target] = written[target]
            if read:
                self.add('Low', 'Written but never read: %s.' % ', '.join(sorted(read)[:6]),
                         'esql %s L%d' % (basename(e.path), min(read.values())), 'Remove or use it.')
        client, server = None, None
        for _, n in c.calls:
            server = prop(n['props'], 'timeoutForServer') or server
        for _, n in c.inputs:
            client = prop(n['props'], 'timeoutForClient') or client
        for key, vals in table.items():
            if key.lower().endswith('timeoutforserver'):
                server = next(iter(vals.values()), server)
            if key.lower().endswith('timeoutforclient'):
                client = next(iter(vals.values()), client)
        try:
            if client and server and int(server) >= int(client):
                self.add('Med', 'Backend time-out (%ss) is not below the client time-out (%ss), so the caller can '
                         'give up before the backend does.' % (server, client), 'timeoutForServer / timeoutForClient',
                         'Keep the backend time-out below the client time-out.')
        except ValueError:
            pass
        for key, vals in table.items():
            if 'testharness' in key.lower():
                live = {e: v for e, v in vals.items() if v and 'dummy' not in v.lower()}
                prod = [e for e in live if tier_of(e) == 'PROD']
                if prod:
                    self.add('High', 'A test-harness / stub URL (%s) is configured in %s. Anyone who can trigger the '
                             'stub route gets stub data instead of real data.' % (key.rsplit('#', 1)[-1], ', '.join(prod)),
                             '%s in %s properties' % (key, '/'.join(prod)),
                             'Remove the URL in production or block the trigger at the gateway.')
        for key, vals in table.items():
            if key.lower().endswith('url') and 'testharness' not in key.lower():
                for env, value in vals.items():
                    host, tier = host_of(value).lower(), tier_of(env)
                    if not host or not tier:
                        continue
                    bad = (tier == 'PROD' and any(t in host for t in NON_PROD)) or \
                          (tier != 'PROD' and any(t in host for t in sum((ENV_TIERS[x] for x in ENV_TIERS if x != tier), ()))
                           and not any(t in host for t in ENV_TIERS[tier]))
                    if bad:
                        self.add('Med', '%s points at %s, which looks like another environment.' % (env, host),
                                 '%s property file, key %s' % (env, key), 'Confirm this is intended.')
        if self.envs:
            for key, vals in table.items():
                absent = [e for e in self.envs if e not in vals]
                if absent and len(absent) < len(self.envs):
                    self.add('Low', 'Override key %s is missing from %s.' % (key, ', '.join(absent)),
                             'properties files', 'Add it or confirm the default is wanted.')
        udps = c.udps()
        dummy = {k: v for k, v in udps.items() if re.search(r'(?i)dummy|localhost|example\.com', str(v))}
        if dummy:
            self.add('Med', 'Defaults for %s are placeholder URLs. An environment with no property file would run '
                     'with them.' % ', '.join(sorted(dummy)), 'subflow/ESQL defaults',
                     'Block deployment where no override exists.')
        for g, n in c.inputs:
            if n['kind'] == 'HTTP Input' and not prop(n['props'], 'securityProfileName'):
                self.add('Low', 'No security profile on %s. Authentication is expected at the API gateway [I].' % n['label'],
                         'msgflow %s: %s' % (basename(g.path), n['label']), 'Confirm the gateway policy.')
                break

    def _from_model(self):
        for a in self.model.get('ambiguities') or []:
            if isinstance(a, dict):
                impact = str(a.get('impact') or '')
                sev = 'Med' if re.search(r'(?i)block|manual|critical|incomplete', impact) else 'Low'
                self.add(sev, 'Open question: %s' % a.get('question', ''), a.get('evidenceReference') or '—',
                         'Resolve with %s%s.' % (a.get('owner') or 'the ACE SME', ' (impact: %s)' % impact if impact else ''))
        # The same construct type recurs wherever it is used; one row each, with where.
        grouped = {}
        for u in self.unsupported:
            grouped.setdefault((u.get('construct_type'), u.get('classification'), u.get('impact')), []).append(u)
        for (kind, classification, impact), rows in grouped.items():
            refs = [r.get('evidence_ref') or r.get('source_path') or '' for r in rows]
            self.add('Med', 'Unsupported construct %s (%s)%s.' % (kind, classification,
                                                                  ', %d occurrences' % len(rows) if len(rows) > 1 else ''),
                     '; '.join(refs[:3]) + (' and %d more' % (len(refs) - 3) if len(refs) > 3 else ''),
                     impact or 'Manual mapping or plug-in development required.')

    # -- facts the sections share ---------------------------------------------------------------

    def not_in_repo(self):
        """What the code needs that the repository does not hold, with the tag [N]."""
        out = []
        have = {basename(f[0]).lower() for f in self.files}
        for c in self.chapters:
            for e in c.esqls:
                for name in e.java:
                    out.append('the Java jar containing %s' % name)
            text = ' '.join(g.text for g in c.graphs) + ' ' + ' '.join(e.text for e in c.esqls)
            for ref in sorted(set(re.findall(r'[\w./-]+\.(?:xsd|wsdl)', text))):
                if basename(ref).lower() not in have:
                    out.append('the schema %s' % basename(ref))
            if any(prop(n['props'], 'useHTTPS').lower() in ('true', 'yes') or prop(n['props'], 'sslProtocol')
                   for _, n in c.nodes):
                out.append('integration-server configuration (HTTPS listener, keystore/truststore)')
            if c.of('MQ Input', 'MQ Output', 'MQ Get'):
                out.append('queue manager definitions and channel security')
            if c.of('Database', 'Database Route') or any(e.databases for e in c.esqls):
                out.append('the ODBC/JDBC data-source definitions')
            if any(g.monitoring for g in c.graphs):
                out.append('the monitoring event consumer')
        out.append('the API gateway / API Connect product and plan definition')
        return list(dict.fromkeys(out))
