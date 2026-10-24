"""What a generated project's own declared Maven dependencies actually contain.

Nothing here names a vendor or a framework: this migration factory targets
many different source and destination stacks, so grounding a generated file's
imports has to come from whatever *that* project's own `pom.xml` declares and
whatever is actually resolvable in the local Maven cache -- never a hardcoded
table of one framework's classes. ``feedback.py``'s model-assisted repair is
the only caller: it is a fallback the repair stage consults once a real build
has already failed, not grounding handed to the first, free authoring attempt.

When a dependency cannot be resolved locally (not yet downloaded, a non-Maven
project, a version this environment has never built), every function here
degrades to an empty result rather than raising -- the caller simply has
nothing more to offer and falls back to today's behaviour.
"""
import re
import zipfile
from pathlib import Path

_DEPENDENCY = re.compile(r'<dependency>(.*?)</dependency>', re.S)
_PROPERTIES_BLOCK = re.compile(r'<properties>(.*?)</properties>', re.S)
_PROPERTY = re.compile(r'<([\w.-]+)>([^<]*)</\1>')
_PLACEHOLDER = re.compile(r'^\$\{([\w.-]+)\}$')


def _field(tag, block):
    m = re.search(r'<%s>([^<]*)</%s>' % (tag, tag), block)
    return m.group(1).strip() if m else None


def _properties(pom_text):
    block = _PROPERTIES_BLOCK.search(pom_text)
    if not block:
        return {}
    return {name: value.strip() for name, value in _PROPERTY.findall(block.group(1))}


def _resolved(raw, props):
    """One level of ``${property}`` substitution against this same pom's own properties."""
    placeholder = _PLACEHOLDER.match((raw or '').strip())
    if not placeholder:
        return raw
    return props.get(placeholder.group(1), raw)


def dependencies(pom_text, props=None):
    """``[{'group_id', 'artifact_id', 'version'}, ...]`` -- whatever this one pom declares.

    Regex, not an XML parser with namespace handling: a generated pom's own
    default namespace declaration is irrelevant to reading element text, and
    this stays consistent with the same light-touch style already used to read
    a pom in ``app/services/enterprise_v5.py``'s ``FrameworkConventionExtractor``.

    ``props`` lets a caller substitute against properties pulled from a
    *sibling* pom -- a child module's own ``<version>${framework.version}</version>``
    is routinely defined only in its parent's ``<properties>`` block, per normal
    Maven inheritance, so resolving strictly against this one file's own
    properties would silently leave the placeholder unresolved. Falls back to
    this pom's own properties when not given, so the function still stands on
    its own for a single, self-contained pom.

    ``version`` is ``None`` when the dependency block omits it -- normal for a
    *transitive* dependency whose version is actually fixed by a parent's
    ``<dependencyManagement>``/BOM rather than stated at the point of use.
    ``group_id``/``artifact_id`` are still required: a dependency with neither
    is not a usable coordinate at all.
    """
    own = _properties(pom_text)
    merged = {**own, **(props or {})}
    found = []
    for block in _DEPENDENCY.findall(pom_text):
        group_id = _field('groupId', block)
        artifact_id = _field('artifactId', block)
        if not (group_id and artifact_id):
            continue
        version = _field('version', block)
        found.append({'group_id': group_id, 'artifact_id': artifact_id,
                      'version': _resolved(version, merged) if version else None})
    return found


def jar_path(m2_root, group_id, artifact_id, version):
    """The standard Maven local-repository layout -- Maven's own convention, not a framework's."""
    return Path(m2_root).joinpath(*group_id.split('.'), artifact_id, version,
                                  '%s-%s.jar' % (artifact_id, version))


def classes_in_jar(path):
    """``{simple_name: [fqn, ...]}`` from a jar's own ``.class`` entries.

    ``{}`` for a missing or unreadable jar -- the "not resolvable locally"
    case every caller here must treat as "nothing more to offer", not an error.
    """
    index = {}
    path = Path(path)
    if not path.is_file():
        return index
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith('.class') or '$' in name:
                    continue
                fqn = name[:-len('.class')].replace('/', '.')
                simple = fqn.rsplit('.', 1)[-1]
                bucket = index.setdefault(simple, [])
                if fqn not in bucket:
                    bucket.append(fqn)
    except (OSError, zipfile.BadZipFile):
        return {}
    return index


def pom_path(m2_root, group_id, artifact_id, version):
    """Where a dependency's own cached pom sits -- same layout as ``jar_path``, same file, ``.pom``."""
    return Path(m2_root).joinpath(*group_id.split('.'), artifact_id, version,
                                  '%s-%s.pom' % (artifact_id, version))


def project_classes(pom_texts, m2_root, max_depth=3):
    """The merged class index for every dependency these poms resolve to locally, transitively.

    Driven entirely by what the generated project's own ``pom.xml`` file(s)
    name -- for whichever migration and target framework this is. ``{}`` when
    nothing resolves, which is the signal a caller uses to add no hint at all.

    Properties are pooled across every given pom before any dependency's
    version is resolved, mirroring normal Maven parent/child inheritance (a
    multi-module project's child poms routinely use a property only the
    parent pom's own ``<properties>`` block defines).

    A class a generated file needs is routinely not in anything the project
    declares *directly* -- ``EieHeader`` lives one hop further in, pulled in
    only through another framework module's own dependency on it. So each
    resolved dependency's own cached pom (Maven always caches it alongside the
    jar, including for a purely transitive dependency) is walked in turn, up
    to ``max_depth`` hops. A transitive entry with no explicit version is
    assumed to share the version of the dependency that pulled it in -- true
    of how a single framework family's own modules are versioned in practice,
    and a wrong guess here only ever means the guessed jar is not actually
    there, which resolves to nothing, exactly like any other unresolvable one.
    """
    pom_texts = list(pom_texts)
    pooled_props = {}
    for pom_text in pom_texts:
        pooled_props.update(_properties(pom_text))

    merged, seen = {}, set()

    def index(group_id, artifact_id, version):
        path = jar_path(m2_root, group_id, artifact_id, version)
        for simple, fqns in classes_in_jar(path).items():
            bucket = merged.setdefault(simple, [])
            for fqn in fqns:
                if fqn not in bucket:
                    bucket.append(fqn)

    def visit(pom_text, props, inherited_version, depth):
        for dep in dependencies(pom_text, props=props):
            version = dep['version'] or inherited_version
            if not version:
                continue
            key = (dep['group_id'], dep['artifact_id'], version)
            if key in seen:
                continue
            seen.add(key)
            index(*key)
            if depth >= max_depth:
                continue
            sub_pom = pom_path(m2_root, *key)
            if sub_pom.is_file():
                visit(sub_pom.read_text(errors='ignore'), {}, version, depth + 1)

    for pom_text in pom_texts:
        visit(pom_text, pooled_props, None, 0)
    return merged
