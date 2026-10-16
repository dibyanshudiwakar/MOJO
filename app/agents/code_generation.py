"""Code Generation Agent.

Generates the target Spring project from an approved plan and runs the quality
gate over what it produced. Wraps ``MappingAndPlanningService.generate_spring``,
which refuses without an artefact-bound G3 approval of the plan, and
``quality_validate``, which is the compile/secret/endpoint/provenance gate whose
result the COMPLETED transition later requires.

The quality run belongs here rather than with the test engineering agent because
this contract is the one granted ``build.execute``; differential replay against a
running runtime is ``test.execute`` and ``runtime.invoke``, which this contract
does not hold.
"""
import json
import re
from pathlib import Path
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import ReasoningGateway
from app.agents.skills import SkillIntegrityError
from app.repository import digest
from app.services.feedback import MAX_EDITS, MAX_FILE_CHARS, accept_edit, redact
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

#: Business-logic synthesis bundles SKILL.md with structural-conventions.md
#: and the connector reference the detected backend type names -- the same
#: two references every backend type's own generation step is told to load
#: (SKILL.md Step 3.1), plus whichever one is specific to this service.
_CONNECTOR_REFERENCE = {
    'REST_EXTERNAL_DIRECT': 'rest-external-direct.md', 'SOAP': 'soap-backend.md',
    'QL_CICS': 'ql-cics-camel-bindy.md', 'RXCLAIM_SBO': 'rxclaim-sbo-xml-jaxb.md',
    'MQ_COBOL': 'ibm-mq-cobol-copybook.md',
}


def _extract_node_fragment(text, node_id):
    """The one ``<nodes ...>...</nodes>`` (or self-closing) XML element for
    ``node_id``, or ``''`` if not found.

    A flow/subflow's real source (captured whole, ACEParser._flow) can run
    tens of thousands of characters -- live-tested: truncating that to a flat
    character budget cut a 13KB subflow off at 6000 chars, before reaching the
    cited ComIbmWSRequest node's own attributes at offset 7409. The model
    correctly declined to implement it rather than guess, which is the
    designed behaviour, but it never had a chance to succeed. Extracting just
    the cited node's own element -- typically a few hundred characters --
    means truncation only has to apply to something that is already small.
    """
    m = re.search(r'<nodes\b[^>]*?xmi:id="' + re.escape(node_id) + r'"[^>]*?(?:/>|>.*?</nodes>)', text, re.S)
    return m.group(0) if m else ''


def _resolve_sources(model, source_refs):
    """Real source text for a set of task source_references.

    A ``source_reference`` is ``"<path>#<node_id>"`` for a flow/subflow node
    or bare ``"<path>"`` for an ESQL/map module. A node reference first tries
    to extract just that node's own fragment (see ``_extract_node_fragment``);
    when that is not possible (ESQL/map module refs have no such element, or
    the id was not found) it falls back to the whole file, deduped by path so
    several refs into the same file collapse to one copy.
    """
    by_path = {}
    for key in ('flows', 'subflows', 'esql', 'maps'):
        for item in model.get(key, []):
            by_path[item.get('path')] = item.get('source', '')
    seen_whole, seen_frag, out = set(), set(), []
    for ref in source_refs:
        path, _, node_id = ref.partition('#')
        text = by_path.get(path, '')
        if not text:
            continue
        if node_id:
            frag = _extract_node_fragment(text, node_id)
            if frag and ref not in seen_frag:
                seen_frag.add(ref)
                out.append((ref, frag))
                continue
        if path not in seen_whole:
            seen_whole.add(path)
            out.append((path, text))
    return out


class CodeGenerationAgent(BaseAgent):
    AGENT_ID = 'code-generation'
    SKILL = 'iib-to-ms-code-generation'
    TOOLS = ('repository.branch', 'repository.write', 'build.execute', 'evidence.write')
    OUTPUTS = ('source_code', 'tests', 'manifest', 'provenance')
    PROMPT = 'agent/code-generation/quality-failure-triage'
    BUSINESS_LOGIC_PROMPT = 'agent/code-generation/business-logic-synthesis'

    def _synthesize_business_logic(self, context, root, stub_sources, model):
        """Fill in a HAND_WRITTEN_PROTECTED stub from its real ACE source.

        Deliberately narrower than CodeFeedbackService's model-assisted
        repair: there is no failing check to measure against, so edits are
        applied directly rather than snapshotted and reverted -- the honest
        TODO stub Phase A wrote is itself a safe, always-valid fallback, so an
        edit that goes nowhere just leaves that stub in place.
        """
        if not stub_sources:
            return {'attempted': False, 'applied': [], 'refused': [], 'outcome': 'no_stubs', 'mode': ''}
        if context.provider is None:
            return {'attempted': False, 'applied': [], 'refused': [], 'outcome': 'no_provider_configured', 'mode': ''}
        root = Path(root)
        stub_files = []
        for rel in stub_sources:
            target = root / rel
            if target.is_file():
                stub_files.append({'path': rel, 'content': redact(target.read_text(errors='ignore'))[:MAX_FILE_CHARS]})
        real_source = {rel: [{'path': p, 'source': redact(text)[:MAX_FILE_CHARS]}
                             for p, text in _resolve_sources(model, refs)]
                      for rel, refs in stub_sources.items()}
        skill = None
        backend = (model.get('backend_type') or {}).get('type', '')
        connector_ref = _CONNECTOR_REFERENCE.get(backend)
        try:
            own = context.skill()
            if own is not None and connector_ref:
                skill = own.focus_many(['structural-conventions.md', connector_ref])
        except (AttributeError, KeyError, SkillIntegrityError):
            # Guidance, not authority -- a damaged/missing skill or reference
            # must not block synthesis; it just proceeds without that context.
            skill = None
        answer, provenance = ReasoningGateway(self.repo).reason(
            context, self.BUSINESS_LOGIC_PROMPT,
            {'stub_files': stub_files, 'real_source': real_source}, skill=skill)
        if not isinstance(answer, dict) or not answer.get('edits'):
            return {'attempted': bool(answer), 'applied': [], 'refused': [],
                    'outcome': 'no_edits_proposed', 'mode': provenance.get('mode', '')}
        applied, refused = [], []
        for edit in list(answer['edits'])[:MAX_EDITS]:
            target, why = accept_edit(root, edit)
            if target is None:
                refused.append(why)
                continue
            text = target.read_text(errors='ignore')
            target.write_text(text.replace(str(edit['old_str']), str(edit.get('new_str', '')), 1))
            applied.append({'path': str(target.relative_to(root)).replace('\\', '/'),
                            'why': str(edit.get('why', ''))[:200]})
        return {'attempted': True, 'applied': applied, 'refused': refused,
                'outcome': 'applied' if applied else 'all_edits_refused', 'mode': provenance.get('mode', '')}

    def execute(self, context):
        service = MappingAndPlanningService(self.repo)
        plan_id = context.param('plan_id')
        generated = service.generate_spring(
            context.job_id, plan_id,
            context.param('output_root'),
            context.param('service_name'),
            context.param('package_name'),
            # Optional, and read-only: the ESQL statements that become the
            # transformer bodies live in the source tree, not in the semantic
            # model (which records module names). Absent, the project is still
            # generated and its README says which transformers came through
            # without translated assignments.
            source_root=context.param('ace_path', '') or None)
        synthesis = self._synthesize_business_logic(
            context, generated['project_root'], generated.get('stub_sources', {}),
            generated.get('semantic_model', {}))
        manifest_path = Path(generated['manifest'])
        manifest = json.loads(manifest_path.read_text())
        if synthesis['applied']:
            # Synthesis edits files generate_spring already hashed and wrote
            # provenance for -- re-hash exactly those so the manifest's
            # per-file hash still matches what is actually on disk; nothing
            # else in the manifest (file set, other hashes) changes.
            touched = {a['path'] for a in synthesis['applied']}
            root = Path(generated['project_root'])
            for entry in manifest.get('files', []):
                if entry['path'].replace('\\', '/') in touched:
                    entry['hash'] = digest((root / entry['path']).read_text(errors='ignore'))
            manifest_path.write_text(json.dumps(manifest, indent=2))
            generated['content_hash'] = digest(json.dumps(manifest, sort_keys=True))
        quality = service.quality_validate(
            context.job_id, plan_id, generated['project_root'],
            bool(context.param('execute_build', False)))
        files = [x['path'] for x in manifest.get('files', [])]
        tests = [x for x in files if 'src/test/' in x.replace('\\', '/')]
        failed = [x['name'] for x in quality['checks'] if x['status'] == 'FAIL']
        # Only consulted when the gate already failed, and only about why: the
        # status below is ``quality['status']``, never the model's opinion of it.
        # The payload carries check names and counts, not generated source --
        # the project is on disk for a human to read.
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'failed_checks': [{'name': x['name'], 'detail': str(x.get('detail', ''))[:400]}
                              for x in quality['checks'] if x['status'] == 'FAIL'],
            'file_count': generated['file_count'], 'test_count': len(tests),
            'service_name': context.param('service_name')}) if failed else (None, {
                'mode': 'DETERMINISTIC_FALLBACK', 'prompt_name': self.PROMPT,
                'reason': 'quality_gate_passed_nothing_to_triage'})
        return AgentResult(
            status='FAILED' if quality['status'] == 'FAIL' else 'COMPLETED',
            error='quality_gate_failed:' + quality['quality_run_id'] if quality['status'] == 'FAIL' else '',
            outputs={'source_code': generated['project_root'], 'tests': tests,
                     'manifest': str(manifest_path), 'provenance': generated['content_hash']},
            # ``generate_spring`` hashes the manifest as sorted, compact json but
            # writes it indented, so the file's own bytes do not carry that hash.
            # Re-serialising canonically is what keeps the evidence hash equal to
            # the artefact hash an approval binds.
            evidence=(EvidenceRef('GENERATED_PROJECT', '1.0.0', 'generated://' + plan_id,
                                  json.dumps(manifest, sort_keys=True),
                                  {'plan_id': plan_id, 'file_count': generated['file_count'],
                                   'quality_run_id': quality['quality_run_id'],
                                   'quality_status': quality['status']}),),
            metrics={'file_count': generated['file_count'], 'test_count': len(tests),
                     'quality_status': quality['status'], 'failed_checks': failed,
                     # What the generator could not derive from the plan and the
                     # semantic model. Surfaced on the run so the output panel
                     # shows it beside the code, not only in the README.
                     'action_items': generated.get('action_items', []),
                     'model_failure_triage': (answer or {}).get('failures', [])
                     if isinstance(answer, dict) else [],
                     'business_logic_synthesis': synthesis['outcome'],
                     'business_logic_synthesized': [a['path'] for a in synthesis['applied']]},
            reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(CodeGenerationAgent))
