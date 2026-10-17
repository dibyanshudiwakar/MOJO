"""A deterministic stand-in for Claude that answers each prompt properly.

With the fallback gone, an agent cannot complete without a provider: every
``produce`` call either returns a schema-valid artefact or raises
``ArtefactUnavailable``. So every agent test needs a provider, and a double
returning ``{'note': 'deterministic test answer'}`` would simply make all of
them fail their response schema -- which tests the schema, not the agent.

This answers per prompt, building each artefact from the payload the agent
actually sent. Where a correct answer is well defined, it is built with the
*same reference code the deterministic path used to use*: ``ACEParser.fold``
for a source file, ``_scan_java``/``_scan_config`` for framework standards,
``DEFAULT_MAPPINGS``/``mapping_key`` for the mapping catalogue.

That reuse is deliberate and is worth being explicit about, because it looks at
first glance like the fallback coming back in through the test suite. The
difference is where it sits: no agent can reach that code any more, which is the
whole point of the change, but it remains the best available description of what
a competent answer looks like. Using it here is what keeps assertions about
artefact *content* meaningful -- a double returning hand-written stubs would
reduce every agent test to "the agent passed the fixture's answer through".

Prompts whose answer is a judgement rather than a derivation (gate assessment,
materiality, root-cause hypotheses) get a minimal schema-valid answer instead,
because there is no reference implementation of a judgement and inventing one
here would assert nothing.
"""
import json
from pathlib import Path

from app.services.enterprise_v5 import ACEParser, FrameworkConventionExtractor
from app.services.factory_v3 import DormancyService, IntakeService
from app.services.migration_lifecycle_v6 import (
    DEFAULT_MAPPINGS, is_presentation_only, mapping_key)

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE_PROMPTS = json.loads(
    (ROOT / 'config' / 'agent-prompts.v1.json').read_text(encoding='utf-8'))['prompts']

#: template -> prompt name. ``ProviderExecutionAdapter.invoke`` receives the
#: approved *template*, never the prompt name, so this is the only way a double
#: can tell what it is being asked for. Unique across the catalogue;
#: ``test_the_prompt_catalogue_templates_stay_unique`` pins that, because two
#: prompts sharing a template would silently route to one answer here.
PROMPT_BY_TEMPLATE = {p['template']: p['name'] for p in CATALOGUE_PROMPTS}


def _inventory(payload):
    """Classify the paths the agent sent, by extension.

    ``record_manifest`` rebuilds ``files`` from the real listing with the real
    hashes regardless of what comes back -- the file set is a fact about the
    repository, not an answer -- so only the per-area classification matters.
    """
    areas = {k: [] for k in set(IntakeService.EXT.values())}
    for item in payload.get('files') or []:
        path = str(item.get('path', ''))
        suffix = ('.' + path.rsplit('.', 1)[-1]).lower() if '.' in path else ''
        if suffix in IntakeService.EXT:
            areas[IntakeService.EXT[suffix]].append(path)
    return {'inventory': areas,
            'gaps': [] if areas['flows'] else ['no_message_flows_detected']}


def _semantic_fragment(payload):
    """One source file, parsed. ``produce_many`` sends one request per file."""
    parser = ACEParser()
    fragment = parser.empty()
    parser.fold(fragment, payload.get('path', ''), payload.get('text', ''),
                payload.get('kind', ''))
    return fragment


def _standards(payload):
    """Framework standards from the sampled files.

    ``extract`` walks a directory; the agent sends the files it sampled, so the
    per-file scanners are applied to those instead. ``assemble`` is still done
    by the agent -- the template markdown is what a G1 approval binds.
    """
    extractor = FrameworkConventionExtractor()
    rules = {'build': {}, 'dependencies': [], 'projectStructure': [], 'api': [],
             'messaging': [], 'security': [], 'configuration': [], 'logging': [],
             'errorHandling': [], 'observability': []}
    conflicts = []
    files = {str(x.get('path', '')): str(x.get('text', '')) for x in payload.get('files') or []}
    pom = files.get('pom.xml')
    if pom is not None:
        import xml.etree.ElementTree as ET
        try:
            x = ET.fromstring(pom)
            rules['build'] = {'tool': 'MAVEN',
                              'javaVersion': extractor._xml_text(x, 'java.version'),
                              'springBootVersion': extractor._parent_version(x)}
            rules['dependencies'] = sorted(
                {extractor._xml_text(d, 'artifactId') for d in x.iter()
                 if d.tag.split('}')[-1] == 'dependency'} - {None})
        except Exception:
            conflicts.append('invalid_pom_xml')
    else:
        conflicts.append('missing_supported_build_file')
    for rel, text in files.items():
        if 'src' in rel.replace('\\', '/').split('/'):
            rules['projectStructure'].append(rel)
        extractor._scan_java(rules, rel, text)
        extractor._scan_config(rules, rel, text)
    if not rules['security']:
        conflicts.append('security_standard_not_detected')
    if not rules['observability']:
        conflicts.append('observability_standard_not_detected')
    return {'standards': rules, 'conflicts': sorted(set(conflicts))}


def _mapping_catalogue(payload):
    """Construct-to-Spring mappings over the semantic model the agent sent.

    ``record_mapping_catalogue`` stamps the framework, analysis and
    specification hashes itself, so they are deliberately absent here -- a
    model cannot produce a SHA-256 of bytes it was never given.
    """
    model = payload.get('semantic_model') or {}
    mappings, unsupported, risks = [], [], []

    def add(source, name, properties=None):
        key = mapping_key(name, DEFAULT_MAPPINGS, properties)
        if key:
            target = DEFAULT_MAPPINGS[key]
            mappings.append({'source_reference': source, 'ace_construct': name,
                             'mapping_key': key, 'spring_pattern': target['pattern'],
                             'target_module': target['module'],
                             'boundary': 'GENERATED' if target['generated']
                             else 'HAND_WRITTEN_PROTECTED',
                             'tests': target['tests']})
        else:
            unsupported.append({'source_reference': source,
                                'ace_construct': name or 'UNKNOWN',
                                'reason': 'NO_APPROVED_SPRING_PATTERN',
                                'status': 'OPEN', 'owner': 'ACE_SME_AND_JAVA_ARCHITECT'})
            risks.append({'category': 'UNSUPPORTED_MAPPING', 'severity': 'HIGH',
                          'source_reference': source,
                          'statement': 'No approved Spring mapping for ' + str(name),
                          'response': 'Clarify or implement approved plug-in mapping',
                          'owner': 'ACE_SME_AND_JAVA_ARCHITECT', 'status': 'OPEN'})

    for flow in (model.get('flows') or []) + (model.get('subflows') or []):
        for node in flow.get('nodes') or []:
            ntype = node.get('type', 'UNKNOWN')
            if is_presentation_only(ntype):
                continue
            add(str(flow.get('path', '')) + '#' + str(node.get('id', '')), ntype,
                node.get('properties') or {})
    for x in model.get('esql') or []:
        add(x.get('path', ''), 'ESQL')
    for x in model.get('maps') or []:
        add(x.get('path', ''), x.get('type', 'Mapping'))
    for x in model.get('schemas') or []:
        add(x.get('path', ''), 'SCHEMA')
    for x in model.get('policies') or []:
        add(x.get('path', ''), 'POLICY')
    return {'mappings': mappings, 'unsupported': unsupported, 'risks': risks}


def _paths(items):
    return sorted({x.get('path') for x in items or []
                   if isinstance(x, dict) and x.get('path')})


def _specification(payload):
    """The migration specification document.

    Every fact that is not in the semantic model now arrives in the payload --
    ``metadata``, ``governance``, ``approved_dependencies``, ``clarifications``
    -- which is what makes authoring it possible at all. This composes them the
    way the agent's own deleted composer did, so
    ``AnalysisSpecificationConsistencyValidator`` still has a real document to
    pass or fail on.
    """
    model = payload.get('semantic_model') or {}
    nodes = sorted({n.get('type', '') for f in (model.get('flows') or [])
                    + (model.get('subflows') or [])
                    for n in f.get('nodes') or [] if n.get('type')})
    constructs = [n.get('type', '') for f in (model.get('flows') or [])
                  + (model.get('subflows') or []) for n in f.get('nodes') or []]
    constructs += ['ESQL'] * len(model.get('esql') or [])
    constructs += [x.get('type', 'Mapping') for x in model.get('maps') or []]
    constructs += ['SCHEMA'] * len(model.get('schemas') or [])
    constructs += ['POLICY'] * len(model.get('policies') or [])
    keys = {mapping_key(x, DEFAULT_MAPPINGS) for x in constructs}
    modules = sorted({DEFAULT_MAPPINGS[k]['module'] for k in keys if k})
    service = (payload.get('metadata') or {}).get('service', '')
    return {'specification': {
        'metadata': payload.get('metadata') or {},
        'source': {'aceVersion': str(payload.get('ace_version', '12')),
                   'applications': [service] if service else [],
                   'libraries': [], 'flows': _paths(model.get('flows')),
                   'subflows': _paths(model.get('subflows')), 'nodes': nodes,
                   'esql': _paths(model.get('esql')), 'maps': _paths(model.get('maps')),
                   'schemas': _paths(model.get('schemas')),
                   'policies': _paths(model.get('policies'))},
        'interfaces': model.get('interfaces') or [],
        'behaviour': model.get('behaviour') or {},
        'target': {'springModules': modules,
                   'approvedDependencies': payload.get('approved_dependencies') or []},
        # The planning agent owns construct-level mappings, under G2 approval of
        # this document. Naming them here would pre-empt a decision this
        # document is an input to.
        'mappings': [],
        'tests': {'inputSources': sorted({x.get('protocol', '')
                                          for x in model.get('interfaces') or []}),
                  'invariants': [], 'masks': [], 'tolerances': []},
        'clarifications': payload.get('clarifications') or [],
        'governance': payload.get('governance') or {}}}


def _project_layout(payload):
    """A coherent repository: a POM, an application class, and a test per task.

    Deliberately not the thinnest thing the schema permits. A skill-guided model
    is expected to produce a project that builds and a test beside each
    generated flow, and several assertions depend on that -- a generated flow
    with no test, or no ``Application`` class, is a project nobody can run. A
    double that emitted only what ``minItems`` demanded would make those
    assertions pass or fail on the fixture rather than on the agent.
    """
    service = str(payload.get('service_name') or 'service')
    package = str(payload.get('package_name') or 'com.example')
    directory = 'src/main/java/' + package.replace('.', '/')
    test_directory = 'src/test/java/' + package.replace('.', '/')
    files = [{'path': 'pom.xml', 'purpose': 'Maven build'},
             {'path': directory + '/Application.java',
              'purpose': 'Spring Boot entry point'}]
    for index, task in enumerate(payload.get('tasks') or [], 1):
        files.append({'path': '%s/Generated%d.java' % (directory, index),
                      'purpose': str(task.get('spring_pattern', '')),
                      'source_reference': str(task.get('source_reference', ''))})
        files.append({'path': '%s/Generated%dTest.java' % (test_directory, index),
                      'purpose': 'differential test for ' + str(task.get('spring_pattern', '')),
                      'source_reference': str(task.get('source_reference', ''))})
    return {'root': service + '-parent', 'files': files, 'omitted': []}


def _file_synthesis(payload):
    """One file. ``content`` carries minLength 1, so it is never blank."""
    path = str(payload.get('path') or 'pom.xml')
    if path.endswith('.xml'):
        body = ('<project><modelVersion>4.0.0</modelVersion>'
                '<artifactId>%s</artifactId></project>' % (payload.get('service_name') or 'app'))
    else:
        package = str(payload.get('package_name') or 'com.example')
        name = path.rsplit('/', 1)[-1].rsplit('.', 1)[0]
        body = 'package %s;\n\npublic class %s {\n}\n' % (package, name)
    return {'path': path, 'content': body,
            'provenance': {'source_reference': str(payload.get('source_reference') or '')}}


def _dormancy(payload):
    """``DormancyService.classify`` itself -- the rules, not a paraphrase.

    The agent records the model's verdict and keeps the rules' verdict beside it
    under ``agrees_with_rules``. A double that classified by its own logic would
    disagree by accident, so every dormancy test would be asserting a
    disagreement no real model expressed. Calling the rules means agreement is
    the default and a test that wants a disagreement passes its own provider.

    ``confidence`` is deliberately left out of the returned verdict: the agent
    measures evidence coverage itself and must never take a model's figure, so
    supplying one here would let that regression pass unnoticed.
    """
    # repo is unused by classify, which is why it can be built with None.
    verdict = DormancyService(None).classify({
        'traffic_count': payload.get('traffic_count'),
        'last_seen_days': payload.get('last_seen_days'),
        'deployed': payload.get('deployed'),
        'already_migrated': payload.get('already_migrated')})
    return {'classification': verdict['classification'],
            'decisive_evidence': 'the supplied traffic and deployment signals',
            'rationale': verdict['rationale'],
            'missing_evidence': sorted(k for k in
                                       ('traffic_count', 'last_seen_days', 'deployed',
                                        'already_migrated') if payload.get(k) is None)}


def _gate_assessment(payload):
    """A judgement, so it agrees with the evaluation rather than inventing one.

    The agent records ``disagrees`` only on a straight contradiction, and a
    double that contradicted every gate would make every governance test assert
    a disagreement that no real model expressed.
    """
    evaluation = payload.get('evaluation') or {}
    allowed = evaluation.get('decision') == 'ALLOW'
    return {'recommendation': 'ALLOW' if allowed else 'DENY',
            'target_state': payload.get('target_state', ''),
            'rationale': 'the recorded approvals and evidence support this reading',
            'evidence_sufficient': allowed,
            'missing_evidence': list(evaluation.get('reasons') or [])}


def _variance_findings(payload):
    return {'findings': [{'path': v.get('path', ''), 'kind': v.get('kind', ''),
                          'severity': v.get('severity', 'LOW'), 'material': True,
                          'explanation': 'the two runtimes differ at this path'}
                         for v in payload.get('variances') or []]}


def _rca_hypothesis(payload):
    kind = payload.get('kind', '')
    paths = ', '.join(sorted({v.get('path', '') for v in payload.get('variances') or []})[:5])
    return {'hypotheses': [{
        'hypothesis': 'The %s divergence at %s originates in the migrated '
                      'transformation rather than the contract.' % (kind, paths),
        'scope': kind, 'missing_evidence': []}]}


def _patch_plan(payload):
    return {'pattern': 'APPROVED_PATTERN_REQUIRED', 'bounded': True, 'tests': [],
            'changes': [{'path': v.get('path', ''),
                         'intent': 'correct the transformation producing this value'}
                        for v in payload.get('variances') or []]}


def _case_design(payload):
    """At least one case: the schema carries minItems 1."""
    interfaces = payload.get('interfaces') or [{}]
    cases = []
    for index, x in enumerate(interfaces, 1):
        endpoint = str(x.get('endpoint') or '')
        cases.append({'id': 'CASE-%03d' % index,
                      'description': 'nominal traffic for ' + str(x.get('node', 'interface')),
                      'input': {'interface': x.get('node', ''), 'protocol': x.get('protocol', '')},
                      'method': 'POST' if x.get('protocol') in ('HTTP', 'SOAP') else 'GET',
                      'path': endpoint if endpoint.startswith('/') else '/',
                      'headers': {}, 'timing': {}, 'side_effect_observations': [],
                      'secure_references': []})
    return {'cases': cases}


def _citations(payload):
    return {'citations': [{'evidence_id': x.get('reference', ''),
                           'claim': payload.get('query', ''),
                           'locator': x.get('kind', '')}
                          for x in payload.get('retrieved') or []],
            'uncited_claims': []}


def _code_repair(payload):
    """No edits proposed. The schema requires the key, not a non-empty list."""
    return {'edits': []}


def _next_action(payload):
    return {'next_agent': None, 'next_state': None,
            'blocked_by': payload.get('stop_reason', ''),
            'rationale': 'the governed pipeline decides what runs next'}


def _finding_triage(payload):
    return {'recommendations': [{'item': check, 'why_manual': 'needs an architect',
                                 'owner': 'JAVA_ARCHITECT'}
                                for check in payload.get('unfixed_checks') or []]}


def _suggested_answers(payload):
    """A draft answer per clarification question, in the order they were asked.

    The agent matches suggestions back to questions by exact text, so echoing
    the question is not incidental -- a double that reworded them would make
    every suggestion silently fail to attach.
    """
    return {'suggestions': [
        {'question': q.get('question', ''),
         'suggested_answer': 'Follow the standard ACE-to-Spring mapping for this construct.',
         'confidence': 'MEDIUM',
         'caveat': 'confirm against the live system before relying on it'}
        for q in payload.get('questions') or []]}


def _static_check(payload):
    """parity-check / hardcoding-check reach the gateway through ``reason``.

    No response schema applies to these, so the shape only has to be a dict the
    agent can read.
    """
    return {'findings': [], 'summary': 'no divergence detected'}


#: prompt name -> callable(payload) -> answer.
ANSWERS = {
    'agent/repository-intake/inventory': _inventory,
    'agent/ace-analysis/semantic-model': _semantic_fragment,
    'agent/framework-baseline/standards': _standards,
    'agent/migration-specification/authoring': _specification,
    'agent/planning/mapping-catalogue': _mapping_catalogue,
    'agent/code-generation/project-layout': _project_layout,
    'agent/code-generation/file-synthesis': _file_synthesis,
    'agent/code-generation/quality-failure-triage': lambda p: {'failures': []},
    'agent/dormancy-intelligence/classification-rationale': _dormancy,
    'agent/governance/gate-assessment': _gate_assessment,
    'agent/variance-analysis/findings': _variance_findings,
    'agent/variance-analysis/parity-check': _static_check,
    'agent/variance-analysis/hardcoding-check': _static_check,
    'agent/rca/hypothesis': _rca_hypothesis,
    'agent/remediation/patch-plan': _patch_plan,
    'agent/test-engineering/case-design': _case_design,
    'agent/knowledge-evidence/citations': _citations,
    'agent/feedback/code-repair': _code_repair,
    'agent/feedback/finding-triage': _finding_triage,
    'agent/migration-orchestrator/next-action': _next_action,
    'agent/clarification/suggested-answers': _suggested_answers,
}


class ModelDouble:
    """Answers whatever prompt it is handed, recording every call.

    ``accepts_skill`` is True so ``ReasoningGateway`` passes the agent's skill
    through and ``skills`` can be asserted on -- a skill silently never
    arriving is exactly the regression worth catching, and it is now the only
    guidance an agent has.

    ``overrides`` replaces the answer for one prompt name, which is how a test
    makes the model return a schema-violating object, fail a fixed number of
    times, or answer differently on the second attempt. A callable raising
    ``RuntimeError`` reaches ``reason`` as a provider failure, which is the
    retryable path.
    """

    accepts_skill = True
    model = 'double'

    def __init__(self, overrides=None):
        self.overrides = dict(overrides or {})
        self.calls = []
        self.skills = []

    def prompt_name(self, template):
        return PROMPT_BY_TEMPLATE.get(template, template)

    def count(self, prompt_name):
        """How many requests this double answered for one prompt."""
        return sum(1 for c in self.calls if c['prompt'] == prompt_name)

    def invoke(self, template, payload, skill=None):
        name = self.prompt_name(template)
        self.calls.append({'prompt': name, 'template': template, 'payload': payload,
                           'skill': skill})
        self.skills.append(skill)
        handler = self.overrides.get(name, ANSWERS.get(name))
        if handler is None:
            # An unregistered prompt answers emptily rather than guessing: the
            # response schema then says which prompt has no answer here, which
            # is a better failure than a plausible-looking wrong shape.
            return {}
        return handler(payload)
