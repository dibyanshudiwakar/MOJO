"""Build and verify ``agent-skills/`` from ``external-skills/iib-to-ms-migration``.

The source skill is one workflow; ``agent-skills/`` holds one self-contained
skill per catalogue agent. Every reference file has exactly ONE owning skill
(the original, or a verbatim line-range extract of one). A skill that needs a
file another skill owns gets a byte-identical COPY, so each skill directory can
be mounted on its own. The copy set is derived from the ``references/...``
links in each ``SKILL.md`` -- the links are the single source of what a skill
reads.

    python scripts/sync_agent_skills.py           # regenerate everything
    python scripts/sync_agent_skills.py --check   # exit 1 if anything is stale

Authored files (each ``SKILL.md`` body and ``known-variance-signatures.md``) are
never overwritten; only the generated *Agent binding* table inside ``SKILL.md``
is refreshed from ``config/agent-catalogue.v1.json``.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'external-skills' / 'iib-to-ms-migration'
OUT = ROOT / 'agent-skills'
CATALOGUE = ROOT / 'config' / 'agent-catalogue.v1.json'

AGENTS = ['migration-orchestrator', 'repository-intake', 'ace-analysis', 'framework-baseline',
          'migration-specification', 'planning', 'code-generation', 'test-engineering',
          'variance-analysis', 'rca', 'remediation', 'governance', 'knowledge-evidence']
SKILL = {a: 'iib-to-ms-' + a for a in AGENTS}
UNMAPPED = {'dormancy-intelligence':
            'The source skill never decides whether a service is worth migrating. It has no traffic, telemetry, '
            'deployment-inventory or dormancy content, so any skill written for this agent would be invented.'}

W, S, T, G = ('references/workflow-and-state.md', 'SKILL.md',
              'references/translation-table.md', 'references/gap-checklist.md')
# owner agent, file name, [(source file, [(first line, last line), ...]), ...]   -- 1-indexed, inclusive
EXTRACTS = [
    ('repository-intake', 'intake-procedure.md', [(W, [(9, 46)])]),
    ('ace-analysis', 'backend-type-detection.md', [(W, [(50, 66)])]),
    ('migration-orchestrator', 'state-file-and-resumption.md', [(W, [(1, 3), (70, 118)])]),
    ('migration-orchestrator', 'definition-of-done.md', [(S, [(44, 56)])]),
    ('migration-orchestrator', 'scope-boundary.md', [(W, [(270, 278)])]),
    ('migration-orchestrator', 'handoff-and-action-items.md', [(S, [(135, 165)])]),
    ('code-generation', 'scaffold-and-generate.md', [(W, [(122, 177)])]),
    ('code-generation', 'handoff-build-and-reconcile.md', [(W, [(181, 240), (259, 266)])]),
    ('test-engineering', 'smoke-test-procedure.md', [(W, [(242, 257)])]),
    ('remediation', 'fix-and-retry-policy.md', [(W, [(251, 257)]), (G, [(178, 183)])]),
    ('governance', 'verification-checklists.md', [(S, [(175, 217)])]),
    ('governance', 'hard-constraints.md', [(W, [(282, 293)])]),
    ('knowledge-evidence', 'evidence-hierarchy.md', [(W, [(5, 5)])]),
    ('planning', 'construct-mapping.md', [(T, [(1, 20)])]),
    ('migration-specification', 'type-and-context-root-rules.md', [(T, [(22, 41)])]),
]
# whole-file copies of source references (path relative to the source's references/)
WHOLE = [('code-generation', n) for n in [
    'mandatory-migration-rules.md', 'gap-checklist.md', 'ql-cics-camel-bindy.md', 'rxclaim-sbo-xml-jaxb.md',
    'ibm-mq-cobol-copybook.md', 'rest-external-direct.md', 'soap-backend.md']] + [
    ('framework-baseline', n) for n in [
        'structural-conventions.md', 'fixed-file-templates.md', 'templates/config-map-baseline.properties']] + [
    ('rca', 'common-patterns-and-gotchas.md')]
# files owned by a skill but sourced from another folder under external-skills/
EXTERNAL = [
    ('variance-analysis', 'parity-skill.md', ROOT / 'external-skills' / 'gap-analysis' / 'parity-skill.md'),
    ('variance-analysis', 'hardcoding-skill.md', ROOT / 'external-skills' / 'hardcoding' / 'hardcoding-skill.md'),
]
# hand-written reference files that live in the repo and are only copied onward
AUTHORED = [('variance-analysis', 'known-variance-signatures.md')]

HDR = ('<!-- Extracted verbatim from external-skills/iib-to-ms-migration/ -- {desc}. '
       'File names mentioned inside (for example `references/gap-checklist.md`) are the ORIGINAL names; '
       'every file mentioned there that this skill uses sits in its own references/ folder. -->\n\n')
LINK = re.compile(r'`references/([^`\s]+)`')


def sha(b):
    return hashlib.sha256(b).hexdigest()


def lf(b):
    """Line endings are not content: a Windows checkout (core.autocrlf) turns LF into CRLF."""
    return b.replace(b'\r\n', b'\n')


def src_lines(rel):
    return (SRC / rel).read_text(encoding='utf-8').split('\n')


def rng(r):
    return '%d' % r[0] if r[0] == r[1] else '%d–%d' % r


def owners():
    """file name (relative to a references/ folder) -> owning agent."""
    o = {}
    for a, n, _ in EXTRACTS:
        o[n] = a
    for a, n in WHOLE + AUTHORED:
        o[n] = a
    for a, n, _ in EXTERNAL:
        o[n] = a
    return o


def catalogue():
    agents = json.loads(CATALOGUE.read_text(encoding='utf-8'))['agents']
    key = 'id' if 'id' in agents[0] else 'agent_id'
    return {a[key]: a for a in agents}


def contract_block(a, aid):
    j = lambda v: ', '.join('`%s`' % i for i in v) if v else '—'
    return ('## Agent binding\n\n'
            '_Generated from `config/agent-catalogue.v1.json` by `scripts/sync_agent_skills.py`; '
            'edit the catalogue, not this table._\n\n'
            '| | |\n|---|---|\n'
            f"| Agent id | `{aid}` ({a['display_name']}) |\n"
            f"| Required role | `{a['required_role']}` |\n"
            f"| Depends on | {j(a['dependencies'])} |\n"
            f"| Accepted inputs | {j(a['accepted_inputs'])} |\n"
            f"| Produced outputs | {j(a['produced_outputs'])} |\n"
            f"| Tool allow-list | {j(a['tool_allowlist'])} |\n")


def extract_bytes(parts):
    desc = '; '.join('%s lines %s' % ('SKILL.md' if s == S else s, ', '.join(rng(r) for r in rs))
                     for s, rs in parts)
    chunks = []
    for s, rs in parts:
        L = src_lines(s)
        chunks.append('\n'.join('\n'.join(L[a - 1:b]) for a, b in rs))
    return (HDR.format(desc=desc) + '\n\n'.join(chunks).rstrip('\n') + '\n').encode('utf-8')


def plan():
    """Return (files {Path: bytes}, manifest dict, errors []). Touches nothing on disk."""
    cat = catalogue()
    own = owners()
    errors = []
    files = {}
    owned_bytes = {}                       # file name -> bytes of the owner's version
    for a, n, parts in EXTRACTS:
        owned_bytes[n] = extract_bytes(parts)
    for a, n in WHOLE:
        owned_bytes[n] = lf((SRC / 'references' / n).read_bytes())
    for a, n, src in EXTERNAL:
        owned_bytes[n] = lf(src.read_bytes())
    for a, n in AUTHORED:
        owned_bytes[n] = lf((OUT / SKILL[a] / 'references' / n).read_bytes())

    for cid in cat:
        if cid not in SKILL and cid not in UNMAPPED:
            errors.append('catalogue agent has neither a skill nor a stated reason: ' + cid)

    agents_out = []
    for aid in AGENTS:
        if aid not in cat:
            errors.append('unknown agent: ' + aid)
            continue
        d = OUT / SKILL[aid]
        skill_md = d / 'SKILL.md'
        if not skill_md.exists():
            errors.append('missing ' + str(skill_md.relative_to(ROOT)))
            continue
        text = skill_md.read_text(encoding='utf-8')
        text = re.sub(r'## Agent binding\n.*?(?=\n## )', lambda m: contract_block(cat[aid], aid), text,
                      count=1, flags=re.S)
        if '## Agent binding' not in text:
            errors.append('no "## Agent binding" section in ' + SKILL[aid])
        if re.search(r'`\.\./', text):
            errors.append('%s/SKILL.md links outside its own folder (use references/...)' % SKILL[aid])
        skill_bytes = text.encode('utf-8')
        files[skill_md] = skill_bytes

        needed = set()
        for m in LINK.finditer(text):
            n = m.group(1)
            if n not in own:
                errors.append('%s/SKILL.md links to unknown reference: %s' % (SKILL[aid], n))
            else:
                needed.add(n)
        mine = {n for n, o in own.items() if o == aid}
        refs = []
        for n in sorted(mine | needed):
            b = owned_bytes[n]
            files[d / 'references' / n] = b
            refs.append({'path': 'references/' + n, 'sha256': sha(b),
                         'kind': 'owned' if own[n] == aid else 'copy', 'owner': SKILL[own[n]]})
        agents_out.append({
            'agent_id': aid, 'skill_name': SKILL[aid], 'skill_path': SKILL[aid] + '/SKILL.md',
            'skill_sha256': sha(skill_bytes), 'required_role': cat[aid]['required_role'],
            'depends_on': cat[aid]['dependencies'], 'references': refs})

    manifest = {
        'source_skill': 'external-skills/iib-to-ms-migration',
        'additional_sources': sorted({str(src.parent.relative_to(ROOT)).replace('\\', '/') for _, _, src in EXTERNAL}),
        'catalogue': 'config/agent-catalogue.v1.json',
        'note': 'One self-contained skill per agent. Each reference file has one owner; other skills hold '
                'byte-identical copies (kind=copy). Regenerate with scripts/sync_agent_skills.py.',
        'agents': agents_out,
        'agents_without_skill': [{'agent_id': k, 'reason': v} for k, v in UNMAPPED.items()],
        'reference_owners': {n: SKILL[a] for n, a in sorted(own.items())},
    }
    files[OUT / 'skills-map.json'] = (json.dumps(manifest, indent=2) + '\n').encode('utf-8')
    files[OUT / 'README.md'] = readme(manifest).encode('utf-8')
    return files, manifest, errors


def readme(m):
    rows = '\n'.join('| `%s` | [`%s`](%s) | %s | %s |' % (
        a['agent_id'], a['skill_name'], a['skill_path'],
        ', '.join('`%s`' % Path(r['path']).name for r in a['references'] if r['kind'] == 'owned') or '—',
        sum(1 for r in a['references'] if r['kind'] == 'copy')) for a in m['agents'])
    owners_rows = '\n'.join('| `%s` | `%s` |' % (n, o) for n, o in m['reference_owners'].items())
    return f"""# Agent skills — IIB → Spring Boot migration

`external-skills/iib-to-ms-migration` is one skill: a single linear workflow (`INTAKE → SCAFFOLD → GENERATE → HANDOFF`) with about 4,000 lines of references. The factory runs **14 agents**, each with a narrow tool contract. This folder breaks the single skill into **one self-contained skill per agent**.

The source skill is left untouched. `app/agents/skills.py` loads these skills (see `docs/AGENTS_DEVELOPMENT.md`, "Agent skills").

## Agent → skill

| Agent (`config/agent-catalogue.v1.json`) | Skill | Reference files it owns | Copies of others' files |
|---|---|---|---|
{rows}
| `dormancy-intelligence` | **none** | — | — |

### Why `dormancy-intelligence` has no skill

{m['agents_without_skill'][0]['reason']}

If you later want one, the content has to come from somewhere other than this skill.

## How the split was made

- **By who does the work, not by file.** Each part of the pipeline was assigned to the agent whose contract performs it, and the tool allow-list was respected: `repository-intake` cannot raise clarifications (so its skill reports *gaps*), `remediation` plans changes but does not write source, `test-engineering` can run what was built but cannot rebuild.
- **Other external skills.** `parity-skill.md` (from `external-skills/gap-analysis/`) and `hardcoding-skill.md` (from `external-skills/hardcoding/`) are owned by `iib-to-ms-variance-analysis`; the sync script copies them like the others.
- **No retyping.** Owned reference files are byte-identical copies of the originals, or cuts of them by exact line range; each cut starts with an HTML comment naming the source and ranges.
- **Self-contained.** A skill that needs a file owned by another skill holds a byte-identical **copy** in its own `references/` folder, so any skill directory can be mounted alone. The copy set is derived from the `references/...` links in that skill's `SKILL.md`. Copies are never edited by hand.
- **Drift is detected, not trusted.** `python scripts/sync_agent_skills.py --check` fails if any copy differs from its owner, if a generated table is stale, or if a `SKILL.md` links outside its folder. Every file's SHA-256 is in `skills-map.json`, and the loader verifies them at load time.
- **Authored, not extracted:** all 13 `SKILL.md` files (summaries of the agent's part of the workflow with a *Boundaries* section from the tool allow-list, and in places an *Adaptation note*), and `iib-to-ms-variance-analysis/references/known-variance-signatures.md`. The "suggested class" column in that index is a labelling choice made here. Treat the authored files as the part to review.

## Editing

1. Change an **owned** file in its owning skill (or change the source skill and re-sync), or edit a `SKILL.md` body.
2. Run `python scripts/sync_agent_skills.py` to refresh copies, the *Agent binding* tables, `skills-map.json` and this README.
3. Run `python scripts/sync_agent_skills.py --check` (also run by `tests/agents/test_agent_skills.py`).

The extracted ranges are line-number based: if the source skill changes and lines shift, the ranges in the script need updating.

## Who owns each reference file

Text inside copied and extracted files still uses the **original** names (for example `references/gap-checklist.md`); each skill has the files it uses in its own `references/`.

| File | Owner |
|---|---|
{owners_rows}
"""


def apply(files):
    for p, b in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or lf(p.read_bytes()) != b:
            p.write_bytes(b)


def stale_files(files):
    """Files under references/ folders that the plan does not produce (e.g. a copy no longer linked)."""
    keep = set(files)
    out = []
    for aid in AGENTS:
        ref = OUT / SKILL[aid] / 'references'
        if ref.exists():
            out += [p for p in ref.rglob('*') if p.is_file() and p not in keep]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', action='store_true', help='report drift and exit 1; write nothing')
    args = ap.parse_args(argv)
    files, _, errors = plan()
    stale = stale_files(files)
    if args.check:
        for p, b in files.items():
            if not p.exists():
                errors.append('missing: ' + str(p.relative_to(ROOT)))
            elif lf(p.read_bytes()) != b:
                errors.append('out of date: ' + str(p.relative_to(ROOT)))
        errors += ['stale (no SKILL.md links to it): ' + str(p.relative_to(ROOT)) for p in stale]
        for e in errors:
            print('  -', e)
        print('agent-skills: %s' % ('DRIFT (%d problems)' % len(errors) if errors else 'in sync'))
        return 1 if errors else 0
    if errors:
        for e in errors:
            print('  -', e)
        return 1
    apply(files)
    for p in stale:
        p.unlink()
    print('agent-skills: wrote %d files, removed %d stale' % (len(files), len(stale)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
