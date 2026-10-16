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
from pathlib import Path
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import ReasoningGateway
from app.services.migration_lifecycle_v6 import MappingAndPlanningService


class CodeGenerationAgent(BaseAgent):
    AGENT_ID = 'code-generation'
    SKILL = 'iib-to-ms-code-generation'
    TOOLS = ('repository.branch', 'repository.write', 'build.execute', 'evidence.write')
    OUTPUTS = ('source_code', 'tests', 'manifest', 'provenance')
    PROMPT = 'agent/code-generation/quality-failure-triage'

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
        manifest_path = Path(generated['manifest'])
        manifest = json.loads(manifest_path.read_text())
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
                     if isinstance(answer, dict) else []},
            reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(CodeGenerationAgent))
