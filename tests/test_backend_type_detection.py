"""Backend-type detection follows the skill's table, and finds every backend.

A real ClaimReimbursement service routes to RxClaim and to RECAP. It was
classified SOAP because the canonical model ships ``.wsdl`` contracts (and a
shared ``*_LIB`` library carries SOAP helpers), so code generation produced a
SOAP/EOMS service for a backend the service never calls.
"""
from app.agents.code_generation import backends_of
from app.services.enterprise_v5 import ACEParser

SVC = 'ClaimReimbursement\\'
LIB = 'ESL_PBM_CommonFlowUtility_LIB\\com\\cvscm\\ecs\\common\\util\\'


def _model(interfaces=(), flows=(), subflows=(), esql=()):
    return {'interfaces': [dict(i) for i in interfaces], 'esql': list(esql),
            'flows': [dict(f, nodes=f.get('nodes', [])) for f in flows],
            'subflows': [dict(f, nodes=f.get('nodes', [])) for f in subflows]}


def _iface(protocol, source, node='n'):
    return {'protocol': protocol, 'source': source, 'node': node, 'endpoint': 'UNKNOWN'}


def detect(model):
    return ACEParser()._detect_backend_type(model)


def test_rxclaim_and_recap_subflows_win_over_wsdl_and_shared_library_soap():
    model = _model(
        interfaces=[_iface('SOAP', 'ClaimEmmCanonicalMS\\participants\\ECSClaim_v1.wsdl', 'IClaimPort'),
                    _iface('SOAP', LIB + 'ESLFacetsUtil.esql', 'ECSFacetsUtil_SetLocalEnvionement'),
                    _iface('HTTP', SVC + 'gen\\ClaimReimbursement.msgflow')],
        flows=[{'path': SVC + 'gen\\ClaimReimbursement.msgflow'}],
        subflows=[{'path': SVC + 'getClaimReimbursement_Rxclaim.subflow'},
                  {'path': SVC + 'getClaimReimbursement_Recap.subflow'}])
    found = detect(model)
    assert found['type'] == 'RXCLAIM_SBO'
    assert found['backends'] == ['RXCLAIM_SBO', 'RECAP']


def test_a_wsdl_alone_is_not_soap_routing():
    found = detect(_model(interfaces=[_iface('SOAP', 'svc\\Thing.wsdl'), _iface('HTTP', 'svc\\a.msgflow')],
                          flows=[{'path': 'svc\\a.msgflow'}]))
    assert found['type'] == 'REST_EXTERNAL_DIRECT'


def test_a_soap_envelope_in_the_services_own_esql_is_soap():
    found = detect(_model(interfaces=[_iface('SOAP', SVC + 'get.esql', 'Call')], flows=[{'path': SVC + 'a.msgflow'}]))
    assert found['type'] == 'SOAP' and found['backends'] == ['SOAP']


def test_a_recap_helper_in_a_shared_library_says_nothing_about_this_service():
    found = detect(_model(flows=[{'path': SVC + 'a.msgflow'}],
                          subflows=[{'path': LIB + 'RecapMVSAccessHelper.subflow'}]))
    assert 'RECAP' not in found['backends']


def test_nothing_recognisable_is_undetermined():
    found = detect(_model())
    assert found == {'type': 'UNDETERMINED', 'signal': 'no recognised backend-type signal found', 'backends': []}


def test_mq_with_no_other_signal_is_mq_cobol():
    node = {'id': 'q', 'type': 'ComIbmMQOutputNode', 'properties': {'queueName': 'PLAIN.OUT'}}
    found = detect(_model(flows=[{'path': SVC + 'a.msgflow', 'nodes': [node]}]))
    assert found['type'] == 'MQ_COBOL'


def test_code_generation_reads_every_backend_and_falls_back_to_the_old_shape():
    assert backends_of({'backend_type': {'type': 'RXCLAIM_SBO', 'backends': ['RXCLAIM_SBO', 'RECAP']}}) == [
        'RXCLAIM_SBO', 'RECAP']
    assert backends_of({'backend_type': {'type': 'SOAP'}}) == ['SOAP']   # an analysis stored before `backends`
    assert backends_of({}) == []
