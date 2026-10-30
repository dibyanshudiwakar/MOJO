"""Make sure every ``${property}`` a generated pom uses is defined somewhere.

The model writes the web module's pom against the parent's properties, and sometimes uses one the
parent never defines. Maven then refuses to read the project ("'dependencies.dependency.version'
... must be a valid version but is '${springdoc.version}'") before anything is compiled. The skill
says the parent pom carries ``springdoc.version`` and ``camel.version``; this adds a property that is
used and not defined, to the parent, when the platform knows a value for it.

What it will not do is invent a version. A property with no known value is reported, not guessed, and
the gate's build check says what is missing.
"""
import re

_REF = re.compile(r'\$\{([\w.\-]+)\}')
_DEF = re.compile(r'<([\w.\-]+)>')

#: Defined by Maven itself or by the org's framework-starter parent, so a pom may use them without
#: defining them. (The starter's own properties were read from framework-starter 3.2.2.)
_PROVIDED_PREFIXES = ('project.', 'env.', 'settings.', 'java.', 'maven.', 'basedir', 'user.', 'os.', 'parent.')
_PROVIDED = {'framework.version', 'org-springframework-boot.version', 'jacoco.aggregate', 'jacoco.integration',
             'jacoco.unit', 'project.build.sourceEncoding', 'project.reporting.outputEncoding'}
_PROVIDED_SUFFIX = ('-plugin.version',)

#: Versions the platform knows. The framework-starter 3.2.2 parent sits on Spring Boot 4.1.x, and the
#: springdoc line that supports Boot 4 is 3.x (Boot 3.x projects use 2.x). The skill says to prefer a
#: sibling repo's pin; with none to read, this is the default and it is reported as an action item.
DEFAULTS = {'springdoc.version': '3.0.3'}


def _is_provided(name):
    return (name in _PROVIDED or name.startswith(_PROVIDED_PREFIXES) or name.endswith(_PROVIDED_SUFFIX))


def _poms(files):
    return [(i, p, c) for i, (p, c) in enumerate(files) if p.replace('\\', '/').rsplit('/', 1)[-1] == 'pom.xml']


def _defined(content):
    block = re.search(r'<properties>(.*?)</properties>', content, re.S)
    return set(_DEF.findall(block.group(1))) if block else set()


def undefined(files):
    """``{property: [pom paths that use it]}`` for properties no generated pom defines and the parents do not provide."""
    poms = _poms(files)
    known = set()
    for _i, _p, c in poms:
        known |= _defined(c)
    out = {}
    for _i, path, content in poms:
        body = re.sub(r'<!--.*?-->', '', content, flags=re.S)
        for name in _REF.findall(body):
            if name not in known and not _is_provided(name):
                out.setdefault(name, [])
                if path not in out[name]:
                    out[name].append(path)
    return out


def _add(content, additions):
    lines = ''.join('        <%s>%s</%s>\n' % (k, v, k) for k, v in additions)
    if '</properties>' in content:
        # The closing tag keeps the indent already in front of it, so the first new line borrows that.
        return content.replace('</properties>', lines[4:] + '    </properties>', 1)
    block = '    <properties>\n%s    </properties>\n' % lines
    for anchor in ('<modules>', '<build>', '<dependencies>'):
        if anchor in content:
            return content.replace(anchor, block + '    ' + anchor, 1)
    return content.replace('</project>', block + '</project>', 1)


def _values(files):
    """``{property: literal value}`` from every generated pom's ``<properties>``."""
    out = {}
    for _i, _p, c in _poms(files):
        block = re.search(r'<properties>(.*?)</properties>', c, re.S)
        for name, value in re.findall(r'<([\w.\-]+)>\s*([^<>]*?)\s*</\1>', block.group(1) if block else ''):
            if value and '${' not in value:
                out.setdefault(name, value)
    return out


def resolve_parent_versions(files):
    """``(files, action_items)`` with ``${property}`` replaced by its value inside each ``<parent>`` block.

    Maven reads a pom's parent before it reads that pom's properties, so ``<parent><version>${framework.version}
    </version>`` cannot work: the build stops with "Non-resolvable parent POM ... framework-starter:pom:${framework.version}".
    (Maven 3.5+ allows only ``revision``, ``sha1`` and ``changelist`` there, and the org builds with 3.3.9.)
    """
    values = _values(files)
    actions = []
    files = list(files)
    for i, path, content in _poms(files):
        def fix(block):
            def sub(m):
                name = m.group(1)
                if name not in values:
                    actions.append('%s: the <parent> block uses ${%s}, which Maven cannot resolve there, and no value '
                                   'is known for it.' % (path, name))
                    return m.group(0)
                actions.append('%s: <parent> used ${%s}; replaced with %s because Maven reads the parent before '
                               'the properties.' % (path, name, values[name]))
                return values[name]
            return _REF.sub(sub, block.group(0))
        fixed = re.sub(r'<parent>.*?</parent>', fix, content, flags=re.S)
        if fixed != content:
            files[i] = (path, fixed)
    return files, actions


# -- module coordinates ---------------------------------------------------------------------------------------------
_PARENT_BLOCK = re.compile(r'<parent>.*?</parent>', re.S)
#: Blocks whose groupId/artifactId/version belong to something other than the project itself.
_NOT_THE_PROJECT = re.compile(r'<(parent|dependencies|dependencyManagement|build|profiles|properties|modules|scm|'
                              r'reporting|distributionManagement|repositories|pluginRepositories)\b.*?</\1>', re.S)


def _coordinates(content):
    """``(groupId, artifactId, version, parent block or '')`` of a pom, inheriting groupId and version from its parent."""
    body = re.sub(r'<!--.*?-->', '', content, flags=re.S)
    parent_match = _PARENT_BLOCK.search(body)
    parent = parent_match.group(0) if parent_match else ''
    own = _NOT_THE_PROJECT.sub('', body)

    def tag(name, block):
        found = re.search(r'<%s>\s*([^<>]+?)\s*</%s>' % (name, name), block)
        return found.group(1) if found else ''

    return (tag('groupId', own) or tag('groupId', parent), tag('artifactId', own),
            tag('version', own) or tag('version', parent), parent)


def _normalise(path):
    parts = []
    for piece in str(path).replace(chr(92), '/').split('/'):
        if piece == '..':
            if parts:
                parts.pop()
        elif piece and piece != '.':
            parts.append(piece)
    return '/'.join(parts)


def align_module_parents(files):
    """``(files, action_items)`` with each module's ``<parent>`` naming the pom its ``relativePath`` points at.

    Maven reads a parent from ``relativePath`` only when the coordinates declared there match the pom found. When
    they do not (the web module says ``com.cvs.esl`` and the parent module is ``com.cvshealth.eip.microservices``)
    it falls back to the repository, where a SNAPSHOT that was never published cannot be found, and the build stops
    with "Non-resolvable parent POM" before anything compiles. The two poms are written separately, so nothing made
    them agree. The parent pom's own coordinates are the truth; the child takes them.
    """
    files = list(files)
    actions = []
    index = {_normalise(path): i for i, (path, _c) in enumerate(files)
             if str(path).replace(chr(92), '/').rsplit('/', 1)[-1] == 'pom.xml'}
    for path_key, i in sorted(index.items()):
        path, content = files[i]
        group, artifact, version, parent = _coordinates(content)
        relative = re.search(r'<relativePath>\s*([^<>]*?)\s*</relativePath>', parent)
        if not parent or not relative or not relative.group(1):
            continue
        base = path_key.rsplit('/', 1)[0] if '/' in path_key else ''
        target = _normalise((base + '/' if base else '') + relative.group(1))
        if not target.endswith('pom.xml'):
            target = _normalise(target + '/pom.xml')
        if target not in index:
            continue
        want_group, want_artifact, want_version, _p = _coordinates(files[index[target]][1])
        if not (want_group and want_artifact and want_version):
            continue
        fixed = parent
        for name, want in (('groupId', want_group), ('artifactId', want_artifact), ('version', want_version)):
            fixed = re.sub(r'(<%s>)\s*[^<>]*?\s*(</%s>)' % (name, name), lambda m, w=want: m.group(1) + w + m.group(2),
                           fixed, count=1)
        if fixed != parent:
            said = _coordinates(parent)[:3]
            files[i] = (path, content.replace(parent, fixed, 1))
            actions.append('%s: <parent> named %s:%s:%s but the pom at its relativePath is %s:%s:%s; the parent now '
                           'uses the pom\'s own coordinates, since Maven falls back to the repository when they differ.'
                           % ((path,) + tuple(x or '?' for x in said) + (want_group, want_artifact, want_version)))
    return files, actions


FRAMEWORK_GROUP = 'com.cvshealth.eip.microservices.frameworks'


def missing_modules(files):
    """Action items for each ``<module>`` a pom lists that no generated pom answers: Maven stops on the first."""
    index = {_normalise(path) for path, _c in files if str(path).replace(chr(92), '/').rsplit('/', 1)[-1] == 'pom.xml'}
    out = []
    for path, content in files:
        if str(path).replace(chr(92), '/').rsplit('/', 1)[-1] != 'pom.xml':
            continue
        base = _normalise(path).rsplit('/', 1)[0] if '/' in _normalise(path) else ''
        for module in re.findall(r'<module>\s*([^<>]+?)\s*</module>', re.sub(r'<!--.*?-->', '', content, flags=re.S)):
            target = _normalise((base + '/' if base else '') + module)
            if not target.endswith('pom.xml'):
                target = _normalise(target + '/pom.xml')
            if target not in index:
                out.append('%s lists module %s, but no pom was generated at %s; Maven cannot load the project.'
                           % (path, module, target))
    return out


def drop_unknown_framework_artifacts(files, m2_root):
    """``(files, action_items)`` with framework dependencies the framework's own BOM does not contain removed.

    The artifact names come from the model's memory; the BOM the project imports is the list of what exists. A
    dependency that is not in it can never resolve, so it is removed here, and the classes that needed it are then
    reported by the import check, which is the real error to act on.
    """
    from pathlib import Path
    version = _values(files).get('framework.version', '')
    bom = Path(m2_root).joinpath(*FRAMEWORK_GROUP.split('.'), 'framework-bom', version, 'framework-bom-%s.pom' % version)
    if not version or not bom.is_file():
        return files, []
    known = set(re.findall(r'<artifactId>\s*([\w.\-]+)\s*</artifactId>', bom.read_text(encoding='utf-8', errors='ignore')))
    actions, out = [], []
    for path, content in files:
        if str(path).replace(chr(92), '/').rsplit('/', 1)[-1] != 'pom.xml':
            out.append((path, content))
            continue
        text = content
        for block in re.findall(r'\s*<dependency>(?:(?!</dependency>).)*</dependency>\n?', content, re.S):
            group = re.search(r'<groupId>\s*([^<>]+?)\s*</groupId>', block)
            artifact = re.search(r'<artifactId>\s*([^<>]+?)\s*</artifactId>', block)
            if group and artifact and group.group(1) == FRAMEWORK_GROUP and artifact.group(1) not in known:
                text = text.replace(block, '\n', 1)
                actions.append('%s: removed the dependency on %s, which framework-bom %s does not contain.'
                               % (path, artifact.group(1), version))
        out.append((path, text))
    return out, actions


def complete(files):
    """``(files, action_items)`` with each undefined, known property added to the parent pom."""
    files, aligned = align_module_parents(files)
    aligned = aligned + missing_modules(files)
    files, actions = resolve_parent_versions(files)
    actions = aligned + actions
    missing = undefined(files)
    known = {name: DEFAULTS[name] for name in missing if name in DEFAULTS}
    if known:
        poms = _poms(files)
        parent = next((t for t in poms if '<packaging>pom</packaging>' in t[2]), poms[0] if poms else None)
        if parent:
            i, path, content = parent
            files = list(files)
            files[i] = (path, _add(content, sorted(known.items())))
            for name, value in sorted(known.items()):
                actions.append('%s is used by %s but the parent pom did not define it; added %s=%s to %s. '
                               'Confirm the version against a sibling repo of this family.'
                               % (name, ', '.join(missing[name]), name, value, path))
    for name in sorted(set(missing) - set(known)):
        actions.append('%s is used by %s and defined nowhere; Maven cannot read the project until it is. '
                       'No default is known for it.' % (name, ', '.join(missing[name])))
    return files, actions


# -- the Spring line -------------------------------------------------------------------------------------------
#: springdoc major version that supports each Spring Boot major. Springdoc 2.x targets Boot 3; 3.x targets Boot 4.
SPRINGDOC_FOR_BOOT = {'3': '2', '4': '3'}
_BOOT_VERSION = re.compile(r'<org-springframework-boot\.version>\s*([\d.]+)\s*</org-springframework-boot\.version>')
_TODO_COMMENT = re.compile(r'\s*<!--(?:(?!-->).)*\bTODO\b(?:(?!-->).)*-->\n?', re.S)


def _version_key(text):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r'[.\-]', text))


def _boot_in_clone(framework_root):
    """The Spring Boot version declared by the framework parent pom in the supplied framework clone, or ``None``."""
    from pathlib import Path
    if not framework_root or not Path(framework_root).is_dir():
        return None
    for pom in sorted(Path(framework_root).rglob('pom.xml'))[:200]:
        try:
            text = pom.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        if re.search(r'<artifactId>\s*framework-starter\s*</artifactId>', text):
            found = _BOOT_VERSION.search(text)
            if found:
                return found
    return None


def pin_spring_line(files, m2_root, framework_root=None):
    """``(files, action_items)`` with springdoc on the same Spring Boot line as the framework parent.

    The framework parent imports Spring Boot through ``org-springframework-boot.version``; that is the platform's
    Spring line, and it is the newest the framework ships. A springdoc pinned to an older line than Boot does not
    run on it, so the model's value is replaced by the newest springdoc in the same major line that is available
    locally. TODO comments are dropped from the poms: a pom has nothing to act on in one.
    """
    from pathlib import Path
    version = _values(files).get('framework.version', '')
    starter = Path(m2_root).joinpath(*FRAMEWORK_GROUP.split('.'), 'framework-starter', version,
                                     'framework-starter-%s.pom' % version)
    actions = []
    out = []
    boot = _boot_in_clone(framework_root) or (
        _BOOT_VERSION.search(starter.read_text(encoding='utf-8', errors='ignore')) if version and starter.is_file() else None)
    major = boot.group(1).split('.')[0] if boot else ''
    springdoc = SPRINGDOC_FOR_BOOT.get(major, '')
    line = ''
    if springdoc:
        root = Path(m2_root).joinpath('org', 'springdoc', 'springdoc-openapi-starter-webmvc-ui')
        candidates = [d.name for d in root.iterdir() if d.is_dir() and d.name.split('.')[0] == springdoc] if root.is_dir() else []
        line = max(candidates, key=_version_key, default=DEFAULTS.get('springdoc.version', ''))
    for path, content in files:
        text = _TODO_COMMENT.sub('', content) if str(path).endswith('pom.xml') else content
        if str(path).endswith('pom.xml') and text != content:
            actions.append('%s: removed a TODO comment; a pom carries no open decision in a comment.' % path)
        if line and str(path).endswith('pom.xml'):
            found = re.search(r'<springdoc\.version>\s*([^<>]*?)\s*</springdoc\.version>', text)
            if found and found.group(1) != line:
                text = text.replace(found.group(0), '<springdoc.version>%s</springdoc.version>' % line, 1)
                actions.append('%s: springdoc.version %s is on a different Spring line from Boot %s; set to %s.'
                               % (path, found.group(1), boot.group(1), line))
        out.append((path, text))
    return out, actions


# -- artifacts nothing manages ----------------------------------------------------------------------------------------
#: group:artifact pairs that the framework BOM and the Spring Boot BOM do not manage but the skill's standard poms use.
#: Without a version Maven stops reading the project ("'dependencies.dependency.version' ... is missing").
UNMANAGED_ARTIFACTS = ('org.openapitools:jackson-databind-nullable',)
_UNMANAGED_FALLBACK = {'org.openapitools:jackson-databind-nullable': '0.2.6'}


def pin_unmanaged_versions(files, m2_root):
    """``(files, action_items)`` with a version added to each unmanaged dependency that has none.

    The version is the newest one in the local Maven repository, which is the one the build can resolve offline; the
    table's value is used only when the repository has none. Dependencies that already carry a version, and every
    artifact a BOM manages, are left alone.
    """
    from pathlib import Path
    actions, out = [], []
    for path, content in files:
        if not str(path).replace(chr(92), '/').endswith('pom.xml'):
            out.append((path, content))
            continue
        text = content
        for match in re.finditer(r'<dependency>(?:(?!</dependency>).)*</dependency>', content, re.S):
            block = match.group(0)
            group = re.search(r'<groupId>\s*([^<>]+?)\s*</groupId>', block)
            artifact = re.search(r'<artifactId>\s*([^<>]+?)\s*</artifactId>', block)
            if not (group and artifact) or '<version>' in block:
                continue
            coordinate = '%s:%s' % (group.group(1), artifact.group(1))
            if coordinate not in UNMANAGED_ARTIFACTS:
                continue
            folder = Path(m2_root).joinpath(*group.group(1).split('.'), artifact.group(1))
            cached = [d.name for d in folder.iterdir() if d.is_dir()] if folder.is_dir() else []
            version = max(cached, key=_version_key) if cached else _UNMANAGED_FALLBACK.get(coordinate, '')
            if not version:
                continue
            closing = '</artifactId>'
            fixed = block.replace(closing, closing + '\n            <version>%s</version>' % version, 1)
            text = text.replace(block, fixed, 1)
            actions.append('%s: %s has no managed version; set to %s (the newest in the local Maven repository).'
                           % (path, coordinate, version))
        out.append((path, text))
    return out, actions
