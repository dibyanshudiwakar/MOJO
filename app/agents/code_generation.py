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
import re
from pathlib import Path
from app.agents.artefact import AUTHORING_EFFORT, LISTING_EFFORT, ArtefactUnavailable, produce, produce_many
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import NOT_CONSULTED, ReasoningGateway
from app.agents.skills import SkillIntegrityError
from app.services import skill_gate, contract_reconcile, failure_reports, import_grounding, maven_settings, pom_properties, spring_versions
from app.services.migration_lifecycle_v6 import MappingAndPlanningService

#: The connector reference the detected backend type names, loaded alongside
#: structural-conventions.md -- the two references every backend type's own
#: generation step is told to load (SKILL.md Step 3.1).
_CONNECTOR_REFERENCE = {
    'REST_EXTERNAL_DIRECT': 'rest-external-direct.md', 'SOAP': 'soap-backend.md',
    'QL_CICS': 'ql-cics-camel-bindy.md', 'RXCLAIM_SBO': 'rxclaim-sbo-xml-jaxb.md',
    'MQ_COBOL': 'ibm-mq-cobol-copybook.md',
    # RECAP is IBM MQ with fixed-width buffers, which is what that reference
    # covers; the skill's own table has no RECAP reference of its own yet.
    'RECAP': 'ibm-mq-cobol-copybook.md',
}


def backends_of(model):
    """Every backend type the analysis found, primary first; ``[]`` when none was determined."""
    detected = model.get('backend_type') or {}
    listed = [b for b in (detected.get('backends') or []) if b]
    return listed or ([detected['type']] if detected.get('type') else [])


def _omission(item):
    """One omitted file as text: ``path: reason`` for the object form, as-is for a string."""
    if isinstance(item, dict):
        return '%s: %s' % (item.get('path', ''), item.get('reason') or 'no reason given')
    return str(item)


#: A TODO, FIXME or XXX marker left in generated code is an open decision that was not made, so the file is refused and
#: the model is asked again with the reason. The decision belongs in action_items, where a person can act on it.
OPEN_MARKER = re.compile(r'\b(TODO|FIXME|XXX)\b')


def no_open_markers(key, answer):
    """The correction for a file that carries an open-decision marker, or ``None`` for a clean file."""
    content = (answer or {}).get('content') or ''
    if OPEN_MARKER.search(content):
        return ('the file still contains a TODO/FIXME/XXX marker. Write the complete behaviour instead, using the '
                'skill and framework standards where the ACE source is silent, and record the assumption in '
                'action_items rather than in the code.')
    return None


def no_endpoint_literals(key, answer):
    """The correction for a Java file that hard-codes a service URL, or ``None``.

    The scanner (HARDCODED_ENDPOINT_SCAN) counts an absolute http(s) URL in Java unless it names a spec, a namespace or
    a parser feature. Such a URL belongs in the properties file under a key, and the code injects it with @Value.
    """
    if not str(key).endswith('.java'):
        return None
    content = (answer or {}).get('content') or ''
    for match in skill_gate.URL.finditer(skill_gate.strip_java_comments(content)):
        if not skill_gate.SPEC_URIS.match(match.group(0)):
            return ('the file hard-codes the service URL %s. Move its value to the configuration files under a property '
                    'key, and inject the key with @Value("${...}") into an instance field. A URL must not appear in Java '
                    'source, including as a static constant.' % match.group(0)[:120])
    return None


def authored_with_no_endpoints(context, prompt_name, items, **kwargs):
    """``produce_many`` for a generated file, refusing a hard-coded service URL in Java as well as an open marker."""
    def check(key, answer):
        return no_open_markers(key, answer) or no_endpoint_literals(key, answer)
    return produce_many(context, prompt_name, items, validator=check, **kwargs)




#: Path fragments of files other files compile against. Anything matching is written in the first wave.
CONTRACT_PATHS = ('/api/', '/model/', '/dto/', '/constant/', 'Api.java', 'Request.java', 'Response.java', 'Dto.java',
                  'Constants.java')


def is_contract_file(path):
    """True for a file the rest of the project depends on: every non-Java file, and the Java files that declare types."""
    text = str(path).replace(chr(92), '/')
    return (not text.endswith('.java')) or any(h in text for h in CONTRACT_PATHS)


def signatures_of(authored):
    """The real signatures of the Java types in ``{path: {'content': ...}}``, as the contract check reads them."""
    out = {}
    for path, answer in authored.items():
        if not str(path).endswith('.java'):
            continue
        text = (answer or {}).get('content') or ''
        for name, info in contract_reconcile._types_of(contract_reconcile._code(text)).items():
            out[name] = {'kind': info['kind'], 'supers': info['supers'],
                         'methods': sorted('%s %s(%s)' % (ret, method, ', '.join(params))
                                           for method, sigs in info['methods'].items() for ret, params in sigs)}
    return out


class CodeGenerationAgent(BaseAgent):
    AGENT_ID = 'code-generation'
    SKILL = 'iib-to-ms-code-generation'
    TOOLS = ('repository.branch', 'repository.write', 'build.execute', 'evidence.write')
    OUTPUTS = ('source_code', 'tests', 'manifest', 'provenance')
    PROMPT = 'agent/code-generation/quality-failure-triage'
    LAYOUT_PROMPT = 'agent/code-generation/project-layout'
    FILE_PROMPT = 'agent/code-generation/file-synthesis'

    #: Every per-file call is otherwise blind to its siblings (produce_many fixes
    #: each item's payload before the first call; no answer feeds another), which is
    #: how a controller came to believe a service exposed no method at all, and how
    #: six files came to each declare their own copy of the same constant. The layout
    #: call already decided the whole file list before any file was written, so handing
    #: that list to every file is free -- no new call, no template change (the whole
    #: payload is JSON-dumped as the user turn regardless of whether the template names
    #: a key, confirmed in CvscodeClaudeAdapter._request; this mirrors _precedents/
    #: _precedents_note in app/agents/artefact.py).
    PROJECT_FILES_NOTE = (
        "Every file this project will contain and what each is for, decided before any "
        "file was written. If your file calls another file's method, or another file's "
        "purpose says it owns a shared concern (constants, error messages, a config "
        "class), write against that declared purpose instead of inventing your own.")

    def _skill_for_backend(self, context, model):
        """The skill, narrowed to the structure plus this backend's connector."""
        # One reference per backend the service talks to: a service routing to
        # RxClaim and RECAP needs both, and loading only the first generated it
        # for one backend and left the other unimplemented (501).
        connectors = []
        for backend in backends_of(model):
            ref = _CONNECTOR_REFERENCE.get(backend)
            if ref and ref not in connectors:
                connectors.append(ref)
        try:
            own = context.skill()
            if own is None:
                return None
            wanted = ['structural-conventions.md', 'mandatory-migration-rules.md'] + connectors
            return own.focus_many(wanted)
        except (AttributeError, KeyError, SkillIntegrityError):
            return None

    def _framework_root(self, context):
        """The job's cloned FRAMEWORK repository, whose parent pom sets the Spring line, or ``None`` when none was supplied."""
        row = self.repo.one("select local_path from job_repositories where job_id=? and role='FRAMEWORK' "
                            "and enabled=1 and local_path<>''", (context.job_id,))
        return row['local_path'] if row else None

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
             'backends': backends_of(model),
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
        # settings.xml is copied from the skill's template, never authored: it names the internal
        # Maven repositories and a retyped copy that drops one breaks every build.
        wanted = [item for item in layout['files'] if item['path'] != maven_settings.NAME]
        # The layout call already decided every file's path and purpose before any file
        # was written -- hand that whole list to every file, so each one can write against
        # a sibling's declared intent instead of inventing its own guess about it.
        project_files = [{'path': item['path'], 'purpose': item.get('purpose', '')} for item in layout['files']]
        def request(item, signatures):
            payload = {'path': item['path'], 'purpose': item.get('purpose', ''),
                       'source_reference': item.get('source_reference', ''),
                       'skill_reference': item.get('skill_reference', ''),
                       'service_name': context.param('service_name'),
                       'package_name': context.param('package_name'),
                       'tasks': tasks,
                       'semantic_model': model, 'framework_standards': standards,
                       'project_files': project_files, 'project_files_note': self.PROJECT_FILES_NOTE}
            if signatures:
                payload['project_contracts'] = signatures
            return (item['path'], payload)

        # Two parallel waves. The files other files compile against (interfaces, models, DTOs, constants, configuration)
        # are written first; the consumers are written second, each with the real signatures wave one produced. Both
        # waves keep their requests in parallel, so the cost is one extra wave boundary, and what it buys is that a
        # consumer no longer has to guess a method name the contract already fixed.
        first = [i for i in wanted if is_contract_file(i['path'])]
        second = [i for i in wanted if not is_contract_file(i['path'])]
        authored, file_provenance, attempts = {}, {}, 0
        if first:
            found, provenance = authored_with_no_endpoints(context, self.FILE_PROMPT, [request(i, None) for i in first],
                                             skill=skill, effort=AUTHORING_EFFORT)
            authored.update(found)
            file_provenance, attempts = provenance, attempts + int(provenance.get('attempts_total') or 0)
        if second:
            found, provenance = authored_with_no_endpoints(context, self.FILE_PROMPT,
                                             [request(i, signatures_of(authored)) for i in second],
                                             skill=skill, effort=AUTHORING_EFFORT)
            authored.update(found)
            file_provenance, attempts = provenance, attempts + int(provenance.get('attempts_total') or 0)
        file_provenance = dict(file_provenance, waves=int(bool(first)) + int(bool(second)), attempts_total=attempts)
        # No emptiness filter: 'content' carries minLength 1, so a blank file is
        # a violation on that one file and is re-asked. Filtering here meant one
        # blank response discarded every other file the model had authored.
        files = [(path, answer['content']) for path, answer in authored.items()]
        actions = ['omitted by the model: ' + _omission(x) for x in (layout.get('omitted') or [])]
        files, pom_actions = pom_properties.complete(files)
        actions += pom_actions
        files, version_actions = spring_versions.strip_spring_versions(files)
        actions += version_actions
        files, spring_actions = pom_properties.pin_spring_line(
            files, import_grounding.m2_root(), framework_root=(self._framework_root(context)))
        actions += spring_actions
        files, unmanaged_actions = pom_properties.pin_unmanaged_versions(files, import_grounding.m2_root())
        actions += unmanaged_actions
        files, import_actions = import_grounding.ground(files, import_grounding.m2_root())
        actions += import_actions
        files, class_actions = self._complete_missing_classes(
            context, files, tasks, model, standards, skill, project_files)
        actions += class_actions
        files, breaker_actions = self._complete_circuit_breaker(
            context, files, tasks, model, standards, skill, project_files)
        actions += breaker_actions
        # Last, so it sees the files as they will be written, including anything revised above.
        files, contract_actions = contract_reconcile.reconcile(
            context, files, import_grounding.m2_root(), authored_with_no_endpoints, self.FILE_PROMPT, AUTHORING_EFFORT, skill,
            {'service_name': context.param('service_name'), 'package_name': context.param('package_name'),
             'tasks': tasks, 'semantic_model': model, 'framework_standards': standards,
             'project_files': project_files, 'project_files_note': self.PROJECT_FILES_NOTE})
        actions += contract_actions
        settings = maven_settings.template()
        if settings is not None:
            files.append((maven_settings.NAME, settings))
        else:
            actions.append('settings.xml was not written: the code-generation skill could not be read. '
                           'Without it Maven cannot resolve the framework parent POM.')
        recorded = service.record_generated_project(
            context.job_id, plan_id, context.param('output_root'),
            layout.get('root') or '', files, action_items=actions)
        file_provenance['layout_root'] = layout.get('root') or ''
        file_provenance['file_count'] = len(files)
        return recorded, file_provenance

    #: [M18] circuit-breaker properties are dead config without a Java branch that reads the open breaker.
    BREAKER_PROPERTY = 'application.circuitbreaker.'
    BREAKER_BRANCH = 'isCircuitBreakerError'
    BREAKER_CHANGE = (
        'Configuration in this project sets application.circuitbreaker.* properties, so this class must '
        'handle an open circuit breaker: add an isCircuitBreakerError() branch in the catch block around the '
        'backend call that returns the timeout (408) response, exactly as rest-external-direct.md '
        '"Circuit breaker - the Java-side consumption pattern" and common-patterns-and-gotchas.md section 2 '
        'show. Keep everything else in the file unchanged.')

    def _complete_circuit_breaker(self, context, files, tasks, model, standards, skill, project_files):
        """Author the missing ``isCircuitBreakerError()`` branch, once, when the properties need it.

        The model writes the config-map properties and the service class in separate calls, and often
        carries the breaker properties without the Java side. The gate reports that as skill gap M18.
        Catching it here means the project leaves generation without the gap, not with a note for the
        feedback agent. One re-authoring of the service class; whatever it returns is then re-checked,
        and a gap that survives is left for the gate to report as it always was.
        """
        def configured():
            return any(self.BREAKER_PROPERTY in c for p, c in files
                       if p.endswith(('.properties', '.yml', '.yaml')))

        def handled():
            return any(self.BREAKER_BRANCH in c for p, c in files if p.endswith('.java'))

        if not configured() or handled():
            return files, []
        targets = [(p, c) for p, c in files
                   if p.endswith('Service.java') and 'src/main/java/' in p.replace(chr(92), '/')]
        if not targets:
            return files, []
        try:
            authored, _prov = self._revise(context, targets, tasks, model, standards, skill, project_files)
        except ArtefactUnavailable as exc:
            # Optional polish on a project that is otherwise complete: it must not fail the agent.
            return files, ['could not add the isCircuitBreakerError() branch (%s); skill gap M18 stays open.' % exc]
        revised = {p: a['content'] for p, a in authored.items() if self.BREAKER_BRANCH in a.get('content', '')}
        if not revised:
            return files, ['circuit-breaker properties are configured but the service class was not given an '
                           'isCircuitBreakerError() branch (skill gap M18); add it or remove the properties.']
        return ([(p, revised.get(p, c)) for p, c in files],
                ['added the isCircuitBreakerError() branch to %s (skill gap M18)' % ', '.join(sorted(revised))])

    def _complete_missing_classes(self, context, files, tasks, model, standards, skill, project_files):
        """Write each project class the generated code imports and nothing defines.

        The model imports its own request model and never writes it (the layout left it out), so the build
        fails on "cannot find symbol" for a file that is otherwise complete. Only classes in a package the
        project already declares are written; an import of any other package is left for the gate to report,
        because writing it would invent a framework class. The importing file is shown to the model, so the
        new class carries the members it is used for. A failed authoring is reported, not raised.
        """
        needed = import_grounding.missing(files, import_grounding.m2_root())
        if not needed:
            return files, []
        by_path = dict(files)
        requests, seen = [], set()
        for consumer, fqn in needed:
            if fqn in seen:
                continue
            seen.add(fqn)
            module = consumer.replace(chr(92), '/').split('/src/main/java/')[0]
            target = module + '/src/main/java/' + fqn.replace('.', '/') + '.java'
            requests.append((target, {'path': target,
                                      'purpose': 'A class the generated code imports and nothing defines: ' + fqn,
                                      'consumer_source': by_path.get(consumer, ''),
                                      'consumer_path': consumer,
                                      'service_name': context.param('service_name'),
                                      'package_name': context.param('package_name'),
                                      'tasks': tasks, 'semantic_model': model, 'framework_standards': standards,
                                      'project_files': project_files, 'project_files_note': self.PROJECT_FILES_NOTE}))
        try:
            authored, _prov = authored_with_no_endpoints(context, self.FILE_PROMPT, requests, skill=skill, effort=AUTHORING_EFFORT)
        except ArtefactUnavailable as exc:
            return files, ['could not write %d class(es) the project imports (%s); the build will fail on them.'
                           % (len(requests), exc)]
        added = [(path, answer['content']) for path, answer in authored.items() if answer.get('content')]
        return files + added, ['wrote %s, which the project imports and did not define' % path for path, _ in added]

    def _revise(self, context, targets, tasks, model, standards, skill, project_files):
        return authored_with_no_endpoints(
            context, self.FILE_PROMPT,
            [(p, {'path': p, 'purpose': 'Revision of this class: ' + self.BREAKER_CHANGE,
                  'current_content': c, 'required_change': self.BREAKER_CHANGE,
                  'skill_reference': 'references/gap-checklist.md [M18]',
                  'service_name': context.param('service_name'),
                  'package_name': context.param('package_name'),
                  'tasks': tasks, 'semantic_model': model, 'framework_standards': standards,
                  'project_files': project_files, 'project_files_note': self.PROJECT_FILES_NOTE})
             for p, c in targets],
            skill=skill, effort=AUTHORING_EFFORT)

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
            context.param('execute_build', 'auto'))
        files = [x['path'] for x in manifest.get('files', [])]
        tests = [x for x in files if 'src/test/' in x.replace('\\', '/')]
        failed = [x['name'] for x in quality['checks'] if x['status'] == 'FAIL']
        # Only consulted when the gate already failed, and only about why: the
        # status below is ``quality['status']``, never the model's opinion of it.
        # The payload carries check names and counts, not generated source --
        # the project is on disk for a human to read.
        answer, triage = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'failed_checks': [{'name': x['name'], 'detail': str(x.get('detail') or x.get('evidence') or '')[:400]}
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
        gate_failed = quality['status'] == 'FAIL'
        quality_report = failure_reports.record_quality(self.repo, context.job_id, quality['quality_run_id']) if gate_failed else None
        # The skill's non-blocking findings, named. HANDOFF-B says to print the
        # residual open items as GAPs with a remediation path rather than refuse
        # the hand-off, so they travel on the run for a reviewer to read.
        gaps = [{'check': x['name'], 'skill_ref': x.get('skill_ref', ''), 'findings': x.get('evidence', [])[:5],
                 'remediation': x.get('remediation', '')}
                for x in quality['checks'] if x['status'] == 'GAP']
        if gaps and context.logger:
            context.logger.warn('%d skill GAP(s) open: %s' % (len(gaps), ', '.join(g['check'] for g in gaps)))
        if gate_failed and context.logger:
            context.logger.warn('Quality gate FAILED (%s) — the project is generated and handed to the '
                                'feedback agent to repair' % ', '.join(failed))
            if quality_report:
                context.logger.info('Quality gate report: %s' % quality_report['html_url'])
        return AgentResult(
            # Generation is complete either way; the gate's verdict is recorded
            # beside it (``quality_status``, the quality run, the evidence) and is
            # not softened. This used to return FAILED, which the pipeline worker
            # did not read -- so the step showed COMPLETED while the run was
            # FAILED, and the feedback agent, whose whole job is to repair a
            # project the gate has flagged, was refused for want of a completed
            # code-generation run. ``repair_by`` tells the worker to fail the step
            # if nothing in the pipeline is going to do that.
            status='COMPLETED',
            error='',
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
                     'gap_count': len(gaps), 'gaps': gaps,
                     'quality_run_id': quality['quality_run_id'],
                     'quality_report': (quality_report or {}).get('html_url', ''),
                     'repair_by': 'feedback' if gate_failed else '',
                     # What the model said it left out, surfaced on the run so
                     # the output panel shows it beside the code.
                     'action_items': generated.get('action_items', []),
                     'model_failure_triage': (answer or {}).get('failures', [])
                     if isinstance(answer, dict) else []},
            reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(CodeGenerationAgent))
