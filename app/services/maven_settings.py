"""The generated repository's Maven ``settings.xml``, and the ``-s`` flag every build passes.

The org's artifacts (the ``framework-starter`` parent and the rest) resolve only through the
internal Nexus repositories that this file names. A build that does not read it fails with a
non-resolvable parent POM before anything is compiled, so the file is written into every
generated project (code generation) and every Maven build of that project passes it
(``build_check``, the quality gate, the repair loops).

The content is the code-generation skill's ``references/templates/maven-settings.xml``, copied
verbatim. Nothing here retypes it, so the skill is the one place it can change.
"""
from pathlib import Path

NAME = 'settings.xml'
TEMPLATE = 'maven-settings.xml'


def template():
    """The skill's settings text with LF line endings, or ``None`` if the skill cannot be read."""
    try:
        from app.agents.skills import skill_for
        skill = skill_for('code-generation')
        return skill.reference(TEMPLATE).replace('\r\n', '\n') if skill else None
    except Exception:                                  # noqa: BLE001 -- a missing skill must not break a build
        return None


def find(root):
    """The project's ``settings.xml`` (at its root), or ``None``."""
    path = Path(root) / NAME
    return path if path.is_file() else None


def args(root):
    """``['-s', '<root>/settings.xml']`` when the project has one, else ``[]``."""
    path = find(root)
    return ['-s', str(path)] if path else []


def write(root):
    """Write the template into ``root``. Returns the path, or ``None`` if there is no template to write."""
    text = template()
    if text is None:
        return None
    path = Path(root) / NAME
    path.write_bytes(text.encode('utf-8'))
    return path
