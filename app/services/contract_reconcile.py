"""Make every call, override and constant in the generated code agree with the real type it uses.

Generated files are written one at a time, so a controller and the interface it implements, a service and
the connector it calls, or a message and the builder that returns it can disagree about a name, an arity, a
type or a constant. The build then stops with "cannot find symbol" or "method does not override", which is a
contract error, not a design choice. The cure is the same every time: the consumer conforms to the real type.

Nothing is guessed. The real types come from two places:

* the project's own sources, read back from the files it wrote: classes, interfaces, enums, and records (whose
  components are accessor methods), nested or not;
* the framework jars the project depends on, read with ``javap``: their public methods, and their public
  static fields (enum constants among them).

A file is revised only when it makes a claim the real types refute: an ``@Override`` of a method the supertype
does not declare, a call to a method the type lacks, arguments that match no overload, a member of a framework
type that does not exist, or a ``var`` local whose type has no such method. The revision is kept only if it
leaves that file with fewer such problems, so a model that cannot help leaves the file as it was.
"""
import re
import shutil
import subprocess
from pathlib import Path

from app.services import dependency_jars
from app.services.feedback import _METHOD_START, _balanced_parens, _param_types, _split_top_level_commas

#: Declarations: classes, interfaces, enums and records, nested or not.
_DECL = re.compile(r'\b(class|interface|enum|record)\s+(\w+)\s*(\([^)]*\))?([^{;]*)\{')
_EXTENDS = re.compile(r'\bextends\s+([\w.<>, ]+?)(?=\s+implements\b|\s*$)')
_IMPLEMENTS = re.compile(r'\bimplements\s+([\w.<>, ]+?)\s*$')
_FIELD = re.compile(r'(?m)^\s*(?:private|protected|public)?\s*(?:final\s+)?([A-Z][\w.]*(?:<[^;=(){}]*>)?(?:\[\])?)\s+(\w+)\s*[;=]')
_LOCAL = re.compile(r'([A-Za-z_][\w.]*(?:<[^;=(){}]*>)?(?:\[\])?)\s+(\w+)\s*(?=[,)=;:])')
_VAR = re.compile(r'\bvar\s+(\w+)\s*=\s*([^;]+);')
_CALL = re.compile(r'(?<![\w.])(\w+)\.(\w+)\s*\(')
_CONSTANT = re.compile(r'(?<![\w.])([A-Z]\w*)\.([A-Z][A-Z0-9_]*)\b(?!\s*[(\w])')
_OVERRIDE = re.compile(r'@Override\s+(?:@\w+(?:\([^)]*\))?\s+)*(?:public\s+|protected\s+)?(?:static\s+)?[\w<>\[\],.? ]+?\s+(\w+)\s*\(')
_JAVAP_METHOD = re.compile(r'^\s*public\s+(?:static\s+|final\s+|abstract\s+|default\s+)*(?:<[^>]*>\s*)?([\w.$<>\[\], ?]+?)\s+(\w+)\((.*)\);\s*$')
_JAVAP_FIELD = re.compile(r'^\s*public\s+static\s+(?:final\s+)?[\w.$<>\[\], ?]+?\s+([A-Z][A-Z0-9_]*)\s*;')
_JAVAP_HEADER = re.compile(r'\b(class|interface|enum)\s+\w+(.*)\{')
_NEW = re.compile(r'^new\s+([A-Z]\w*)(?:<[^()]*>)?\s*\(')
_LITERAL_STRING = re.compile(r'^"')
MAX_REVISIONS = 6


def _simple(type_name):
    """``java.util.Optional<String>`` -> ``Optional``; arrays and varargs kept as ``[]``."""
    base = re.sub(r'<.*>', '', str(type_name)).strip()
    array = base.endswith('[]') or base.endswith('...')
    base = base.rstrip('.').removesuffix('[]').rsplit('.', 1)[-1].replace('$', '.').rsplit('.', 1)[-1]
    return base + ('[]' if array else '')


def _param_simple_types(params):
    out = []
    for raw in _split_top_level_commas(params):
        tokens = re.sub(r'@\w+(\([^()]*\))?\s*', '', raw).split()
        if len(tokens) >= 2:
            out.append(_simple(tokens[-2]))
        elif tokens:
            out.append(_simple(tokens[0]))
    return out


def _block_end(text, open_idx):
    """Index just past the ``}`` that closes the ``{`` at ``open_idx``, ignoring braces in strings and comments."""
    depth, i, n = 0, open_idx, len(text)
    while i < n:
        ch = text[i]
        if text.startswith('//', i):
            i = text.find('\n', i)
            i = n if i < 0 else i
            continue
        if text.startswith('/*', i):
            i = text.find('*/', i)
            i = n if i < 0 else i + 2
            continue
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == '\\' else 1
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _code(text):
    """``text`` with comments blanked out (line breaks and positions kept), so prose is never read as code."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith('//', i):
            end = text.find('\n', i)
            end = n if end < 0 else end
            out.append(' ' * (end - i))
            i = end
        elif text.startswith('/*', i):
            end = text.find('*/', i)
            end = n if end < 0 else end + 2
            out.append(re.sub(r'[^\n]', ' ', text[i:end]))
            i = end
        elif text[i] == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == '\\' else 1
            out.append(text[i:end + 1])
            i = end + 1
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def _declarations(text):
    """``[(kind, name, span, header, components)]`` for every type declared in ``text``, nested or not."""
    out = []
    for m in _DECL.finditer(text):
        kind, name, components, header = m.group(1), m.group(2), m.group(3), m.group(4)
        open_idx = m.end() - 1
        out.append((kind, name, (open_idx, _block_end(text, open_idx)), header, components))
    return out


def _types_of(text):
    """``{name: {'kind','supers','methods'}}`` for every type in one source file.

    A method belongs to the innermost type whose body contains it. A record's components are its accessors.
    """
    decls = _declarations(text)
    found = {}
    for kind, name, span, header, components in decls:
        supers = []
        ext = _EXTENDS.search(header or '')
        imp = _IMPLEMENTS.search(header or '')
        for clause in (ext, imp):
            if clause:
                supers += [_simple(x) for x in clause.group(1).split(',') if x.strip()]
        found[name] = {'kind': kind, 'supers': supers, 'methods': {}, 'fields': set(), 'framework': False}
        if kind == 'record' and components:
            for raw in _split_top_level_commas(components.strip('()')):
                tokens = re.sub(r'@\w+(\([^()]*\))?\s*', '', raw).split()
                if len(tokens) >= 2:
                    found[name]['methods'].setdefault(tokens[-1], []).append((_simple(tokens[-2]), []))
    for return_type, mname, types, pos in _methods_with_positions(text):
        owner = _innermost(decls, pos)
        if owner:
            found[owner]['methods'].setdefault(mname, []).append((_simple(return_type), [_simple(t) for t in types]))
    return found


def _methods_with_positions(text):
    """``[(return_type, name, [param_type...], position)]`` for every method declaration."""
    out = []
    for m in _METHOD_START.finditer(text):
        open_pos = m.end() - 1
        span = _balanced_parens(text, open_pos)
        if not span:
            continue
        after = text[open_pos + len(span):].lstrip()
        if not (after.startswith('{') or after.startswith(';')):
            continue
        out.append((m.group(1), m.group(2), _param_types(span[1:-1]), m.start()))
    return out


def _innermost(decls, pos):
    best, best_size = None, None
    for kind, name, (start, end), _h, _c in decls:
        if start < pos < end and (best_size is None or end - start < best_size):
            best, best_size = name, end - start
    return best


def _framework_contracts(poms, m2_root, wanted):
    """``{simple: {...}}`` for jar classes named by fully qualified name in ``wanted``, read with javap."""
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
    lines = done.stdout.splitlines()
    header = next((ln for ln in lines if _JAVAP_HEADER.search(ln)), '')
    kind = 'interface' if re.search(r'\binterface\s', header) else ('enum' if re.search(r'\benum\s', header) else 'class')
    supers = []
    ext = _EXTENDS.search(header.split('{')[0])
    imp = _IMPLEMENTS.search(header.split('{')[0])
    for clause in (ext, imp):
        if clause:
            supers += [_simple(x) for x in clause.group(1).split(',') if x.strip()]
    methods, fields = {}, set()
    for line in lines:
        f = _JAVAP_FIELD.match(line)
        if f:
            fields.add(f.group(1))
            continue
        m = _JAVAP_METHOD.match(line)
        if m:
            methods.setdefault(m.group(2), []).append((_simple(m.group(1)), _param_simple_types(m.group(3))))
    return {'kind': kind, 'supers': supers, 'methods': methods, 'fields': fields, 'framework': True}


def contracts(files, m2_root):
    """The real type of everything a generated file can refer to: the project's own and the framework's."""
    local = {}
    for path, text in files:
        if str(path).endswith('.java'):
            for name, info in _types_of(_code(text)).items():
                info.setdefault('fields', set())
                local.setdefault(name, info)
    poms = [text for path, text in files if str(path).replace(chr(92), '/').rsplit('/', 1)[-1] == 'pom.xml']
    wanted = set()
    for path, text in files:
        if str(path).endswith('.java'):
            for fqn in re.findall(r'^import\s+([\w.]+)\s*;', text, re.M):
                if fqn.rsplit('.', 1)[-1] not in local:
                    wanted.add(fqn)
    framework = _framework_contracts(poms, m2_root, wanted) if poms else {}
    return {**framework, **local}


# -- what a file declares and uses ------------------------------------------------------------------------
def _declared_types(text, contract_map):
    """``{variable: type}`` from fields, parameters and locals: the explicit declarations."""
    out = {}
    for kind, name in _FIELD.findall(text):
        out[name] = _simple(kind)
    for kind, name in _LOCAL.findall(text):
        if kind != 'var':
            out.setdefault(name, _simple(kind))
    return out


def _with_vars(text, contract_map, types):
    """Add the ``var`` locals whose type can be read off the right-hand side: ``new T(...)`` or a known method."""
    types = dict(types)
    for name, rhs in _VAR.findall(text):
        rhs = rhs.strip()
        new = _NEW.match(rhs)
        if new:
            types[name] = new.group(1)
            continue
        call = re.match(r'^(\w+)\.(\w+)\s*\(', rhs)
        if call and call.group(1) in types:
            owner = types[call.group(1)]
            overloads = _all_overloads(owner, call.group(2), contract_map)
            returns = {_simple(r) for r, _p in overloads}
            if len(returns) == 1:
                types[name] = returns.pop()
    return types


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


def _all_fields(type_name, contract_map, seen=None):
    seen = seen or set()
    if type_name in seen or type_name not in contract_map:
        return set()
    seen.add(type_name)
    c = contract_map[type_name]
    names = set(c.get('fields', ()))
    for sup in c['supers']:
        names |= _all_fields(sup, contract_map, seen)
    return names


# -- what the file claims -----------------------------------------------------------------------------------
def problems(text, contract_map):
    """The concrete claims in one file that the real types refute, as short sentences."""
    text = _code(text)
    out = []
    own = _types_of(text)
    for name, info in own.items():
        for sup in info['supers']:
            if sup not in contract_map:
                continue
            for method in set(_override_names(text)):
                if method not in _all_methods(sup, contract_map):
                    out.append('@Override %s: %s declares no such method' % (method, sup))
    types = _with_vars(text, contract_map, _declared_types(text, contract_map))
    for obj, meth in sorted(set(_CALL.findall(text))):
        typ = types.get(obj)
        if typ is None and obj[:1].isupper() and obj in contract_map:
            typ = obj                                   # a static call on a type: ErrorUtil.errorResponse(...)
        if typ is None or typ not in contract_map:
            continue
        overloads = _all_overloads(typ, meth, contract_map)
        if not overloads:
            if meth not in _all_methods(typ, contract_map):
                out.append('%s.%s(): %s has no such method' % (obj, meth, typ))
            continue
        args = _call_args(text, obj, meth)
        if args is None:
            continue
        if not any(_fits(args, params, types) for _ret, params in overloads):
            out.append('%s.%s(%s): no %s overload takes these arguments' % (
                obj, meth, ', '.join(a or '?' for a in args), typ))
    for obj, const in sorted(set(_CONSTANT.findall(text))):
        if obj in contract_map and contract_map[obj].get('framework'):
            if const not in _all_fields(obj, contract_map):
                out.append('%s.%s: %s has no constant %s' % (obj, const, obj, const))
    return out


def _override_names(text):
    return _OVERRIDE.findall(text)


def _call_args(text, obj, meth):
    """The argument expressions of the first ``obj.meth(...)`` call, or ``None`` if unreadable."""
    m = re.search(r'(?<![\w.])%s\.%s\s*\(' % (re.escape(obj), re.escape(meth)), text)
    if not m:
        return None
    span = _balanced_parens(text, m.end() - 1)
    if not span:
        return None
    body = span[1:-1].strip()
    return [a.strip() for a in _split_top_level_commas(body)] if body else []


def _fits(args, params, types):
    varargs = bool(params) and params[-1].endswith('[]') and len(args) >= len(params) - 1
    if len(args) != len(params) and not varargs:
        return False
    for i, arg in enumerate(args):
        if i >= len(params):
            break
        want = params[i]
        got = _arg_type(arg, types)
        if got is None or want == 'Object' or got == want or (varargs and i >= len(params) - 1):
            continue
        return False
    return True


def _arg_type(arg, types):
    """The simple type of an argument this check can read: a string literal or a typed variable, else ``None``."""
    if _LITERAL_STRING.match(arg):
        return 'String'
    if re.fullmatch(r'\w+', arg):
        declared = types.get(arg)
        return _simple(declared) if declared else None
    return None


# -- the revision -------------------------------------------------------------------------------------------
def reconcile(context, files, m2_root, produce_many, file_prompt, effort, skill, base_payload):
    """``(files, actions)``: revise each file the real types refute, keeping a revision only if it is better."""
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
            requests.append((path, dict(base_payload, path=path, current_content=by_path[path],
                                        purpose='Revision: make this file agree with the real types it uses',
                                        required_change=_instruction(found),
                                        contracts=_needed_contracts(by_path[path], contract_map),
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
            if candidate and candidate != text and len(problems(candidate, contract_map)) < len(broken.get(path, [])):
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
    types = _with_vars(text, contract_map, _declared_types(text, contract_map))
    named = set(types.values()) | {n for n in re.findall(r'\b([A-Z]\w*)\.\w+', text) if n in contract_map}
    for info in _types_of(_code(text)).values():
        named |= set(info['supers'])
    out = {}
    for name in sorted(named):
        c = contract_map.get(name)
        if not c:
            continue
        entry = {'kind': c['kind'], 'supers': c['supers'],
                 'methods': sorted('%s(%s) -> %s' % (m, ', '.join(p), r)
                                   for m, sigs in c['methods'].items() for r, p in sigs)}
        if c.get('fields'):
            entry['constants'] = sorted(c['fields'])
        out[name] = entry
    return out


def _instruction(found):
    return ('Change only what disagrees with the real types this file uses, listed in contracts: '
            + '; '.join(found) + '. Where a framework constant is named, use one that the contract lists. '
            'Keep the behaviour, the other members and the class itself unchanged.')
