"""The Spring version of a generated project is the framework parent's, and nothing else sets one.

The framework parent imports the Spring Boot BOM, so every ``org.springframework`` / ``org.springframework.boot``
dependency and plugin inherits its version. A generated pom that writes a Spring version of its own, or a property
that names one, can only disagree with that parent, so those are removed and the artifacts inherit what the parent
manages. The one Spring-adjacent version the platform does set is springdoc, which the framework does not manage:
see ``pom_properties.pin_spring_line``, which aligns it to the parent's Boot line.
"""
import re

#: Group ids whose versions the Spring Boot BOM, imported by the framework parent, already manages.
SPRING_MANAGED_GROUPS = ('org.springframework.boot', 'org.springframework')
#: Properties that name a Spring version directly.
SPRING_VERSION_PROPERTIES = ('spring.version', 'spring-boot.version', 'spring-framework.version',
                             'spring-boot-dependencies.version', 'spring.boot.version')

_BLOCK = re.compile(r'<(dependency|plugin)>(.*?)</\1>', re.S)
_GROUP = re.compile(r'<groupId>\s*([^<>]+?)\s*</groupId>')
_ARTIFACT = re.compile(r'<artifactId>\s*([^<>]+?)\s*</artifactId>')
_VERSION = re.compile(r'[ \t]*<version>[^<>]*</version>[ \t]*\n?')


def _is_pom(path):
    return str(path).replace(chr(92), '/').endswith('pom.xml')


def strip_spring_versions(files):
    """``(files, action_items)`` with every Spring version a pom sets itself removed."""
    actions, out = [], []
    for path, content in files:
        if not _is_pom(path):
            out.append((path, content))
            continue
        text = content
        for match in _BLOCK.finditer(content):
            block = match.group(0)
            group = _GROUP.search(block)
            if not group or group.group(1) not in SPRING_MANAGED_GROUPS:
                continue
            version = _VERSION.search(block)
            if version:
                artifact = _ARTIFACT.search(block)
                text = text.replace(block, _VERSION.sub('', block, count=1), 1)
                actions.append('%s: removed the version on %s; the framework parent manages Spring.'
                               % (path, artifact.group(1) if artifact else group.group(1)))
        for name in SPRING_VERSION_PROPERTIES:
            found = re.search(r'[ \t]*<%s>[^<>]*</%s>[ \t]*\n?' % (re.escape(name), re.escape(name)), text)
            if found:
                text = text.replace(found.group(0), '', 1)
                actions.append('%s: removed the property %s; the framework parent sets the Spring version.' % (path, name))
        out.append((path, text))
    return out, actions
