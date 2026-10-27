"""Mechanical repairs for the skill gate's GAPs.

``skill_gate`` names what is wrong; the skill's own instruction for MEDIUM and
HIGH items is "auto-fix what's unambiguous; report the rest as named GAPs". This
is the unambiguous part: every repair here is one the skill's text spells out
(copy the baseline block, copy the listed key, swap the hostname prefix, delete
the parent ``target/``), and none needs a judgment about the service.

Each repair is idempotent and is proven by the same gate afterwards: the feedback
agent re-runs ``skill_gate.run`` and only the gate's verdict counts. What is left
for the model-assisted stage, or a person, is what the skill says needs judgment
(a missing Dockerfile or Helm chart, an exception package, Akeyless).
"""
import re
import shutil
from pathlib import Path

from app.services import skill_gate
from app.services.skill_gate import Project, keys

#: Check names (as ``skill_gate.run`` reports them) this module can repair.
REPAIRABLE = ('SKILL_A0', 'SKILL_A', 'SKILL_M5_M16', 'SKILL_M23', 'SKILL_M24', 'SKILL_M1_M2', 'SKILL_M27')

_TIERS = ('dev', 'qa', 'stress', 'uat', 'prod')


def tier(config_map_name):
    """``application.qa2-pbmaz.properties`` -> ``qa`` (one logback file per tier)."""
    env = re.sub(r'^application\.|\.properties$', '', config_map_name).split('-')[0]
    return next((t for t in _TIERS if env.startswith(t)), 'dev')


def _append(path, lines):
    path = Path(path)
    text = path.read_text(encoding='utf-8', errors='ignore') if path.exists() else ''
    if text and not text.endswith('\n'):
        text += '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + '\n'.join(lines) + '\n', encoding='utf-8')


def _baseline_lines():
    """key -> template line, in the template's order."""
    out = {}
    if skill_gate.BASELINE.exists():
        for line in skill_gate.BASELINE.read_text(encoding='utf-8').splitlines():
            m = skill_gate.KEY.match(line)
            if m:
                out[m.group(1)] = line
    return out


def fix_baseline(p):
    """[A0] Add each missing baseline key to the packaged file and to every config-map."""
    fixes, template = [], _baseline_lines()
    want = set(template)
    packaged = set()
    for name in ('application.properties', 'application-local.properties'):
        for f in p.packaged(name):
            packaged |= keys(p.read(f))
    missing = [k for k in template if k in want - packaged]
    if p.web_modules and missing:
        resources = p.web_modules[0] / 'src' / 'main' / 'resources'
        target = resources / ('application-local.properties' if (resources / 'application-local.properties').exists()
                              else 'application.properties')
        _append(target, ['', '# baseline (code-generation skill, HANDOFF-A0)']
                + [template[k].replace('{{LOGBACK_FILE_FOR_TIER}}', 'logback-dev.xml') for k in missing])
        fixes.append({'path': str(target), 'check': 'SKILL_A0',
                      'action': 'added %d baseline key(s): %s' % (len(missing), ', '.join(missing))})
    for f in p.config_maps:
        lacks = [k for k in template if k not in keys(f.read_text(encoding='utf-8', errors='ignore'))]
        if lacks:
            _append(f, ['', '# baseline (code-generation skill, HANDOFF-A0)']
                    + [template[k].replace('{{LOGBACK_FILE_FOR_TIER}}', 'logback-%s.xml' % tier(f.name)) for k in lacks])
            fixes.append({'path': str(f), 'check': 'SKILL_A0',
                          'action': 'added %d baseline key(s): %s' % (len(lacks), ', '.join(lacks))})
    return fixes


def fix_mvc_key(p):
    """[M5] ``spring.mvc.throw-exception-if-no-handler-found=true`` in the packaged application.properties."""
    fixes, key = [], 'spring.mvc.throw-exception-if-no-handler-found=true'
    for web in p.web_modules:
        base = web / 'src' / 'main' / 'resources' / 'application.properties'
        text = base.read_text(encoding='utf-8', errors='ignore') if base.exists() else ''
        if not re.search(r'^spring\.mvc\.throw-exception-if-no-handler-found\s*=\s*true', text, re.M):
            if base.exists():       # a wrong value is replaced, not duplicated
                base.write_text(re.sub(r'(?m)^spring\.mvc\.throw-exception-if-no-handler-found\s*=.*\n?', '', text),
                                encoding='utf-8')
            _append(base, [key])
            fixes.append({'path': str(base), 'check': 'SKILL_M5_M16', 'action': 'added ' + key})
    return fixes


def fix_runtime_keys(p):
    """[A] Copy each packaged runtime key into every config-map; never ``spring.profiles.active``."""
    fixes = []
    lines = {}
    for f in p.packaged('application.properties'):
        for line in p.read(f).splitlines():
            m = skill_gate.KEY.match(line)
            if m and m.group(1) != 'spring.profiles.active':
                lines.setdefault(m.group(1), line)
    for f in p.config_maps:
        text = f.read_text(encoding='utf-8', errors='ignore')
        have = keys(text)
        add = [lines[k] for k in lines if k not in have]
        if 'management.endpoints.web.exposure.include' not in have | set(lines):
            add.append('management.endpoints.web.exposure.include=health,info,metrics')
        cleaned = re.sub(r'(?m)^spring\.profiles\.active\s*=\s*local\s*\n?', '', text)
        if cleaned != text:
            f.write_text(cleaned, encoding='utf-8')
            fixes.append({'path': str(f), 'check': 'SKILL_A',
                          'action': 'removed spring.profiles.active=local (it activates the local profile in prod)'})
        if add:
            _append(f, add)
            fixes.append({'path': str(f), 'check': 'SKILL_A', 'action': 'copied %d packaged key(s)' % len(add)})
    return fixes


def fix_local_targets(p):
    """[M23] ``application-local.properties`` targets qa, not dev or prod."""
    fixes = []
    for f in p.packaged('application-local.properties'):
        text = f.read_text(encoding='utf-8', errors='ignore')
        new = re.sub(r'(?m)^(?!\s*#)(.*\.uri\s*=\s*https://)(?:dev|prod)', r'\1qa', text)
        if new != text:
            f.write_text(new, encoding='utf-8')
            fixes.append({'path': str(f), 'check': 'SKILL_M23', 'action': 'swapped the hostname prefix to qa'})
    return fixes


def fix_parent_target(p):
    """[M24] Remove ``target/`` under the parent module."""
    fixes = []
    for d in p.parent_modules:
        if (d / 'target').exists():
            shutil.rmtree(d / 'target', ignore_errors=True)
            fixes.append({'path': str(d / 'target'), 'check': 'SKILL_M24', 'action': 'removed the parent module target/'})
    return fixes


def fix_settings_xml(p):
    """[M27] Write the skill's ``settings.xml`` at the repo root."""
    from app.services import maven_settings
    path = maven_settings.write(p.root)
    return ([{'path': str(path), 'check': 'SKILL_M27', 'action': 'wrote settings.xml from the skill template'}]
            if path else [])


def fix_bundled_config_maps(p):
    """[M1][M2] A config-map bundled in the jar belongs under config/config-map/."""
    fixes = []
    dest = p.root / 'config' / 'config-map'
    for web in p.web_modules:
        for f in sorted((web / 'src' / 'main' / 'resources').glob('application.*-*.properties')):
            if f.name == 'application-local.properties' or not re.fullmatch(
                    r'application\.[a-z0-9]+-[a-z0-9]+\.properties', f.name):
                continue
            dest.mkdir(parents=True, exist_ok=True)
            if not (dest / f.name).exists():
                shutil.move(str(f), str(dest / f.name))
                fixes.append({'path': str(dest / f.name), 'check': 'SKILL_M1_M2',
                              'action': 'moved out of src/main/resources (it was bundled in the jar)'})
    return fixes


#: Order matters: the config-maps are moved before the baseline is added to them,
#: and the baseline is added before runtime keys are reconciled against them.
_STEPS = (('SKILL_M1_M2', fix_bundled_config_maps), ('SKILL_M5_M16', fix_mvc_key),
          ('SKILL_A0', fix_baseline), ('SKILL_A', fix_runtime_keys),
          ('SKILL_M23', fix_local_targets), ('SKILL_M24', fix_parent_target),
          ('SKILL_M27', fix_settings_xml))


def repair(root, open_checks):
    """Apply the repair for each named open check. Returns the fixes made."""
    wanted = set(open_checks)
    fixes = []
    for name, fn in _STEPS:
        if name in wanted:
            fixes += fn(Project(root))        # re-read each time: an earlier step may have changed the tree
    return fixes
