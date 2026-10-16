"""What the generated Spring project must contain to be a working service.

The generator used to emit one ``@Service`` per flow whose body was
``return input;``. These tests pin the facts that distinguish a working service
from that scaffold: the path ACE published is served, the ESQL assignments are
executed, the node graph is followed in order, and the dependencies match the
constructs actually present.

Two of them (``test_an_esql_literal_address_never_reaches_source`` and
``test_a_credential_shaped_assignment_is_not_hard_coded``) assert the
generator's output against the migration quality gate's own two scanning rules,
copied here verbatim. The gate reads comments as readily as code, and ESQL
carries addresses and credentials routinely, so quoting ACE into generated
source is exactly where that breaks.
"""
import json
import re
from pathlib import Path

import pytest

from app.services.enterprise_v5 import ACEParser
from app.services.springgen import SpringProjectGenerator, parse_esql

#: ``quality_validate``'s rules, verbatim. Duplicated on purpose: if they are
#: ever relaxed there, these tests should keep holding the generator to the
#: stricter version rather than silently relaxing with them.
SECRET_SCAN = re.compile(r'(?i)(password|secret|token)\s*[:=]\s*["\'][^$][^"\']+')
ENDPOINT_SCAN = re.compile(r'https?://(?!localhost|example)')
SCANNED = ('.java', '.yml', '.yaml', '.properties')

FRAMEWORK = {'standards': {'build': {'springBootVersion': '3.3.0', 'javaVersion': '21'}}}

FLOW = '''<flow>
  <node id="HTTPInput" type="ComIbmWSInput.msgnode" URLSpecifier="/orders/validate"
        messageDomainProperty="JSON"/>
  <node id="Validate" type="ComIbmFilter.msgnode"/>
  <node id="MapOrder" type="ComIbmCompute.msgnode"/>
  <node id="Backend" type="ComIbmWSRequest.msgnode" URLSpecifier="https://backend.invalid/v1/orders"/>
  <node id="PublishAudit" type="ComIbmMQOutput.msgnode" queueName="ORDERS.AUDIT"/>
  <node id="HTTPReply" type="ComIbmWSReply.msgnode"/>
  <connection source="HTTPInput" target="Validate"/>
  <connection source="Validate" target="MapOrder"/>
  <connection source="MapOrder" target="Backend"/>
  <connection source="Backend" target="PublishAudit"/>
  <connection source="PublishAudit" target="HTTPReply"/>
</flow>'''

ESQL = '''CREATE COMPUTE MODULE Orders_MapOrder
  CREATE FUNCTION Main() RETURNS BOOLEAN
  BEGIN
    SET OutputRoot.JSON.Data.orderNumber = InputRoot.JSON.Data.orderId;
    SET OutputRoot.JSON.Data.status = 'ACCEPTED';
    SET OutputRoot.JSON.Data.customer.id = InputRoot.JSON.Data.member.memberId;
    SET OutputRoot.JSON.Data.total = CAST(InputRoot.JSON.Data.amount AS DECIMAL);
    SET Environment.Variables.audit = 'recorded';
    RETURN TRUE;
  END;
END MODULE;'''


@pytest.fixture
def ace(tmp_path):
    root = tmp_path / 'ace'
    root.mkdir()
    (root / 'Orders.msgflow').write_text(FLOW)
    (root / 'Orders.esql').write_text(ESQL)
    return root


@pytest.fixture
def generate(tmp_path, ace):
    def _generate(source=ace, framework=None, plan=None, service='orders',
                  package='com.example.orders', with_source=True):
        model = ACEParser().parse(source)
        out = tmp_path / ('out-%d' % len(list(tmp_path.glob('out-*'))))
        result = SpringProjectGenerator().generate(
            out, service, package, model, 'PLAN-1', framework or FRAMEWORK,
            source_root=str(source) if with_source else None, plan=plan)
        return result, Path(result['root'])
    return _generate


def _read(root, relative):
    return (root / relative).read_text(encoding='utf-8')


def _java(root, relative):
    return _read(root, 'src/main/java/com/example/orders/' + relative)


def _scanned_text(root):
    return '\n'.join(f.read_text(errors='ignore') for f in root.rglob('*')
                     if f.is_file() and f.suffix.lower() in SCANNED)


# -- the service is actually a service ---------------------------------------
def test_the_path_ace_published_is_the_path_the_controller_serves(generate):
    """The endpoint is migration output, not a naming convention."""
    _, root = generate()
    controller = _java(root, 'controller/OrdersController.java')
    assert '@PostMapping(path = "/orders/validate"' in controller
    # The context root stays externalised: where the service is mounted is a
    # deployment decision, and baking it in makes every environment a code change.
    assert '@RequestMapping("${integration.orders.context-root:}")' in controller
    assert 'service.process(request, correlationId)' in controller


def test_the_flow_service_executes_the_nodes_in_connection_order(generate):
    """The old generator ignored the graph entirely; order is the behaviour."""
    service = None
    _, root = generate()
    service = _java(root, 'service/OrdersService.java')
    positions = [service.index('#' + node) for node in
                 ('HTTPInput', 'Validate', 'MapOrder', 'Backend', 'PublishAudit', 'HTTPReply')]
    assert positions == sorted(positions), 'nodes must appear in the order ACE connected them'
    # Each step is a real call, and each is attributed to the node it came from.
    assert 'if (!validateFilter.matches(request))' in service
    assert 'OrdersResponse response = ordersMapOrderTransformer.transform(request)' in service
    assert 'backendClient.call(response)' in service
    assert 'publishAuditPublisher.publish(response)' in service


def test_esql_assignments_become_field_assignments(generate):
    """A copy, a literal and a nested copy each translate; a CAST does not."""
    _, root = generate()
    transformer = _java(root, 'transform/OrdersMapOrderTransformer.java')
    assert 'source.orderId()' in transformer                      # direct copy
    assert '"ACCEPTED"' in transformer                            # literal
    assert 'srcMember == null ? null : srcMember.memberId()' in transformer   # nested copy
    # What cannot be translated is a TODO beside the statement that needs a
    # human -- never a silent passthrough that looks finished.
    assert 'TODO SET OutputRoot.JSON.Data.total = CAST' in transformer
    # The whole module is quoted so the translation is reviewable against it,
    # including the statement that was not a payload assignment at all.
    assert 'SET Environment.Variables.audit' in transformer


def test_the_message_tree_nests_rather_than_flattens(generate):
    """``customer.id`` must serialise as ``{"customer":{"id":...}}``.

    Flattening the path to ``customerId`` would compile and would change the
    contract the migrated service has to honour.
    """
    _, root = generate()
    response = _java(root, 'dto/OrdersResponse.java')
    assert 'OrdersResponseCustomer customer' in response
    nested = _java(root, 'dto/OrdersResponseCustomer.java')
    assert '@JsonProperty("id")' in nested


def test_the_generated_project_builds_the_layers_a_service_needs(generate):
    """The file list itself is the regression: 4 files was the bug."""
    result, root = generate()
    paths = {x['path'] for x in result['manifest']}
    for expected in ('pom.xml', 'Dockerfile', 'README.md',
                     'src/main/resources/application.yml',
                     'src/main/java/com/example/orders/controller/OrdersController.java',
                     'src/main/java/com/example/orders/service/OrdersService.java',
                     'src/main/java/com/example/orders/transform/OrdersMapOrderTransformer.java',
                     'src/main/java/com/example/orders/client/BackendClient.java',
                     'src/main/java/com/example/orders/messaging/PublishAuditPublisher.java',
                     'src/main/java/com/example/orders/exception/GlobalExceptionHandler.java',
                     'src/main/java/com/example/orders/support/CorrelationIdFilter.java',
                     'src/test/java/com/example/orders/OrderApplicationTests.java'.replace(
                         'OrderApplication', 'OrdersApplication'),
                     'src/test/java/com/example/orders/controller/OrdersControllerTest.java'):
        assert expected in paths, expected
    assert all(x['hash'] and x['plan_id'] == 'PLAN-1' for x in result['manifest'])


def test_the_queue_ace_used_is_the_configured_default(generate):
    """ACE's own destination, externalised rather than discarded or inlined."""
    _, root = generate()
    assert 'ORDERS.AUDIT' in _read(root, 'src/main/resources/application.yml')
    publisher = _java(root, 'messaging/PublishAuditPublisher.java')
    assert '@Value("${integration.queues.publish-audit}")' in publisher
    assert 'jmsTemplate.convertAndSend(destination, payload)' in publisher


def test_dependencies_follow_the_constructs_present(generate, tmp_path):
    """A POM carrying what the code does not import is the rule-2 defect."""
    _, root = generate()
    pom = _read(root, 'pom.xml')
    assert 'spring-boot-starter-artemis' in pom, 'the flow has an MQOutput node'
    assert 'spring-kafka' not in pom
    assert 'spring-boot-starter-jdbc' not in pom

    http_only = tmp_path / 'http-only'
    http_only.mkdir()
    (http_only / 'Simple.msgflow').write_text(
        '<flow><node id="in" type="ComIbmWSInput.msgnode" URLSpecifier="/simple"'
        ' messageDomainProperty="JSON"/></flow>')
    _, plain = generate(source=http_only)
    assert 'spring-boot-starter-artemis' not in _read(plain, 'pom.xml')


# -- the plan is what authorises the code ------------------------------------
def test_the_plans_approved_pattern_wins_over_the_node_name(generate, tmp_path):
    """G3 approves a plan; generation must emit what that plan says.

    Here the plan maps a Compute node to the router pattern. Resolving on the
    node's name would produce a transformer -- which is the approval and the
    artefact disagreeing about what was built.
    """
    source = tmp_path / 'planned'
    source.mkdir()
    (source / 'Flow.msgflow').write_text(
        '<flow><node id="Decide" type="ComIbmCompute.msgnode"/></flow>')
    plan = {'mappings': [{'source_reference': 'Flow.msgflow#Decide',
                          'spring_pattern': 'ROUTER_STRATEGY'}]}
    result, root = generate(source=source, plan=plan)
    paths = {x['path'] for x in result['manifest']}
    assert 'src/main/java/com/example/orders/routing/DecideRouter.java' in paths
    assert not any('transform/DecideTransformer' in p for p in paths)


# -- nothing ACE carried leaks into source -----------------------------------
def test_an_esql_literal_address_never_reaches_source(generate, tmp_path):
    source = tmp_path / 'addressed'
    source.mkdir()
    (source / 'F.msgflow').write_text(
        '<flow><node id="in" type="ComIbmWSInput.msgnode" URLSpecifier="/f"'
        ' messageDomainProperty="JSON"/><node id="m" type="ComIbmCompute.msgnode"/></flow>')
    (source / 'F.esql').write_text(
        "CREATE COMPUTE MODULE F_Map BEGIN"
        " SET OutputRoot.JSON.Data.target = 'https://partner.internal.corp/submit';"
        " SET OutputRoot.JSON.Data.id = InputRoot.JSON.Data.id; END MODULE;")
    result, root = generate(source=source)
    text = _scanned_text(root)
    assert not ENDPOINT_SCAN.search(text), 'an address in a comment fails the gate too'
    assert any('partner.internal.corp' in item for item in result['action_items']), \
        'the address must still be reported, not just dropped'
    transformer = _java(root, 'transform/FMapTransformer.java')
    assert 'TODO externalise the address' in transformer


def test_a_credential_shaped_assignment_is_not_hard_coded(generate, tmp_path):
    source = tmp_path / 'credentialed'
    source.mkdir()
    (source / 'F.msgflow').write_text(
        '<flow><node id="in" type="ComIbmWSInput.msgnode" URLSpecifier="/f"'
        ' messageDomainProperty="JSON"/><node id="m" type="ComIbmCompute.msgnode"/></flow>')
    (source / 'F.esql').write_text(
        "CREATE COMPUTE MODULE F_Map BEGIN"
        " SET OutputRoot.JSON.Data.password = 'hunter2';"
        " SET OutputRoot.JSON.Data.id = InputRoot.JSON.Data.id; END MODULE;")
    result, root = generate(source=source)
    text = _scanned_text(root)
    assert not SECRET_SCAN.search(text)
    assert 'hunter2' not in text, 'the literal must not be written into source at all'
    assert any('credential-shaped' in item for item in result['action_items'])


def test_the_discovered_endpoint_is_recorded_where_the_gate_does_not_scan(generate):
    """Reported, not lost: the README and the manifest are not scanned files."""
    result, root = generate()
    assert 'backend.invalid/v1/orders' in _read(root, 'README.md')
    manifest = json.loads(_read(root, 'generated-manifest.json'))
    assert manifest and all('hash' in entry for entry in manifest)


# -- degrading honestly ------------------------------------------------------
def test_without_esql_the_flow_exchanges_maps_and_says_so(generate):
    """The project is still generated and wired; the gap is stated."""
    result, root = generate(with_source=False)
    service = _java(root, 'service/OrdersService.java')
    assert 'Map<String, Object> process(Map<String, Object> request' in service
    assert any('not available at generation time' in item for item in result['action_items'])
    transformer = _java(root, 'transform/OrdersMapOrderTransformer.java')
    assert 'TODO No ESQL field assignments were resolvable' in transformer


def test_an_unmapped_construct_generates_nothing_and_is_reported(generate, tmp_path):
    source = tmp_path / 'vendor'
    source.mkdir()
    (source / 'V.msgflow').write_text(
        '<flow><node id="custom" type="VendorCustomNode"/></flow>')
    result, root = generate(source=source)
    assert any('No Spring pattern for V.msgflow#custom' in item for item in result['action_items'])
    assert not (root / 'src/main/java/com/example/orders/service').exists()


def test_a_presentation_only_node_is_skipped_silently(generate, tmp_path):
    """A canvas icon has nothing to generate and is not a finding either."""
    source = tmp_path / 'iconned'
    source.mkdir()
    (source / 'F.msgflow').write_text(
        '<flow><node id="icon" type="utility:GIFFileGraphic"/>'
        '<node id="in" type="ComIbmWSInput.msgnode" URLSpecifier="/f"'
        ' messageDomainProperty="JSON"/></flow>')
    result, root = generate(source=source)
    assert not any('icon' in item for item in result['action_items'])
    assert '#icon' not in _java(root, 'service/FService.java')


# -- the ESQL reader ---------------------------------------------------------
def test_parse_esql_resolves_each_assignment_kind():
    modules = parse_esql('F.esql', '''CREATE COMPUTE MODULE F_Main
    BEGIN
      SET OutputRoot.JSON.Data.a = InputRoot.JSON.Data.b;
      SET OutputRoot.XMLNSC.c = 'literal';
      SET OutputRoot.JSON.Data.d = 42;
      SET OutputRoot.JSON.Data.e = COALESCE(InputRoot.JSON.Data.f, '');
      SET OutputRoot.JSON.Data.items[1].sku = InputRoot.JSON.Data.sku;
    END MODULE;''')
    module, = modules
    assert module.name == 'F_Main'
    kinds = {tuple(a['target']): a['kind'] for a in module.assignments}
    assert kinds[('a',)] == 'COPY'
    assert kinds[('c',)] == 'LITERAL'      # the XMLNSC domain is stripped like JSON.Data
    assert kinds[('d',)] == 'LITERAL'
    assert kinds[('e',)] == 'UNRESOLVED'
    # A subscripted path is not a plain field, so it is reported rather than
    # translated into an assignment that would silently address element zero.
    assert ('items', 'sku') not in kinds
    assert any('items[1]' in note for note in module.notes)


def test_parse_esql_reports_statements_it_does_not_translate():
    module, = parse_esql('F.esql', '''CREATE COMPUTE MODULE F_Main
    BEGIN
      SET Environment.Variables.x = 'y';
      IF InputRoot.JSON.Data.a > 1 THEN
        PROPAGATE TO TERMINAL 'out1';
      END IF;
      THROW USER EXCEPTION MESSAGE 2951;
    END MODULE;''')
    notes = ' '.join(module.notes)
    assert 'Environment.Variables.x' in notes
    assert 'PROPAGATE' in notes and 'THROW' in notes
    assert module.assignments == []
