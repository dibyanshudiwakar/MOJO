"""Code Generation Agent.

Authors the target Spring project from an approved plan, entirely through the
model and its skill, then runs the quality gate over what it produced.
``generation_inputs`` still refuses without an artefact-bound G3 approval of the
plan, and ``quality_validate`` is still the compile/secret/endpoint/provenance
gate whose result the COMPLETED transition requires.

``springgen``/``generate_spring`` is no longer reachable from here. It was the
fallback, and it was never able to satisfy the skill: its layout is hardcoded
plain Spring Boot, so a skill requiring parent and web Maven modules under an
``EIPMS_`` root could not be honoured, and it emits an ``exception`` package
Rule 10 forbids. It remains in ``app/services`` behind ``POST /api/v6/*`` for
callers that want a deterministic generation, but a project this agent produces
is model-authored or does not exist.

Two capabilities went with it, both of which only ever operated on its output:
business-logic synthesis (filling in ``springgen``'s TODO transformer bodies
from the real ESQL) and the stub reporting that fed it. A model-authored project
has no stubs to fill, so there is no input left for that pass. If it is wanted
again it needs re-pointing at authored files, which is a new feature rather
than something this change could preserve.

The quality run belongs here rather than with the test engineering agent because
this contract is the one granted ``build.execute``; differential replay against a
running runtime is ``test.execute`` and ``runtime.invoke``, which this contract
does not hold.
"""
import json
from pathlib import Path
from app.agents.artefact import LISTING_EFFORT, produce, produce_many
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import NOT_CONSULTED, ReasoningGateway
from app.agents.skills import SkillIntegrityError
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

#: The connector reference the detected backend type names, loaded alongside
#: structural-conventions.md -- the two references every backend type's own
#: generation step is told to load (SKILL.md Step 3.1).
_CONNECTOR_REFERENCE = {
    'REST_EXTERNAL_DIRECT': 'rest-external-direct.md', 'SOAP': 'soap-backend.md',
    'QL_CICS': 'ql-cics-camel-bindy.md', 'RXCLAIM_SBO': 'rxclaim-sbo-xml-jaxb.md',
    'MQ_COBOL': 'ibm-mq-cobol-copybook.md',
}


class CodeGenerationAgent(BaseAgent):
    AGENT_ID = 'code-generation'
    SKILL = 'iib-to-ms-code-generation'
    TOOLS = ('repository.branch', 'repository.write', 'build.execute', 'evidence.write')
    OUTPUTS = ('source_code', 'tests', 'manifest', 'provenance')
    PROMPT = 'agent/code-generation/quality-failure-triage'
    LAYOUT_PROMPT = 'agent/code-generation/project-layout'
    FILE_PROMPT = 'agent/code-generation/file-synthesis'

    def _skill_for_backend(self, context, model):
        """The skill, narrowed to the structure plus this backend's connector."""
        connector = _CONNECTOR_REFERENCE.get((model.get('backend_type') or {}).get('type', ''))
        try:
            own = context.skill()
            if own is None:
                return None
            wanted = ['structural-conventions.md', 'mandatory-migration-rules.md']
            if connector:
                wanted.append(connector)
            return own.focus_many(wanted)
        except (AttributeError, KeyError, SkillIntegrityError):
            return None

    def _author_project(self, context, service, plan_id):
        """The project, authored by the model from the skill. The only path.

        The skill is the authority on structure, so the layout is asked for
        first: which files this repository contains, and what its root
        directory is called. ``springgen`` could never answer that -- its layout
        is hardcoded plain Spring Boot, so a skill requiring parent and web
        Maven modules under an ``EIPMS_`` root could not be honoured, and it
        generates an ``exception`` package that Rule 10 forbids outright. That
        is why it is no longer behind this: a fallback that cannot satisfy the
        standard is not a fallback, it is a different product.

        All-or-nothing, and now that means failing. A project missing its parent
        POM does not build, so ``produce_many`` raising on any single file is
        the correct outcome rather than a partial tree or a substituted one.
        """
        plan, analysis, framework, specification = service.generation_inputs(
            context.job_id, plan_id)
        model = json.loads(analysis['semantic_model'])
        tasks = json.loads(plan['tasks'] or '[]')
        if not tasks:
            # Not asked, and not generated either. Asking spent 2m47s of model
            # time and then failed the layout schema, because 'files' requires
            # at least one entry and the honest answer to "list the files this
            # repository needs" is that there are none -- the model was right
            # and the question should not have been put. With no generator
            # behind this any more there is nothing to produce, and a plan with
            # no tasks reaching code generation is a pipeline fault upstream.
            raise ValueError('plan_has_no_tasks_to_generate:' + plan_id)
        skill = self._skill_for_backend(context, model)
        standards = json.loads(framework['standards'] or '{}') if framework else {}
        layout, provenance = produce(
            context, self.LAYOUT_PROMPT,
            {'service_name': context.param('service_name'),
             'package_name': context.param('package_name'),
             'backend_type': (model.get('backend_type') or {}).get('type', ''),
             'tasks': tasks,
             'semantic_model': model, 'framework_standards': standards},
            skill=skill,
            # A file list, not a design problem. At the coding alias's xhigh
            # effort the hidden reasoning alone consumed the 32,768-token budget
            # for a 15,096-character layout.
            effort=LISTING_EFFORT)
        # The schema requires minItems 1 on 'files', so an empty layout is a
        # retryable violation inside produce rather than something to check for
        # here -- by the time this returns there is at least one file.
        wanted = layout['files']
        authored, file_provenance = produce_many(
            context, self.FILE_PROMPT,
            [(item['path'], {'path': item['path'], 'purpose': item.get('purpose', ''),
                             'source_reference': item.get('source_reference', ''),
                             'skill_reference': item.get('skill_reference', ''),
                             'service_name': context.param('service_name'),
                             'package_name': context.param('package_name'),
                             'tasks': tasks,
                             'semantic_model': model, 'framework_standards': standards})
             for item in wanted],
            skill=skill)
        # No emptiness filter: 'content' carries minLength 1, so a blank file is
        # a violation on that one file and is re-asked. Filtering here meant one
        # blank response discarded every other file the model had authored.
        files = [(path, answer['content']) for path, answer in authored.items()]
        actions = ['omitted by the model: ' + str(x) for x in (layout.get('omitted') or [])]
        recorded = service.record_generated_project(
            context.job_id, plan_id, context.param('output_root'),
            layout.get('root') or '', files, action_items=actions)
        file_provenance['layout_root'] = layout.get('root') or ''
        file_provenance['file_count'] = len(files)
        return recorded, file_provenance

    def execute(self, context):
        service = MappingAndPlanningService(self.repo)
        plan_id = context.param('plan_id')
        generated, authoring = self._author_project(context, service, plan_id)
        return self._result(context, service, plan_id, generated, authoring)

    def _result(self, context, service, plan_id, generated, authoring):
        """The quality gate and the result.

        The gate is the independent verdict on the code and runs over whatever
        was written, unchanged by this agent becoming model-only: if the model
        both wrote the code and graded it, the gate would be theatre.
        """
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
        answer, triage = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'failed_checks': [{'name': x['name'], 'detail': str(x.get('detail', ''))[:400]}
                              for x in quality['checks'] if x['status'] == 'FAIL'],
            'file_count': generated['file_count'], 'test_count': len(tests),
            'service_name': context.param('service_name')}) if failed else (None, {
                # A passing gate has nothing to explain, so the model is not
                # asked. Unchanged in substance -- only the mode, which used to
                # claim a fallback had happened.
                'mode': NOT_CONSULTED, 'prompt_name': self.PROMPT,
                'reason': 'quality_gate_passed_nothing_to_triage'})
        # The run's provenance is the *authoring* call's, with the triage nested
        # under it. It used to be the triage's, which was wrong in a way that
        # only became visible once the fallback went: on a passing gate the
        # triage is never made, so a wholly model-authored project recorded
        # mode=MODEL_NOT_CONSULTED on agent_runs.reasoning -- the opposite of
        # what happened. Authoring is the model work this agent exists to do.
        provenance = dict(authoring)
        provenance['quality_triage'] = {k: triage.get(k) for k in ('mode', 'reason')}
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
                     # 'authored_by' is gone: there is one author now, and a
                     # metric that always reads MODEL tells a reviewer nothing.
                     # How many requests the file set took does.
                     'model_attempts': authoring.get('attempts', 0),
                     'layout_root': authoring.get('layout_root', ''),
                     'quality_status': quality['status'], 'failed_checks': failed,
                     # What the model said it left out, surfaced on the run so
                     # the output panel shows it beside the code.
                     'action_items': generated.get('action_items', []),
                     'model_failure_triage': (answer or {}).get('failures', [])
                     if isinstance(answer, dict) else []},
            reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(CodeGenerationAgent))
