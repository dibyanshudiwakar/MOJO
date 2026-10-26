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

Each unresolved ambiguity becomes a clarification. ``record_analysis`` stores
ambiguities on the analysis record itself but does not raise them as
clarifications (clarification blocking is disabled at that layer -- see its
own comment); this agent is the one holding ``clarification.create``, so it
turns each OPEN ambiguity into an answerable question rather than leaving it
visible only inside the semantic model's own JSON.
"""
import json
from app.agents.artefact import produce_many
from app.services import ace_analysis_report, automode, workspace
from app.agents.base import AgentResult, BaseAgent, Clarification, EvidenceRef
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

        ``.properties`` is carved out of that before it ever reaches the model.
        ``self.PROMPT`` names the file kinds it covers -- ``.msgflow``,
        ``.subflow``, ``.esql``, ``.map``, ``.xslt``, ``.xsd``, ``.wsdl``,
        ``.policyxml`` -- and ``.properties`` is not one of them, so asking it
        anyway returns a schema-valid answer with nothing extracted, never
        once failing in a way that would trip the parser fallback. Confirmed
        live: ``backend_properties`` came back empty on every real analysis,
        even though the real backend and test-harness URLs
        (``<node>#<vendor>ServiceURL`` / ``<node>#testHarness<vendor>Url``) are
        sitting right there in the file, in a fixed, confirmed naming
        convention ``ACEParser._env_properties`` already parses exactly.
        Folding it in directly, the same way the deterministic-only path does,
        is strictly more reliable than an LLM re-deriving a convention it was
        never told about -- this is a parsing problem, not a judgment one.
        """
        parser = service.parser
        # ValueError('repository_not_accessible') propagates: an unreadable path
        # is an operator error, not a model outage, and it used to be answered
        # by parsing the same unreadable path -- which produced an empty model
        # the pipeline then treated as a real analysis of a repository with
        # nothing in it.
        sources = parser.sources(path)
        properties = [(rel, text, ext) for rel, text, ext in sources if ext == '.properties']
        constructs = [(rel, text, ext) for rel, text, ext in sources if ext != '.properties']
        by_path = {rel: (text, ext) for rel, text, ext in constructs}
        skill = self._focused_skill(context, by_path)
        items, order = [], []
        for rel, text, ext in constructs:
            pieces = self._chunks(text)
            for n, piece in enumerate(pieces, 1):
                key = rel if len(pieces) == 1 else '%s#part%d' % (rel, n)
                payload = {'path': rel, 'kind': ext, 'text': piece}
                if len(pieces) > 1:
                    payload.update({'part': n, 'of_parts': len(pieces),
                                    'note': 'This is one line-aligned part of a larger file; '
                                            'report only the constructs that begin in this part.'})
                items.append((key, payload))
                order.append(key)
        fragments, provenance = produce_many(context, self.PROMPT, items, skill=skill)
        # A split file is still one source: report files read, not requests made.
        if provenance.get('item_count'):
            provenance['item_count'] = len(constructs)
        # The merge stays deterministic: it is list concatenation, and leaving
        # it in code is what stops the assembly inventing, dropping or
        # reordering a construct the per-file answers established.
        for fragment in fragments.values():
            self._literals_as_text(fragment)
        model = parser.merge([fragments[k] for k in order if k in fragments])
        for rel, text, ext in properties:
            parser.fold(model, rel, text, ext)
        # Folded deterministically, not asked of the model, but still real
        # sources this analysis read -- counted in so sources_read reports the
        # whole repository rather than silently undercounting by however many
        # .properties files it has.
        if properties:
            provenance = dict(provenance, item_count=provenance.get('item_count', 0) + len(properties))
        return model, provenance

    #: Serialised characters of file text per request. Well under
    #: ``artefact.MAX_PAYLOAD_CHARS`` so the path, precedents, lessons and the
    #: retry correction that share the payload still fit.
    CHUNK_CHARS = 120_000

    @classmethod
    def _chunks(cls, text):
        """``text`` as one or more line-aligned parts that each fit a request.

        Sized by JSON-escaped length, since that is what the payload limit
        measures. A file that fits stays one part, so the common case is
        unchanged; only an oversized file is split.
        """
        if len(json.dumps(text)) <= cls.CHUNK_CHARS:
            return [text]
        parts, current, size = [], [], 0
        step = cls.CHUNK_CHARS // 6
        for line in text.splitlines(keepends=True):
            while len(json.dumps(line)) > cls.CHUNK_CHARS:
                # One line larger than a whole request: cut it, there is no
                # line boundary to honour.
                if current:
                    parts.append(''.join(current))
                    current, size = [], 0
                parts.append(line[:step])
                line = line[step:]
            cost = len(json.dumps(line))
            if current and size + cost > cls.CHUNK_CHARS:
                parts.append(''.join(current))
                current, size = [], 0
            current.append(line)
            size += cost
        if current:
            parts.append(''.join(current))
        return parts

    @staticmethod
    def _literals_as_text(fragment):
        """Everything downstream reads an assignment's literal as text.

        The model writes ``SET x = 1`` as the number 1, which the schema now
        accepts; the code generator searches the literal for addresses and
        credentials, so it is stored as the text the ESQL had.
        """
        for module in (fragment or {}).get('esql') or []:
            for assignment in (module or {}).get('assignments') or []:
                literal = assignment.get('literal')
                if literal is not None and not isinstance(literal, str):
                    assignment['literal'] = ('true' if literal is True else 'false' if literal is False
                                             else str(literal))

    def _write_report(self, context, path, result):
        """Render the analysis to ``<pipeline workspace>/reports/ace-analysis-report.html``.

        Same place and same rule as the variance reports: the analysis is already stored by the time this
        runs, so a failure here is reported in the step's metrics and never fails the step.
        """
        try:
            page = ace_analysis_report.build_for_analysis(self.repo, result['analysis_id'], path)
            pipeline = self.repo.one('select workspace_dir from pipelines where job_id=? order by created_at desc limit 1',
                                     (context.job_id,))
            base = (pipeline or {}).get('workspace_dir')
            folder = (str(base).rstrip('/\\') + '/reports') if base else 'reports/' + str(context.job_id)
            target = workspace.resolve(folder, must_exist=False, create=True) / ace_analysis_report.FILENAME
            target.write_text(page, encoding='utf-8')
            return {'report_path': target.relative_to(workspace.root()).as_posix()}
        except Exception as exc:
            return {'report_error': '%s:%s' % (type(exc).__name__, exc)}

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
                   'backends': model.get('backend_type', {}).get('backends', []),
                   # Every source file is model-read or the agent failed, so
                   # sources_from_model/sources_computed no longer describe
                   # anything: produce_many is all-or-nothing. What is left to
                   # record is how many files were read and how many requests
                   # the last of them took.
                   'sources_read': provenance.get('item_count', 0),
                   'model_attempts': provenance.get('attempts', 0)}
        # The HTML hand-over report, written once the analysis is stored. Its path (or its failure) is
        # recorded beside the other metrics; it is not an output, so the approved contract is unchanged.
        metrics.update(self._write_report(context, context.param('path'), result))
        clarifications = tuple(
            Clarification(stage='ACE_ANALYSIS', question=a.get('question', ''),
                          owner=a.get('owner', 'ACE_SME'), impact=a.get('impact', 'unassessed'),
                          evidence_ref=a.get('evidenceReference', '') or 'analysis://' + result['analysis_id'])
            for a in ambiguities if isinstance(a, dict) and a.get('status', 'OPEN') == 'OPEN')
        # The analysis itself is complete -- analysis_document and the semantic
        # model's hash are both below -- so in auto mode the ambiguities are
        # recorded as decisions rather than used to park the step. One real
        # application raised 95 of them, which stopped the migration before
        # anything downstream could run.
        #
        # The clarifications are still returned: dispatch persists them and, in
        # auto mode, answers each one through the model and attributes it to
        # AUTO_MODE. Nothing is discarded; what changes is that a reviewer reads
        # decisions instead of blocking questions.
        return AgentResult(
            status='CLARIFICATION_REQUIRED' if ambiguities and not automode.enabled() else 'COMPLETED',
            outputs={'analysis_document': result['analysis_id'],
                     'semantic_model': row['content_hash'],
                     'ambiguities': ambiguities},
            clarifications=clarifications,
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
