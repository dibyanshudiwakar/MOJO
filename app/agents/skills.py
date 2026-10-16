"""Load the per-agent skills from ``agent-skills/``.

A skill is guidance, not authority. It tells a model-assisted step what the
migration playbook says about the agent's part of the work; it grants no tool,
changes no contract and never replaces the deterministic result. What it *is*
is a versioned input to a run, so a run records which skill (by content hash)
it was given -- the same reason ``reasoning.py`` records the approved prompt
hash.

Integrity is verified when a skill is loaded: ``agent-skills/skills-map.json``
carries a SHA-256 for every file, and a file that is missing or has changed
raises ``SkillIntegrityError`` rather than quietly giving a model different
guidance than the one that was reviewed. Callers that treat the skill as
optional (``AgentContext.skill_provenance``) record the error and carry on.

    python -m app.agents.skills                 # list agents and their skills
    python -m app.agents.skills --verify        # check every file hash (exit 1 on problems)
    python -m app.agents.skills --show rca      # print one skill's SKILL.md
"""
import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / 'agent-skills'
ROOT_ENV = 'FACTORY_AGENT_SKILLS_DIR'


class SkillIntegrityError(RuntimeError):
    """A skill is missing, or does not match the hashes recorded in the manifest."""


@dataclass(frozen=True)
class Skill:
    agent_id: str
    name: str
    root: Path
    text: str
    #: path relative to the skill directory (``references/x.md``) -> file text
    references: dict = field(default_factory=dict)
    content_hash: str = ''
    #: Set when this skill was narrowed to one reference file by ``focus``; ``''`` for a whole skill.
    focused_on: str = ''

    def focus(self, reference):
        """This skill narrowed to one of its reference files.

        For a step whose guidance *is* that file and must not be mixed with the
        agent's general SKILL.md -- for example a reference that defines its own
        output contract. ``KeyError`` if the skill has no such reference.
        """
        text = self.reference(reference)
        return Skill(self.agent_id, '%s#%s' % (self.name, reference), self.root, text, {},
                     hashlib.sha256(text.encode('utf-8')).hexdigest(), reference)

    def focus_many(self, references):
        """This skill narrowed to several reference files, combined with SKILL.md.

        ``focus`` only narrows to one reference and drops SKILL.md entirely --
        code-generation's business-logic synthesis needs SKILL.md's own
        guidance *plus* structural-conventions.md *plus* whichever
        connector-specific reference the detected backend type names, all in
        one call. ``KeyError`` if any reference is missing, same as ``focus``.
        """
        parts = [self.text.rstrip('\n')]
        for ref in references:
            parts.append('---\n' + self.reference(ref).rstrip('\n'))
        combined = ('\n\n'.join(parts)) + '\n'
        joined = '+'.join(references)
        return Skill(self.agent_id, '%s#%s' % (self.name, joined), self.root, combined, {},
                     hashlib.sha256(combined.encode('utf-8')).hexdigest(), joined)

    def reference(self, name):
        """One reference by file name or by its ``references/...`` path."""
        for path, text in self.references.items():
            if path == name or path.rsplit('/', 1)[-1] == name:
                return text
        raise KeyError(name)

    def provenance(self):
        out = {'skill_name': self.name, 'skill_hash': self.content_hash}
        if self.focused_on:
            out['skill_focus'] = self.focused_on
        return out

    def as_system_prompt(self):
        """The skill as one block of guidance for a model adapter to place in its system prompt.

        SKILL.md in full, then an index of the reference files (not their text --
        they are large; fetch one with ``reference(name)`` when a step needs it).
        """
        if self.focused_on:
            return self.text.rstrip('\n') + '\n'
        index = '\n'.join('- ' + path for path in sorted(self.references))
        return ('%s\n\n---\nReference files available for this agent (%s):\n%s\n'
                % (self.text.rstrip('\n'), self.name, index))


class SkillLibrary:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get(ROOT_ENV) or DEFAULT_ROOT)
        self._manifest = None
        self._cache = {}

    def manifest(self):
        if self._manifest is None:
            path = self.root / 'skills-map.json'
            try:
                self._manifest = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise SkillIntegrityError('skills_manifest_unreadable:%s:%s' % (path, exc)) from exc
        return self._manifest

    def _entry(self, agent_id):
        return next((a for a in self.manifest()['agents'] if a['agent_id'] == agent_id), None)

    def agent_ids(self):
        return [a['agent_id'] for a in self.manifest()['agents']]

    def has(self, agent_id):
        return self._entry(agent_id) is not None

    def skill_name(self, agent_id):
        """The skill the manifest assigns to ``agent_id``, or ``None``. Reads the manifest only (no file hashes)."""
        entry = self._entry(agent_id)
        return entry['skill_name'] if entry else None

    def absence_reason(self, agent_id):
        """Why an agent deliberately has no skill, or ``''`` if it has one / is unknown."""
        for a in self.manifest().get('agents_without_skill', []):
            if a['agent_id'] == agent_id:
                return a['reason']
        return ''

    def _read(self, skill_dir, rel, expected):
        path = skill_dir / rel
        try:
            # Line endings are not content: git on Windows (core.autocrlf) checks these
            # files out as CRLF, while the manifest hashes the LF form. Compare like with like.
            data = path.read_bytes().replace(b'\r\n', b'\n')
        except OSError as exc:
            raise SkillIntegrityError('skill_file_missing:%s/%s' % (skill_dir.name, rel)) from exc
        if hashlib.sha256(data).hexdigest() != expected:
            raise SkillIntegrityError('skill_file_modified:%s/%s' % (skill_dir.name, rel))
        return data.decode('utf-8')

    def load(self, agent_id):
        """Return the verified ``Skill`` for ``agent_id``, or ``None`` if it has none."""
        entry = self._entry(agent_id)
        if entry is None:
            return None
        if agent_id in self._cache:
            return self._cache[agent_id]
        skill_dir = self.root / entry['skill_name']
        text = self._read(skill_dir, 'SKILL.md', entry['skill_sha256'])
        refs = {r['path']: self._read(skill_dir, r['path'], r['sha256']) for r in entry['references']}
        combined = entry['skill_sha256'] + ''.join(r['path'] + r['sha256'] for r in entry['references'])
        skill = Skill(agent_id, entry['skill_name'], skill_dir, text, refs,
                      hashlib.sha256(combined.encode()).hexdigest())
        self._cache[agent_id] = skill
        return skill

    def verify(self):
        """Every problem found across every skill; empty means the library is intact."""
        problems = []
        for aid in self.agent_ids():
            try:
                self.load(aid)
            except SkillIntegrityError as exc:
                problems.append(str(exc))
        return problems


#: Set to ``1`` to make a damaged or missing skill refuse the run instead of being
#: recorded and skipped. Off by default: a skill is guidance, and a deployment
#: that has not yet synced its skills should still be able to run agents.
REQUIRE_ENV = 'FACTORY_REQUIRE_AGENT_SKILLS'


def skills_required():
    return os.environ.get(REQUIRE_ENV, '') == '1'


_default = {}


def library():
    """The process-wide library for the current ``FACTORY_AGENT_SKILLS_DIR``."""
    root = os.environ.get(ROOT_ENV) or str(DEFAULT_ROOT)
    if root not in _default:
        _default[root] = SkillLibrary(root)
    return _default[root]


def skill_for(agent_id):
    """The verified skill for an agent, ``None`` if it has none; raises on a damaged skill."""
    return library().load(agent_id)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m app.agents.skills', description=__doc__.split('\n')[0])
    ap.add_argument('--verify', action='store_true', help='check every file hash; exit 1 on problems')
    ap.add_argument('--show', metavar='AGENT_ID', help="print one agent's SKILL.md")
    args = ap.parse_args(argv)
    lib = SkillLibrary()
    try:
        if args.show:
            skill = lib.load(args.show)
            if skill is None:
                print('no skill for %s: %s' % (args.show, lib.absence_reason(args.show) or 'unknown agent'))
                return 1
            print(skill.text)
            return 0
        if args.verify:
            problems = lib.verify()
            for p in problems:
                print('  -', p)
            print('skills: %s' % ('%d problem(s)' % len(problems) if problems else 'intact'))
            return 1 if problems else 0
        for aid in lib.agent_ids():
            s = lib.load(aid)
            print('%-24s %-36s %s  (%d references)' % (aid, s.name, s.content_hash[:12], len(s.references)))
        for a in lib.manifest().get('agents_without_skill', []):
            print('%-24s %s' % (a['agent_id'], '(no skill)'))
    except SkillIntegrityError as exc:
        print('skills: ' + str(exc))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
