"""ACE Analysis Agent.

Builds the ACE semantic model -- flows, subflows, ESQL, maps, schemas, policies,
interfaces, dependencies and observable behaviour -- and records the constructs
that have no supported target mapping.

The model reads the source. One request per source file, each guided by the
reference in this agent's skill that covers that construct family, and each
falling back to ``ACEParser``'s own parse of that one file when the gateway
cannot answer or the response does not match its schema. The merge and the
finalisation stay deterministic, so a model-authored semantic model hashes
exactly as a parsed one does and one G2 approval binds either.

``PriorityOneService.record_analysis`` then persists whichever model was
produced, deriving the content hash, the parser coverage report and the
unsupported-construct rows from the model itself.

This agent requests ``clarification.create`` but returns no clarifications of its
own: ``analyze_ace`` already inserts one per ambiguity the parser could not
resolve, on this agent's behalf. Returning them again would duplicate every
question. The tool is in the requested set because the capability really is
exercised during the run -- the registry checks what an agent may do, and this
run does create clarifications.
"""
import json
from app.agents.artefact import produce_many
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.services.priority1 import PriorityOneService

#: Which skill reference a source file's reading is guided by. The whole skill
#: is thousands of lines; narrowing it per file keeps each request about the one
#: construct family in front of the model, which is what ``Skill.focus`` is for.
#: Verified against the skill's own reference list -- a name this skill does
#: not carry makes focus_many raise, the agent fall back to the unfocused
#: skill, and the narrowing silently stop happening. tests/agents/
#: test_agent_skill_focus.py checks every name named in an agent against the
#: skill it belongs to for exactly that reason.
FOCUS = {'.esql': 'references/construct-mapping.md',
         '.msgflow': 'references/construct-mapping.md',
         '.subflow': 'references/construct-mapping.md'}


class ACEAnalysisAgent(BaseAgent):
    AGENT_ID = 'ace-analysis'
    SKILL = 'iib-to-ms-ace-analysis'
    TOOLS = ('repository.read', 'parser.execute', 'evidence.write', 'clarification.create')
    OUTPUTS = ('analysis_document', 'semantic_model', 'ambiguities')
    PROMPT = 'agent/ace-analysis/semantic-model'

    def _read_sources(self, context, service, path):
        """The semantic model, read by the model one source file at a time.

        One request per file rather than per repository: a 200-node flow plus
        its ESQL does not fit in one response.

        All or nothing across the files. A per-file fallback to the parser left
        an analysis that was part model-read and part regex-parsed, and nothing
        downstream could tell which flows came from which -- so a construct the
        parser does not understand looked identical to one the model decided was
        absent. Each file gets its own retry budget instead.
        """
        parser = service.parser
        # ValueError('repository_not_accessible') propagates: an unreadable path
        # is an operator error, not a model outage, and it used to be answered
        # by parsing the same unreadable path -- which produced an empty model
        # the pipeline then treated as a real analysis of a repository with
        # nothing in it.
        sources = parser.sources(path)
        by_path = {rel: (text, ext) for rel, text, ext in sources}
        skill = self._focused_skill(context, by_path)
        items = [(rel, {'path': rel, 'kind': ext, 'text': text})
                 for rel, text, ext in sources]
        fragments, provenance = produce_many(context, self.PROMPT, items, skill=skill)
        # The merge stays deterministic: it is list concatenation, and leaving
        # it in code is what stops the assembly inventing, dropping or
        # reordering a construct the per-file answers established.
        return parser.merge([fragments[rel] for rel, _, _ in sources if rel in fragments]), provenance

    def _focused_skill(self, context, by_path):
        """Narrow the skill to the references the files at hand actually need."""
        try:
            own = context.skill()
        except Exception:
            return None
        if own is None:
            return None
        wanted = sorted({FOCUS[ext] for _, ext in by_path.values() if ext in FOCUS})
        if not wanted:
            return own
        try:
            return own.focus_many(wanted)
        except Exception:
            # A reference the skill does not carry is guidance missing, not a
            # failure: the unfocused skill is still correct guidance.
            return own

    def execute(self, context):
        version = context.param('version', '1.0.0')
        service = PriorityOneService(self.repo)
        model, provenance = self._read_sources(context, service, context.param('path'))
        result = service.record_analysis(context.job_id, model, version)
        row = self.repo.one('select * from ace_analyses where id=?', (result['analysis_id'],))
        model = json.loads(row['semantic_model'])
        ambiguities = json.loads(row['ambiguities'] or '[]')
        metrics = {'coverage_score': result['coverage_score'],
                   'unsupported_count': result['unsupported_count'],
                   'ambiguity_count': result['ambiguity_count'],
                   'interface_count': len(model.get('interfaces', [])),
                   # Determined in this step, the same as the master skill's
                   # own INTAKE output states "Backend type: determined in
                   # INTAKE" up front -- code-generation reads model['backend_type']
                   # directly rather than re-deriving it later.
                   'backend_type': model.get('backend_type', {}).get('type', 'UNDETERMINED'),
                   'backend_type_signal': model.get('backend_type', {}).get('signal', ''),
                   # Every source file is model-read or the agent failed, so
                   # what is left to record is how many files were read and how
                   # many requests the last of them took.
                   'sources_read': provenance.get('item_count', 0),
                   'model_attempts': provenance.get('attempts', 0)}
        return AgentResult(
            status='CLARIFICATION_REQUIRED' if ambiguities else 'COMPLETED',
            outputs={'analysis_document': result['analysis_id'],
                     'semantic_model': row['content_hash'],
                     'ambiguities': ambiguities},
            # content_hash of an analysis is the digest of the semantic model
            # json, so a G2 approval binding this evidence also binds the model.
            evidence=(EvidenceRef('ACE_ANALYSIS', version,
                                  'analysis://' + result['analysis_id'], row['semantic_model'],
                                  {'coverage_report_id': result['coverage_report_id'],
                                   'coverage_score': result['coverage_score'],
                                   'unsupported_count': result['unsupported_count']}),),
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(ACEAnalysisAgent))
