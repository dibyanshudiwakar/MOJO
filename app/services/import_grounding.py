"""Point a generated file's imports at the classes the project's own dependencies actually contain.

The model writes Java against the framework from memory, and gets packages wrong: ``frameworks.annotation``
for ``frameworks.annotations``, ``frameworks.utilities.EieHeader`` for ``frameworks.eieheader.EieHeader``,
``frameworks.mqclient.utility.MQUtility`` for ``frameworks.ibmmq.MQUtility``. The build then stops at
"cannot find symbol" before anything else is checked.

The truth is in the local Maven cache, read through the same index the feedback repair uses
(``dependency_jars.project_classes``), so this is never a table of one framework's classes. A rewrite is
made only when all of these hold, and otherwise the import is left alone and reported:

* the import is in the organisation's own package namespace (the first four segments match a candidate),
* it does not resolve to anything the project declares or its dependencies contain,
* exactly one class of that simple name exists in that namespace.

Anything the index cannot place (a class the model never wrote, a method that does not exist) is not
guessed at; it is reported so the gate and the feedback agent see it as what it is.
"""
import os
import re
from pathlib import Path

from app.services import dependency_jars

_IMPORT = re.compile(r'^import\s+(?!static\s)([\w.]+)\s*;', re.M)
_PACKAGE = re.compile(r'(?m)^\s*package\s+([\w.]+)\s*;')
_TYPE = re.compile(r'(?m)^\s*(?:public\s+|final\s+|abstract\s+|static\s+)*(?:class|interface|record|enum)\s+(\w+)')
#: How many leading segments make two names "the same namespace": com.cvshealth.eip.microservices
NAMESPACE_SEGMENTS = 4


def _local(files):
    """``{simple: [fqn]}`` for every type the generated java files themselves declare."""
    out = {}
    for path, text in files:
        if not str(path).endswith('.java'):
            continue
        package = _PACKAGE.search(text)
        if not package:
            continue
        for name in set(_TYPE.findall(text)):
            fqn = package.group(1) + '.' + name
            bucket = out.setdefault(name, [])
            if fqn not in bucket:
                bucket.append(fqn)
    return out


def _namespace(fqn):
    return '.'.join(fqn.split('.')[:NAMESPACE_SEGMENTS])


def m2_root():
    """The local Maven repository the build resolves from (``MOJO_M2_REPO`` overrides it)."""
    return Path(os.environ.get('MOJO_M2_REPO') or (Path.home() / '.m2' / 'repository'))


def ground(files, m2_root):
    """``(files, actions)`` with org imports corrected to the one real class each names.

    ``files`` is ``[(relative path, content)]``. ``m2_root`` is the local Maven repository. With nothing
    resolvable, every import is left as written and nothing is reported.
    """
    poms = [text for path, text in files if str(path).replace('\\', '/').rsplit('/', 1)[-1] == 'pom.xml']
    index = dependency_jars.project_classes(poms, m2_root) if poms else {}
    local = _local(files)
    known = {fqn for fqns in list(index.values()) + list(local.values()) for fqn in fqns}
    actions, out = [], []
    for path, text in files:
        if not str(path).endswith('.java'):
            out.append((path, text))
            continue
        for fqn in sorted(set(_IMPORT.findall(text))):
            if fqn in known:
                continue
            simple = fqn.rsplit('.', 1)[-1]
            ns = _namespace(fqn)
            cands = [c for c in index.get(simple, []) + local.get(simple, []) if _namespace(c) == ns]
            if not cands:
                if _org_import(fqn):
                    actions.append('%s imports %s, which the project and its dependencies do not contain'
                                   % (path, fqn))
                continue
            if len(set(cands)) == 1:
                text = re.sub(r'^import\s+%s\s*;' % re.escape(fqn), 'import %s;' % cands[0], text, flags=re.M)
                actions.append('%s: import %s corrected to %s' % (path, fqn, cands[0]))
            else:
                actions.append('%s imports %s, and %d classes of that name exist: %s'
                               % (path, fqn, len(set(cands)), ', '.join(sorted(set(cands)))))
        out.append((path, text))
    return out, actions


def _org_import(fqn):
    return fqn.startswith('com.cvshealth.')


def missing(files, m2_root):
    """``[(consumer path, fqn)]``: org imports nothing defines, in a package the generated project itself declares.

    Those are the project's own classes the model imported and never wrote (a request model, say), so the
    project is incomplete rather than wrong. An import of a package the project does not declare is not
    listed: that one names a framework class that may not exist, and writing one would invent it.
    """
    poms = [text for path, text in files if str(path).replace(chr(92), '/').rsplit('/', 1)[-1] == 'pom.xml']
    index = dependency_jars.project_classes(poms, m2_root) if poms else {}
    local = _local(files)
    known = {fqn for fqns in list(index.values()) + list(local.values()) for fqn in fqns}
    packages = {fqn.rsplit('.', 1)[0] for fqns in local.values() for fqn in fqns}
    out = []
    for path, text in files:
        if not str(path).endswith('.java'):
            continue
        for fqn in sorted(set(_IMPORT.findall(text))):
            if fqn in known or not _org_import(fqn):
                continue
            if fqn.rsplit('.', 1)[0] in packages and not any(
                    _namespace(c) == _namespace(fqn) for c in index.get(fqn.rsplit('.', 1)[-1], [])):
                out.append((path, fqn))
    return out

