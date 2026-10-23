"""Apply the generated-code analyses back into the generated code.

``code-generation`` leaves its findings in ``quality_runs``: the checks there
are what "analysing the generated code" actually produces. Three of them fail
for reasons that can be repaired mechanically *and*, crucially, proven repaired
by re-running the very same gate:

* ``SECRET_SCAN`` -- a credential-shaped key assigned a literal value.
* ``HARDCODED_ENDPOINT_SCAN`` -- an absolute http(s) URL baked into source.
* ``PROVENANCE`` -- the provenance manifest missing from the project root.

Each fix is written into the files and then re-validated, so the agent never
claims a repair the gate does not confirm. That is the whole point of looping:
the evidence is the gate's own verdict after the edit, not the fixer's opinion
of it.

What this deliberately does **not** touch is behavioural variance --
``differential_evidence``, ``variances``, ``root_causes``. Those describe the
migrated service behaving differently from ACE, which needs an understanding of
the source system that no regex has. They remain the remediation agent's
business, which proposes rather than writes, and they are reported here as
unresolved rather than quietly counted as fixed.

Only the file types ``quality_validate`` itself scans are edited
(``.java``/``.yml``/``.yaml``/``.properties``), so the fixer and the gate can
never disagree about which files are in scope. ``pom.xml`` is outside that set,
which is what keeps the Maven POM namespace URLs from being rewritten.
``HARDCODED_ENDPOINT_SCAN`` further exempts externalized deployment config
(``is_externalized_config``) in both the fixer and the gate, for the same
reason -- a real backend host in ``config/config-map/*.properties`` or
``application-local.properties`` is the intended externalization, not a
violation of it.
"""
import json
import os
import re
from pathlib import Path

from app.repository import now
from app.services import dependency_jars, skill_repair

#: Exactly the suffixes and size bound ``quality_validate`` reads, so a fix
#: cannot miss a file the gate will go on to scan, or edit one it will not.
SCANNED_SUFFIXES = ('.java', '.yml', '.yaml', '.properties')
MAX_FILE_BYTES = 2_000_000

#: ``quality_validate``'s secret rule is
#: ``(?i)(password|secret|token)\s*[:=]\s*["'][^$][^"']+`` -- it requires a
#: non-``$`` character straight after the opening quote. Replacing the literal
#: with a ``${...}`` placeholder is therefore both the correct intent
#: (externalised configuration) and what clears the check.
_SECRET = re.compile(r'(?i)((password|secret|token)\s*[:=]\s*)(["\'])(?!\$)[^"\']+(["\'])')
#: The endpoint rule is ``https?://(?!localhost|example)``: loopback and
#: example hosts are permitted, so the same lookahead is used here and those
#: URLs are left alone.
_URL = re.compile(r'https?://(?!localhost|example)[^\s"\'<>)\]]*')

FIXABLE = ('SECRET_SCAN', 'HARDCODED_ENDPOINT_SCAN', 'PROVENANCE')

#: Redaction applied before any source reaches the model gateway.
#:
#: ``AIRegistryService.gateway_request`` refuses a payload outright when its DLP
#: rules match, and CREDENTIAL_ASSIGNMENT is
#: ``\b(password|secret|api_key|...)\b\s*[:=]\s*\S`` -- which matches *any*
#: ``password:`` line, including one already correctly externalised as
#: ``password: "${PASSWORD}"``. A Spring application.yml would therefore be
#: refused every time, so sending files verbatim is not an option. Values are
#: blanked here instead; the structure the model needs to reason about survives.
#:
#: This is also why the model is asked for targeted search/replace edits rather
#: than whole-file content: it never has to reproduce bytes it was not shown,
#: and an ``old_str`` that does not match the real file is simply skipped.
_REDACTIONS = (
    # The whole assignment goes, keyword and separator included. Blanking only
    # the value is not enough: the rule is ``<keyword>\s*[:=]\s*\S``, so
    # ``password: <REDACTED>`` still matches on the ``<``, and leaving the line
    # bare still matches because ``\s*`` crosses the newline onto the next
    # line's first character.
    (re.compile(r'(?i)\b(?:password|passwd|secret|api[_-]?key|client_secret|'
                r'access[_-]?token|private[_-]?key)\b\s*[:=]\s*'
                r'("[^"]*"|\'[^\']*\'|\S+)'), 'REDACTED_CREDENTIAL'),
    (re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._-]{16,}'), 'bearer <REDACTED>'),
    (re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'), '<REDACTED_JWT>'),
    (re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://)[^\s:@/]+:[^\s:@/]+@'), r'\1<REDACTED>@'),
    (re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY'), '<REDACTED_PRIVATE_KEY>'),
    (re.compile(r'\b(?:\d[ -]?){13,19}\b'), '<REDACTED_NUMBER>'),
)

#: Caps on what is sent to, and accepted back from, the model. A pathological
#: response must not be able to rewrite a project wholesale.
MAX_PAYLOAD_FILES = 12
MAX_FILE_CHARS = 6000
MAX_CITED_FILE_CHARS = 30000
MAX_EDITS = 20
MAX_NEW_STR_CHARS = 20000
NL = chr(10)


#: The build is judged in stages, because a failing build is a sequence of problems: it
#: cannot start until it compiles, and a compile failure with 20 errors reduced to 5
#: is real progress that the pass/fail verdict cannot see.
BUILD_CHECKS = ('COMPILE_AND_UNIT_TEST', 'STARTUP_SMOKE')


def _build_errors(check):
    evidence = (check or {}).get('evidence')
    return len(evidence.get('errors') or []) if isinstance(evidence, dict) else 0


def _compiler_ran(check):
    """Whether the build got far enough to actually invoke the compiler.

    Observed live: a pom Maven cannot even read (an undefined ``${property}``
    in a dependency version, same shape for any other pom defect) fails
    before any plugin runs, with a short, fixed shape ("Some problems were
    encountered while processing the POMs", "The build could not read N
    project(s)") -- categorically earlier than a real compilation failure,
    which always prints "COMPILATION ERROR" because the compiler plugin
    actually ran (``app/services/build_check.py``'s own ``_ERROR`` pattern
    matches that literal string). Treating both as the same stage and
    breaking the tie by raw error-line count compares unlike things: a real
    compile failure against several source files naturally has many more
    lines than a short pom-read failure, so an edit that made the pom
    readable -- genuine, necessary progress -- looked like it made things
    worse and was reverted, on every attempt, every time.
    """
    evidence = (check or {}).get('evidence')
    errors = evidence.get('errors') if isinstance(evidence, dict) else None
    return any('COMPILATION ERROR' in str(line) for line in (errors or []))


def _unresolved_symbols(check):
    """Whether an import or type the compiler could not even resolve is still in the way.

    Observed live, directly downstream of the pom-readability fix above:
    clearing the *last* "cannot find symbol"/"package ... does not exist"
    error in a file can surface several new, real errors in the very same
    build -- a wrong return type on an ``@Override``, a method call with the
    wrong argument count -- because javac cannot type-check an expression
    that depends on a symbol it never resolved in the first place. Fixing the
    one import that was blocking all of that analysis is still strictly
    forward progress, even though the error *line count* goes up the moment
    the deeper, previously-invisible problems become checkable at all.
    """
    evidence = (check or {}).get('evidence')
    errors = evidence.get('errors') if isinstance(evidence, dict) else None
    return any('cannot find symbol' in str(line) or 'does not exist' in str(line)
              for line in (errors or []))


def score(checks):
    """(open non-build checks, build stages passed, build errors left) for one gate run.

    ``stage`` is 0 while the pom cannot even be read, 1 once the compiler runs
    but a symbol/import it cannot resolve is still in the way, 2 once every
    symbol resolves and only type-level mismatches remain, 3 once it compiles
    and its tests pass but the service does not start, 4 when it also starts
    (or no build was run).
    """
    by_name = {c['name']: c for c in checks}
    others = frozenset(c['name'] for c in checks
                       if c['status'] in ('FAIL', 'GAP') and c['name'] not in BUILD_CHECKS)
    compile_ = by_name.get('COMPILE_AND_UNIT_TEST', {})
    start = by_name.get('STARTUP_SMOKE', {})
    if compile_.get('status') == 'FAIL':
        if not _compiler_ran(compile_):
            stage = 0
        elif _unresolved_symbols(compile_):
            stage = 1
        else:
            stage = 2
        errors = _build_errors(compile_)
    elif start.get('status') == 'FAIL':
        stage, errors = 3, _build_errors(start)
    else:
        stage, errors = 4, 0
    return others, stage, errors


def improved(before, after):
    """Whether ``after`` is strictly better than ``before``, and worse in nothing.

    Nothing that was passing may start failing; then something must have got
    better -- a check closed, the build got a stage further, or it has fewer errors.
    This replaces "the set of failing checks shrinks", under which a repair that
    took the build from 20 errors to 5 was thrown away because the build still failed.
    """
    others_b, stage_b, errors_b = before
    others_a, stage_a, errors_a = after
    if not others_a <= others_b or stage_a < stage_b:
        return False
    return (others_a < others_b or stage_a > stage_b
            or (stage_a == stage_b and errors_a < errors_b))


def redact(text):
    """Blank anything the gateway's DLP rules would refuse."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


#: Externalized deployment config -- ``config-map/`` is Kubernetes-ConfigMap
#: -injected at deploy time, never baked into the jar, and
#: ``application-local.*`` is the local-only dev profile, never deployed as
#: prod config -- so a literal backend host there is the externalization
#: HARDCODED_ENDPOINT_SCAN exists to require, not a violation of it (confirmed
#: against the real reference service's own config-map/*.properties and
#: application-local.properties, which both carry a literal, real backend
#: host by design). The base application.properties/.yml bundled inside the
#: jar is deliberately NOT exempt: a literal there really is shipped in the
#: artifact. ``quality_validate`` imports this so the gate and this fixer can
#: never disagree about which files the endpoint rule covers.
def is_externalized_config(path):
    path = Path(path)
    return 'config-map' in path.parts or path.name.startswith('application-local.')


def _files(root):
    for f in sorted(Path(root).rglob('*')):
        if (f.is_file() and f.suffix.lower() in SCANNED_SUFFIXES
                and f.stat().st_size < MAX_FILE_BYTES):
            yield f


#: Overridable the same way ``MOJO_MODEL_*``/``MOJO_MAX_TOKENS`` are in
#: ``app/providers/cvscode_adapter.py`` -- a deployment whose local Maven cache
#: lives somewhere other than the default.
def _m2_root():
    return Path(os.environ.get('MOJO_M2_REPO') or (Path.home() / '.m2' / 'repository'))


_PACKAGE_DECL = re.compile(r'(?m)^\s*package\s+([\w.]+)\s*;')
_TYPE_DECL = re.compile(r'(?m)^\s*(?:public\s+|final\s+|abstract\s+|static\s+)*'
                        r'(?:class|interface|record|enum)\s+(\w+)')
_IMPORT = re.compile(r'(?m)^\s*import\s+([\w.]+)\.(\w+)\s*;')


def _local_classes(root):
    """``{simple_name: [fqn, ...]}`` for every type this project's own ``.java`` files declare.

    The other half of "refer the real project" alongside ``dependency_jars``:
    what the model itself already wrote, read back rather than assumed.
    """
    merged = {}
    for f in Path(root).rglob('*.java'):
        if f.stat().st_size >= MAX_FILE_BYTES:
            continue
        text = f.read_text(errors='ignore')
        package = _PACKAGE_DECL.search(text)
        if not package:
            continue
        for name in set(_TYPE_DECL.findall(text)):
            fqn = package.group(1) + '.' + name
            bucket = merged.setdefault(name, [])
            if fqn not in bucket:
                bucket.append(fqn)
    return merged


#: A class/interface header, with whatever it extends/implements -- the two
#: ends of the contract a cross-file method-signature mismatch always breaks.
_CLASS_DECL = re.compile(
    r'(?:public\s+|final\s+|abstract\s+)*(class|interface)\s+(\w+)'
    r'(?:\s+extends\s+([\w.]+))?(?:\s+implements\s+([\w.,\s]+?))?\s*\{')
#: A field's declared type -- the other common way a file depends on a
#: sibling's real signature (``this.connectorService.getClaimStatus(...)``).
_FIELD_TYPE = re.compile(
    r'(?m)^\s*(?:private|protected|public)\s+(?:final\s+)?(\w+)(?:<[^<>]*>)?\s+(\w+)\s*[;=]')
#: Where a method declaration starts -- return type and name only. The
#: parameter list is found separately (``_balanced_parens``) because it
#: routinely contains its own nested parens (``@Parameter(description = "...")``
#: on every argument of an interface method is the normal shape here, and a
#: naive ``\(([^()]*)\)`` capture breaks on the first one).
_METHOD_START = re.compile(
    r'(?<=[;}\n])\s*(?:public\s+|protected\s+)?(?:static\s+)?'
    r'((?:[A-Z]\w*|void|boolean|int|long|double|float|char|byte|short)(?:<[^<>]*>)?(?:\[\])?)\s+'
    r'(\w+)\s*\(')


def _balanced_parens(text, open_pos):
    """The span from ``text[open_pos] == '('`` to its true matching ``)``.

    String-aware for the same reason ``cvscode_adapter.py``'s ``_span`` is: a
    quoted ``@Parameter(example = "6003~123^^1")`` value could otherwise be
    mistaken for structure.
    """
    depth, in_string, escaped = 0, False, False
    for i in range(open_pos, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return text[open_pos:i + 1]
    return ''


def _split_top_level_commas(params):
    """Split a parameter list on commas that are not nested inside ``<...>`` or ``(...)``."""
    parts, current, angle, paren = [], '', 0, 0
    for ch in params:
        if ch == '<':
            angle += 1
        elif ch == '>':
            angle = max(0, angle - 1)
        elif ch == '(':
            paren += 1
        elif ch == ')':
            paren = max(0, paren - 1)
        if ch == ',' and angle == 0 and paren == 0:
            parts.append(current)
            current = ''
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


def _strip_param_annotations(param):
    """Drop every leading ``@Annotation`` / ``@Annotation(...)`` before the type and name."""
    param = param.strip()
    leading = re.compile(r'^@\w+(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?\s*')
    while True:
        m = leading.match(param)
        if not m:
            return param
        param = param[m.end():].strip()


def _param_types(params_blob):
    types = []
    for raw in _split_top_level_commas(params_blob):
        tokens = _strip_param_annotations(raw).split()
        if len(tokens) >= 2:
            types.append(tokens[-2])
        elif tokens:
            types.append(tokens[0])
    return types


def _methods_in(text):
    """``[(return_type, name, [param_type, ...]), ...]`` for every method this source declares.

    Matches an interface method (ends ``;``, no body, no modifier at all) the
    same as a class method (ends ``{``) -- both are real contracts a sibling
    file's call or ``@Override`` must agree with.
    """
    found = []
    for m in _METHOD_START.finditer(text):
        return_type, name = m.group(1), m.group(2)
        open_pos = m.end() - 1
        span = _balanced_parens(text, open_pos)
        if not span:
            continue
        after = text[open_pos + len(span):].lstrip()
        if not (after.startswith('{') or after.startswith(';')):
            continue
        found.append((return_type, name, _param_types(span[1:-1])))
    return found


def _local_contracts(root):
    """``{simple_name: {'kind', 'implements_extends', 'methods'}}`` for every project class/interface.

    ``methods`` is ``{method_name: ['ReturnType methodName(Type, Type, ...)', ...]}``
    -- a real signature string per overload, read back from the source rather
    than assumed. This is what lets a cross-file signature mismatch (a
    controller's ``@Override`` disagreeing with its own ``Api`` interface, a
    service calling its connector with the wrong argument shape) be corrected
    with the real answer instead of another guess.
    """
    contracts = {}
    for f in Path(root).rglob('*.java'):
        if f.stat().st_size >= MAX_FILE_BYTES:
            continue
        text = f.read_text(errors='ignore')
        cls = _CLASS_DECL.search(text)
        if not cls:
            continue
        kind, name, extends, implements = cls.groups()
        targets = []
        if extends:
            targets.append(extends.strip())
        if implements:
            targets += [x.strip() for x in implements.split(',') if x.strip()]
        methods = {}
        for return_type, mname, types in _methods_in(text):
            sig = '%s %s(%s)' % (return_type, mname, ', '.join(types))
            methods.setdefault(mname, [])
            if sig not in methods[mname]:
                methods[mname].append(sig)
        contracts[name] = {'kind': kind, 'implements_extends': targets, 'methods': methods}
    return contracts


def accept_edit(root, edit):
    """Validate one proposed search/replace edit, returning the resolved file or a refusal.

    Model output is untrusted data, so the path is re-resolved under the
    project root and the suffix re-checked -- a traversal or a stray
    ``pom.xml`` edit is refused rather than applied. ``old_str`` must appear
    exactly once in the *real* file: zero means the model was working from
    redacted bytes and the edit cannot be placed, more than one means it is
    ambiguous and could change the wrong site.

    Shared between ``CodeFeedbackService`` (repairing a failing quality check)
    and code-generation's business-logic synthesis (filling in a
    HAND_WRITTEN_PROTECTED stub) -- both apply a model's proposed edits to
    governed generated source, and a path-traversal or ambiguous-match check
    must not have two slightly different implementations to drift apart.
    """
    rel = str(edit.get('path', '')).strip().replace('\\', '/')
    if not rel or rel.startswith('/') or '..' in rel.split('/'):
        return None, 'path_not_relative:%s' % rel
    base = Path(root).resolve()
    try:
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return None, 'path_unresolvable:%s' % rel
    if target != base and base not in target.parents:
        return None, 'path_outside_project:%s' % rel
    if target.suffix.lower() not in SCANNED_SUFFIXES and target.name != 'pom.xml':
        return None, 'suffix_not_editable:%s' % rel
    if not target.is_file():
        return None, 'file_not_found:%s' % rel
    old = str(edit.get('old_str', ''))
    new = str(edit.get('new_str', ''))
    if not old:
        return None, 'old_str_empty:%s' % rel
    if len(new) > MAX_NEW_STR_CHARS:
        return None, 'new_str_too_large:%s' % rel
    text = target.read_text(errors='ignore')
    occurrences = text.count(old)
    if occurrences == 0:
        return None, 'old_str_absent:%s' % rel
    if occurrences > 1:
        return None, 'old_str_ambiguous:%s' % rel
    return target, ''


class CodeFeedbackService:
    def __init__(self, repo):
        self.repo = repo

    # -- individual repairs --------------------------------------------------
    def _fix_secrets(self, root):
        changed = []
        for f in _files(root):
            text = f.read_text(errors='ignore')
            new = _SECRET.sub(
                lambda m: '%s%s${%s}%s' % (m.group(1), m.group(3), m.group(2).upper(), m.group(4)),
                text)
            if new != text:
                f.write_text(new)
                changed.append({'path': str(f), 'check': 'SECRET_SCAN',
                                'action': 'replaced embedded credential with a ${...} placeholder'})
        return changed

    def _fix_endpoints(self, root):
        changed = []
        for f in _files(root):
            if is_externalized_config(f):
                continue
            text = f.read_text(errors='ignore')
            new = _URL.sub('${EXTERNAL_ENDPOINT}', text)
            if new != text:
                f.write_text(new)
                changed.append({'path': str(f), 'check': 'HARDCODED_ENDPOINT_SCAN',
                                'action': 'externalised absolute endpoint to ${EXTERNAL_ENDPOINT}'})
        return changed

    def _fix_provenance(self, root, job_id, plan_id):
        target = Path(root) / 'provenance-manifest.json'
        if target.exists():
            return []
        plan = self.repo.one('select * from migration_plans where id=?', (plan_id,))
        manifest = {
            'plan_id': plan_id,
            'analysis_id': (plan or {}).get('analysis_id', ''),
            'specification_id': (plan or {}).get('specification_id', ''),
            'framework_id': (plan or {}).get('framework_id', ''),
            'files': [{'path': str(p.relative_to(Path(root)))} for p in _files(root)],
            'generated_at': now(),
            'reconstructed_by': 'feedback-agent',
        }
        target.write_text(json.dumps(manifest, indent=2))
        return [{'path': str(target), 'check': 'PROVENANCE',
                 'action': 'reconstructed the missing provenance manifest'}]

    def _repair(self, failed, root, job_id, plan_id):
        fixes = []
        if 'SECRET_SCAN' in failed:
            fixes += self._fix_secrets(root)
        if 'HARDCODED_ENDPOINT_SCAN' in failed:
            fixes += self._fix_endpoints(root)
        if 'PROVENANCE' in failed:
            fixes += self._fix_provenance(root, job_id, plan_id)
        # The skill's GAPs whose fix the skill spells out (skill_repair.REPAIRABLE).
        if any(name in skill_repair.REPAIRABLE for name in failed):
            fixes += skill_repair.repair(root, failed)
        return fixes

    # -- model-assisted repair -----------------------------------------------
    @staticmethod
    def _evidence_text(check):
        """What a failing check says, in words the model can act on."""
        ev = check.get('evidence', '')
        if isinstance(ev, dict):
            lines = list(ev.get('errors') or [])
            if ev.get('reason'):
                lines.insert(0, str(ev['reason']))
            return NL.join(lines)[:3000]
        if isinstance(ev, list):
            return NL.join(str(x) for x in ev)[:1500]
        return str(ev)[:400]

    def _candidates(self, root, checks):
        """Files worth showing the model, with their check detail, redacted.

        Files the checks name come first (a compiler error cites its file), then
        the rest up to the cap, so a build error is shown the source it is about.
        """
        open_checks = [c for c in checks if c['status'] in ('FAIL', 'GAP')]
        detail = {}
        for c in open_checks:
            detail[c['name']] = self._evidence_text(c)
            if c.get('remediation'):
                detail[c['name']] += NL + 'Remediation: ' + str(c['remediation'])
        cited = ' '.join(detail.values())
        everything = list(_files(root))
        pom = Path(root).rglob('pom.xml')
        everything += [p for p in pom if p.stat().st_size < MAX_FILE_BYTES]
        ordered = sorted(everything, key=lambda f: 0 if f.name in cited else 1)
        files = []
        for f in ordered[:MAX_PAYLOAD_FILES]:
            # A file a compiler error names is shown nearly whole: the line it
            # points at is rarely in the first 6,000 characters.
            limit = MAX_CITED_FILE_CHARS if f.name in cited else MAX_FILE_CHARS
            body = redact(f.read_text(errors='ignore'))[:limit]
            files.append({'path': str(f.relative_to(Path(root))).replace('\\', '/'),
                          'content': body})
        return detail, files

    def _accept_edit(self, root, edit):
        return accept_edit(root, edit)

    def _verified_symbols(self, root, files):
        """Where a shown file's foreign imports actually resolve, if anywhere real.

        Checked against this project's own other generated files and whatever
        of its declared Maven dependencies are resolvable in the local cache --
        never a hardcoded table of one framework, so this is the same for
        whatever stack a given migration targets. ``[]`` when nothing resolves
        locally at all, which is the signal that this fallback has nothing to
        add and the model is on its own, exactly as before it existed.
        """
        index = _local_classes(root)
        poms = [p.read_text(errors='ignore') for p in Path(root).rglob('pom.xml')
               if p.stat().st_size < MAX_FILE_BYTES]
        for simple, fqns in dependency_jars.project_classes(poms, _m2_root()).items():
            bucket = index.setdefault(simple, [])
            for fqn in fqns:
                if fqn not in bucket:
                    bucket.append(fqn)
        if not index:
            return []
        found, seen = [], set()
        for f in files:
            for pkg, cls in _IMPORT.findall(f.get('content', '')):
                imported = pkg + '.' + cls
                if cls in index and imported not in index[cls] and imported not in seen:
                    seen.add(imported)
                    found.append({'imported_as': imported, 'class': cls, 'actually_at': index[cls]})
        return found

    def _verified_contracts(self, root, files):
        """The real methods of whatever local type a shown file must agree with.

        Two shapes cover what a cross-file signature mismatch actually is:
        a class/interface the file ``implements``/``extends`` (a controller's
        ``@Override`` disagreeing with its own Api interface), and a field
        whose declared type is another local class (a service calling its
        connector with the wrong argument shape). Both are read back from the
        real source, never guessed -- the same "refer the real project"
        principle as ``_verified_symbols``, just for signatures instead of
        imports. ``[]`` when a shown file references no local type this way.
        """
        contracts = _local_contracts(root)
        if not contracts:
            return []
        found, seen = [], set()
        for f in files:
            text = f.get('content', '')
            targets = set()
            cls = _CLASS_DECL.search(text)
            if cls:
                _, _, extends, implements = cls.groups()
                if extends:
                    targets.add(extends.strip())
                if implements:
                    targets |= {x.strip() for x in implements.split(',') if x.strip()}
            for field_type, _field_name in _FIELD_TYPE.findall(text):
                if field_type in contracts:
                    targets.add(field_type)
            for name in targets:
                contract = contracts.get(name)
                if not contract or name in seen:
                    continue
                methods = sorted({sig for sigs in contract['methods'].values() for sig in sigs})
                if methods:
                    seen.add(name)
                    found.append({'class': name, 'kind': contract['kind'], 'methods': methods})
        return found

    def _claude_repair(self, context, root, checks, job_id, plan_id, quality,
                       execute_build='auto', history=None):
        """Ask the model for edits, apply them, and keep them only if the gate improves.

        The gate's verdict decides, not the model's confidence. Every file the
        edits touch is snapshotted first and restored wholesale unless the gate
        scores strictly better and worse in nothing (``improved``) -- so a repair
        that breaks something else, or achieves nothing, leaves no trace.

        The gate is re-run *with the build*. It used to be re-run without it, so a
        build failure showed as "not executed" after any edit, the repair looked like
        an improvement, and an edit that had never been compiled was kept.

        ``verified_symbols``/``verified_contracts`` ride in the payload the same
        way ``previous_attempts``/``_correction`` do elsewhere in this codebase --
        new data the approved template never has to name, added only when there is
        something real to say. The first is where a cited file's import actually
        resolves; the second is the real method signatures of whatever local type
        a shown file implements/extends or holds as a field. Both are checked
        against this project's own other files (plus, for imports, whatever of
        its declared Maven dependencies are resolvable locally) -- never a
        hardcoded table of one framework, since this stage is reached by any
        migration, not only this one.
        """
        from app.agents.reasoning import ReasoningGateway
        # FAIL and GAP both count: a repair that closes a GAP is an improvement
        # the gate can see, and one that opens a new GAP is not.
        failed_before = {c['name'] for c in checks if c['status'] in ('FAIL', 'GAP')}
        detail, files = self._candidates(root, checks)
        verified = self._verified_symbols(root, files)
        contracts = self._verified_contracts(root, files)
        answer, provenance = ReasoningGateway(self.repo).reason(
            context, 'agent/feedback/code-repair',
            {'failing_checks': sorted(failed_before), 'check_detail': detail, 'files': files,
             **({'previous_attempts': list(history)[-3:]} if history else {}),
             **({'verified_symbols': verified} if verified else {}),
             **({'verified_contracts': contracts} if contracts else {})})
        if not isinstance(answer, dict) or not answer.get('edits'):
            return {'attempted': bool(answer), 'applied': [], 'refused': [],
                    'outcome': 'no_edits_proposed',
                    'reason': provenance.get('reason', ''), 'mode': provenance.get('mode', '')}

        accepted, refused, snapshot = [], [], {}
        for edit in list(answer['edits'])[:MAX_EDITS]:
            target, why = self._accept_edit(root, edit)
            if target is None:
                refused.append(why)
                continue
            text = target.read_text(errors='ignore')
            snapshot.setdefault(target, text)
            target.write_text(text.replace(str(edit['old_str']), str(edit.get('new_str', '')), 1))
            accepted.append({'path': str(target), 'check': 'MODEL_REPAIR',
                             'action': str(edit.get('why', 'model-proposed edit'))[:200]})
        if not accepted:
            return {'attempted': True, 'applied': [], 'refused': refused,
                    'outcome': 'all_edits_refused', 'mode': provenance.get('mode', '')}

        after = quality.quality_validate(job_id, plan_id, root, execute_build)
        failed_after = {c['name'] for c in after['checks'] if c['status'] in ('FAIL', 'GAP')}
        better = improved(score(checks), score(after['checks']))
        tried = [a['action'] for a in accepted][:6]
        if not better:
            for path, text in snapshot.items():
                path.write_text(text)
            return {'attempted': True, 'applied': [], 'refused': refused,
                    'outcome': 'reverted_no_improvement',
                    'failed_before': sorted(failed_before), 'failed_after': sorted(failed_after),
                    'mode': provenance.get('mode', ''),
                    # Told to the next attempt so it does not propose the same thing again.
                    '_note': {'tried': tried, 'result': 'reverted: it did not get the build further',
                              'errors_then': self._top_errors(after['checks'])}}
        return {'attempted': True, 'applied': accepted, 'refused': refused,
                'outcome': 'kept', 'failed_before': sorted(failed_before),
                'failed_after': sorted(failed_after), 'mode': provenance.get('mode', ''),
                '_after': after, '_note': {'tried': tried, 'result': 'kept: the build got further'}}

    @staticmethod
    def _top_errors(checks, limit=8):
        for c in checks:
            if c['name'] in BUILD_CHECKS and c['status'] == 'FAIL' and isinstance(c.get('evidence'), dict):
                return list(c['evidence'].get('errors') or [])[:limit]
        return []

    # -- unresolved findings -------------------------------------------------
    def outstanding(self, job_id):
        """Findings this agent will not touch, reported rather than ignored."""
        variances = self.repo.rows(
            "select path,severity,kind from variances where job_id=? and disposition='OPEN'", (job_id,))
        causes = self.repo.rows(
            "select hypothesis,confidence from root_causes where job_id=? and status='PROPOSED'", (job_id,))
        return ([{'kind': 'VARIANCE', 'detail': '%s (%s/%s)' % (v['path'], v['kind'], v['severity']),
                  'owner': 'remediation'} for v in variances]
                + [{'kind': 'ROOT_CAUSE', 'detail': c['hypothesis'], 'owner': 'remediation'}
                   for c in causes])

    # -- the loop ------------------------------------------------------------
    def apply(self, job_id, plan_id, project_root, max_passes=3, execute_build='auto',
              context=None):
        """Fix, re-validate, repeat until the gate is clean or the budget is spent.

        Two repair stages per pass, deterministic first:

        1. The mechanical repairs, which the gate's own rules define exactly.
        2. Only when those produce nothing, and only when ``context`` carries a
           provider, the model is asked for targeted edits. Its output is
           applied, re-validated and kept **only** if the failing-check set
           strictly shrinks; otherwise every touched file is restored.

        Running the model second is deliberate: the deterministic pass clears
        the checks whose fix is already known, so the model is only ever asked
        about what is genuinely left, on a smaller payload.

        The loop runs one validation more than it runs repairs, so the status
        returned is always the gate's verdict on the *final* file state rather
        than on the state before the last edit. It stops early when a pass
        produces nothing at all, because spinning on a failure neither stage can
        repair would burn the budget and still report the same thing.
        """
        from app.services.migration_lifecycle_v6 import MappingAndPlanningService
        quality = MappingAndPlanningService(self.repo)
        budget = max(1, int(max_passes))
        passes, applied, model_rounds, run, history = [], [], [], None, []
        carried = None
        for n in range(budget + 1):
            # The model stage has just validated the tree it left behind (with the
            # build, which takes minutes), so that run is reused rather than repeated.
            run = carried or quality.quality_validate(job_id, plan_id, project_root, execute_build)
            carried = None
            failed = [c['name'] for c in run['checks'] if c['status'] in ('FAIL', 'GAP')]
            if not failed or n == budget:
                passes.append({'pass': n + 1, 'failed': failed, 'fixes': 0, 'model': None,
                               'quality_run_id': run['quality_run_id'], 'status': run['status']})
                break
            fixes = self._repair(failed, project_root, job_id, plan_id)
            applied += fixes
            model = None
            if not fixes and context is not None and getattr(context, 'provider', None) is not None:
                model = self._claude_repair(context, project_root, run['checks'],
                                            job_id, plan_id, quality, execute_build, history)
                applied += model['applied']
                note = model.pop('_note', None)
                if note:
                    history.append(note)
                # Kept: the tree is what that validation saw. Reverted: it is what
                # ``run`` saw. Either way no second build is needed.
                carried = model.pop('_after', None) or run
                model_rounds.append(model)
            passes.append({'pass': n + 1, 'failed': failed, 'fixes': len(fixes),
                           'model': (model or {}).get('outcome'),
                           'quality_run_id': run['quality_run_id'], 'status': run['status']})
            if not fixes and not (model or {}).get('applied'):
                # A reverted attempt is information, not the end: the next one is told
                # what was tried and what the build said. Nothing proposed, or nothing
                # that could be placed in a file, would be asked again unchanged.
                if (model or {}).get('outcome') != 'reverted_no_improvement':
                    break
        final_failed = [c['name'] for c in run['checks'] if c['status'] == 'FAIL']
        final_gaps = [c['name'] for c in run['checks'] if c['status'] == 'GAP']
        return {
            'quality_run_id': run['quality_run_id'],
            'quality_status': run['status'],
            'checks': run['checks'],
            'applied_fixes': applied,
            'passes': passes,
            'model_rounds': model_rounds,
            'unfixed_checks': final_failed,
            'unfixed_gaps': final_gaps,
            'unresolved': self.outstanding(job_id),
            'project_root': str(project_root),
        }
