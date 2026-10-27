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


def complete(files):
    """``(files, action_items)`` with each undefined, known property added to the parent pom."""
    files, actions = resolve_parent_versions(files)
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
