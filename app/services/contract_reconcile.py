"""Make every call and every override in the generated code agree with the real type it uses.

Generated files are written one at a time, so a controller and the interface it implements, or a
service and the connector it calls, can disagree about a method's name, arity or argument types. The
build then stops with "method does not override" or "incompatible types", which is a contract error,
not a bad design, and the cure is always the same: the consumer conforms to the real signature.

Nothing is guessed. The real signatures come from two places:

* the project's own sources (interfaces, services, connectors), read back from the files it wrote, and
* the framework jars it depends on, read with ``javap`` from the jars the project's poms resolve to.

A file is revised only when it makes a concrete claim that the contract refutes: an ``@Override`` of a
method the supertype does not declare, a call to a method the field's type does not have, or a call
whose arguments match no overload of that method. The revision is kept only if it leaves that file with
fewer such problems, so a model that cannot help leaves the file as it was.
"""
import re
import shutil
import subprocess
from pathlib import Path

from app.services import dependency_jars
from app.services.feedback import _CLASS_DECL, _balanced_parens, _methods_in, _split_top_level_commas

_FIELD = re.compile(r'(?m)^\s*(?:private|protected|public)?\s*(?:final\s+)?([A-Z][\w.]*(?:<[^;=(){}]*>)?(?:\[\])?)\s+(\w+)\s*[;=]')
_LOCAL = re.compile(r'([A-Za-z_][\w.]*(?:<[^;=(){}]*>)?(?:\[\])?)\s+(\w+)\s*(?=[,)=;:])')
_CALL = re.compile(r'\b(\w+)\.(\w+)\s*\(')
_OVERRIDE = re.compile(r'@Override\s+(?:@\w+(?:\([^)]*\))?\s+)*(?:public\s+|protected\s+)?(?:static\s+)?[\w<>\[\],.? ]+?\s+(\w+)\s*\(')
_JAVAP_METHOD = re.compile(r'^\s*public\s+(?:static\s+|final\s+|abstract\s+|default\s+)*(?:<[^>]*>\s*)?([\w.$<>\[\], ?]+?)\s+(\w+)\((.*)\);\s*$')
_LITERAL_STRING = re.compile(r'^"')
MAX_REVISIONS = 6


def _simple(type_name):
    """``java.util.Optional<String>`` -> ``Optional``; arrays and primitives kept as written."""
    base = re.sub(r'<.*>', '', str(type_name)).strip()
    array = base.endswith('[]')
    base = base[:-2] if array else base
    base = base.rsplit('.', 1)[-1] + ('[]' if array else '')
    return base


def _param_simple_types(params):
    out = []
    for raw in _split_top_level_commas(params):
        tokens = re.sub(r'@\w+(\([^()]*\))?\s*', '', raw).split()
        if len(tokens) >= 2:
            out.append(_simple(tokens[-2]))
        elif tokens:
            out.append(_simple(tokens[0]))
    return out


# -- what the real types look like ------------------------------------------------------------------------
def _from_text(text):
    """``(kind, [supertypes], {method: [(return, [param simple types])]})`` for one source file."""
    cls = _CLASS_DECL.search(text)
    if not cls:
        return None
    kind, _name, extends, implements = cls.groups()
    supers = []
    if extends:
        supers.append(_simple(extends))
    if implements:
        supers += [_simple(x) for x in implements.split(',') if x.strip()]
    methods = {}
    for return_type, name, types in _methods_in(text):
        methods.setdefault(name, []).append((_simple(return_type), [_simple(t) for t in types]))
    return kind, supers, methods


def _framework_contracts(poms, m2_root, wanted):
    """``{simple: {'kind','supers','methods'}}`` for the jar classes named by fully qualified name in ``wanted``."""
    javap = shutil.which('javap')
    if not javap or not wanted:
        return {}
    out = {}
    for jar in dependency_jars.project_jars(poms, m2_root):
        present = {fqn for fqns in dependency_jars.classes_in_jar(jar).values() for fqn in fqns}
        for fqn in sorted(wanted & present):
            simple = fqn.rsplit('.', 1)[-1]
            if simple in out:
                continue
            listing = _javap(javap, jar, fqn)
            if listing:
                out[simple] = listing
    return out


def _javap(javap, jar, fqn):
    try:
        done = subprocess.run([javap, '-cp', str(jar), fqn], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    header = done.stdout.split('{')[0]
    kind = 'interface' if re.search(r'\binterface\s', header) else 'class'
    supers = []
    for clause in re.findall(r'\b(?:extends|implements)\s+([^{]+?)(?=\s+(?:extends|implements)\b|\s*$)', header, re.M):
        supers += [_simple(x) for x in clause.split(',') if x.strip()]
    methods = {}
    for line in done.stdout.splitlines():
        m = _JAVAP_METHOD.match(line)
        if not m:
            continue
        ret, name, params = m.group(1), m.group(2), m.group(3)
        methods.setdefault(name, []).append((_simple(ret), _param_simple_types(params)))
    return {'kind': kind, 'supers': supers, 'methods': methods}


def contracts(files, m2_root):
    """The real contract of every type a generated file can refer to: the project's own and the framework's."""
    local = {}
    for path, text in files:
        if not str(path).endswith('.java'):
            continue
        parsed = _from_text(text)
        if parsed:
            kind, supers, methods = parsed
            name = Path(path).stem
            local[name] = {'kind': kind, 'supers': supers, 'methods': methods}
    poms = [text for path, text in files if str(path).replace(chr(92), '/').rsplit('/', 1)[-1] == 'pom.xml']
    wanted = set()
    for path, text in files:
        if str(path).endswith('.java'):
            for fqn in re.findall(r'^import\s+([\w.]+)\s*;', text, re.M):
                if fqn.rsplit('.', 1)[-1] not in local:
                    wanted.add(fqn)
    framework = _framework_contracts(poms, m2_root, wanted) if poms else {}
    return {**framework, **local}


# -- what a file claims --------------------------------------------------------------------------------------
def _field_types(text):
    return {name: _simple(kind) for kind, name in _FIELD.findall(text)}


def _local_types(text):
    return {name: _simple(kind) for kind, name in _LOCAL.findall(text)}


def problems(text, contract_map):
    """The concrete claims in one file that the real contracts refute, as short sentences."""
    out = []
    parsed = _from_text(text)
    supers = parsed[1] if parsed else []
    for name in _OVERRIDE.findall(text):
        for sup in supers:
            c = contract_map.get(sup)
            if c and name not in _all_methods(sup, contract_map):
                out.append('@Override %s: %s declares no such method' % (name, sup))
    fields = _field_types(text)
    locals_ = _local_types(text)
    for obj, meth in set(_CALL.findall(text)):
        typ = fields.get(obj) or locals_.get(obj)
        c = contract_map.get(typ) if typ else None
        if not c:
            continue
        overloads = _all_overloads(typ, meth, contract_map)
        if not overloads:
            out.append('%s.%s(): %s has no such method' % (obj, meth, typ))
            continue
        args = _call_args(text, obj, meth)
        if args is None:
            continue
        fit = [params for _ret, params in overloads if _fits(args, params, text)]
        if not fit:
            out.append('%s.%s(%s): no %s overload takes these arguments' % (
                obj, meth, ', '.join(a or '?' for a in args), typ))
    return out


def _all_methods(type_name, contract_map, seen=None):
    seen = seen or set()
    if type_name in seen or type_name not in contract_map:
        return set()
    seen.add(type_name)
    c = contract_map[type_name]
    names = set(c['methods'])
    for sup in c['supers']:
        names |= _all_methods(sup, contract_map, seen)
    return names


def _all_overloads(type_name, meth, contract_map, seen=None):
    seen = seen or set()
    if type_name in seen or type_name not in contract_map:
        return []
    seen.add(type_name)
    c = contract_map[type_name]
    found = list(c['methods'].get(meth, []))
    for sup in c['supers']:
        found += _all_overloads(sup, meth, contract_map, seen)
    return found


def _call_args(text, obj, meth):
    """The argument expressions of the first ``obj.meth(...)`` call, or ``None`` if unreadable."""
    m = re.search(r'\b%s\.%s\s*\(' % (re.escape(obj), re.escape(meth)), text)
    if not m:
        return None
    span = _balanced_parens(text, m.end() - 1)
    if not span:
        return None
    body = span[1:-1].strip()
    return [a.strip() for a in _split_top_level_commas(body)] if body else []


def _fits(args, params, text):
    if len(args) != len(params):
        return False
    types = _local_types(text)
    for arg, want in zip(args, params):
        got = _arg_type(arg, types)
        if got is None or want == 'Object' or got == want:
            continue
        return False
    return True


def _arg_type(arg, types):
    """The simple type of an argument this check can read: a literal or a declared local, else ``None``."""
    if _LITERAL_STRING.match(arg):
        return 'String'
    if re.fullmatch(r'\w+', arg):
        declared = types.get(arg)
        return _simple(declared) if declared else None
    return None


# -- the revision --------------------------------------------------------------------------------------------
def reconcile(context, files, m2_root, produce_many, file_prompt, effort, skill, base_payload):
    """``(files, actions)``: revise each file the real contracts refute, keeping a revision only if it is better.

    ``produce_many`` and ``file_prompt`` are the code generator's own authoring call, so a revision is
    made the same way as every other file. ``base_payload`` carries the service and the plan.
    """
    actions = []
    for _attempt in range(MAX_REVISIONS):
        contract_map = contracts(files, m2_root)
        if not contract_map:
            break
        by_path = dict(files)
        broken = {p: problems(t, contract_map) for p, t in files if str(p).endswith('.java')}
        broken = {p: v for p, v in broken.items() if v}
        if not broken:
            break
        requests = []
        for path, found in sorted(broken.items()):
            needed = _needed_contracts(by_path[path], contract_map)
            requests.append((path, dict(base_payload, path=path, current_content=by_path[path],
                                        purpose='Revision: make this file agree with the real types it uses',
                                        required_change=_instruction(found),
                                        contracts=needed,
                                        skill_reference='references/structural-conventions.md')))
        try:
            authored, _prov = produce_many(context, file_prompt, requests, skill=skill, effort=effort)
        except Exception as exc:                          # noqa: BLE001 -- the file as written stands
            actions.append('could not revise %d file(s) to match their types (%s)' % (len(requests), exc))
            break
        new_files, changed = [], []
        for path, text in files:
            answer = authored.get(path)
            candidate = answer.get('content') if answer else None
            if candidate and candidate != text:
                before = len(broken.get(path, []))
                after = len(problems(candidate, contract_map))
                if after < before:
                    text = candidate
                    changed.append(path)
            new_files.append((path, text))
        if not changed:
            for path in sorted(broken):
                actions.append('%s still disagrees with the real types it uses: %s'
                               % (path, '; '.join(broken[path][:3])))
            break
        files = new_files
        actions += ['revised %s to match the real types it uses' % p for p in changed]
    return files, actions


def _needed_contracts(text, contract_map):
    named = set(_field_types(text).values()) | set(_local_types(text).values())
    parsed = _from_text(text)
    if parsed:
        named |= set(parsed[1])
    out = {}
    for name in sorted(named):
        c = contract_map.get(name)
        if c:
            out[name] = {'kind': c['kind'], 'supers': c['supers'],
                         'methods': sorted('%s(%s)' % (m, ', '.join(p)) + ' -> ' + r
                                           for m, sigs in c['methods'].items() for r, p in sigs)}
    return out


def _instruction(found):
    return ('Change only what disagrees with the real types this file uses, listed in contracts: '
            + '; '.join(found) + '. Keep the behaviour, the other members and the class itself unchanged.')
