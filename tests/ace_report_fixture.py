"""A small but real-shaped IIB REST-to-SOAP facade, written to disk for the ACE analysis report tests.

Shaped like the ``getDispositionReferenceData`` application the hand-over template was written
from: a generated top-level flow, an operation subflow, three error-handler subflows, one ESQL file
with four Compute modules and a Java routine, and per-environment BAR override files. The XML uses
the real toolkit structure (``xmi:type`` node types, ``translation`` labels, connections with
terminal names) because the report reads exactly that.
"""
from pathlib import Path

NS = ('xmlns:xmi="http://www.omg.org/XMI" xmlns:ecore="http://www.eclipse.org/emf/2002/Ecore" '
      'xmlns:eflow="http://www.ibm.com/wbi/2005/eflow" xmlns:utility="http://www.ibm.com/wbi/2005/eflow_utility"')


def node(nid, kind_type, label, **props):
    attrs = ''.join(' %s="%s"' % (k, v) for k, v in props.items())
    return ('<nodes xmi:type="%s" xmi:id="%s" location="10,10"%s><translation xmi:type="utility:ConstantString" '
            'string="%s"/></nodes>' % (kind_type, nid, attrs, label))


def wire(src, out, dst, into='in'):
    return ('<connections xmi:type="eflow:FCMConnection" xmi:id="c_%s_%s_%s" targetNode="%s" sourceNode="%s" '
            'sourceTerminalName="OutTerminal.%s" targetTerminalName="InTerminal.%s"/>' % (src, out, dst, dst, src, out, into))


def flow(nodes, wires, extra='', note=''):
    return ('<?xml version="1.0" encoding="UTF-8"?><ecore:EPackage xmi:version="2.0" %s><eClassifiers '
            'xmi:type="eflow:FCMComposite" name="FCMComposite_1">%s<composition>%s%s</composition></eClassifiers>'
            '</ecore:EPackage>' % (NS, extra, ''.join(nodes), ''.join(wires) + note))


def handler(name):
    return flow([node('i', 'eflow:FCMSource', 'Input'),
                 node('c', 'ComIbmCompute.msgnode:FCMComposite_1', 'getDispositionReferenceData_Failure',
                      computeExpression='esql://routine/#getDispositionReferenceData_Failure.Main', computeMode='all'),
                 node('o', 'eflow:FCMSink', 'Output')],
                [wire('i', 'out', 'c'), wire('c', 'out', 'o')])


ESQL = '''DECLARE EOMSurl EXTERNAL CHARACTER '';
DECLARE testHarnessURL EXTERNAL CHARACTER '';
DECLARE soapenv NAMESPACE 'http://schemas.xmlsoap.org/soap/envelope/';
DECLARE ns2 NAMESPACE 'http://www.cvs.com/esp/OpportunityService/messages/1_0';

CREATE COMPUTE MODULE getDispositionReferenceData_Request
  CREATE FUNCTION Main() RETURNS BOOLEAN
  BEGIN
    DECLARE HTTPHeaderRef REFERENCE TO InputRoot.HTTPInputHeader;
    DECLARE GlobalTransId CHAR COALESCE(HTTPHeaderRef."Eieheadertransactionid", HTTPHeaderRef."X-Global-Transaction-Id");
    DECLARE neverUsed CHAR 'x';
    SET Environment.Variables.transactionId = COALESCE(GlobalTransId, 'none');
    SET Environment.Variables.testHarness = COALESCE(HTTPHeaderRef."Testharness", '');
    SET Environment.Variables.orphan = 'unused';
    SET OutputRoot.XMLNSC.soapenv:Envelope.soapenv:Body.ns2:GetDispositionReferenceDataRequest = '';
    IF Environment.Variables.testHarness = 'P' AND testHarnessURL IS NOT NULL THEN
      SET OutputLocalEnvironment.Destination.HTTP.RequestURL = testHarnessURL;
    ELSE
      SET OutputLocalEnvironment.Destination.HTTP.RequestURL = EOMSurl;
    END IF;
    RETURN TRUE;
  END;
END MODULE;

CREATE COMPUTE MODULE getDispositionReferenceData_Response
  CREATE FUNCTION Main() RETURNS BOOLEAN
  BEGIN
    DECLARE inRef REFERENCE TO InputRoot.XMLNSC.*:Envelope.*:Body.*:GetDispositionReferenceDataResponse;
    DECLARE J INTEGER 1;
    DECLARE K INTEGER CARDINALITY(inRef.*:CodeTable[]);
    CREATE FIELD OutputRoot.JSON.Data.codeTables IDENTITY(JSON.Array)codeTables;
    WHILE J <= K DO
      SET OutputRoot.JSON.Data.codeTables.Item[J].productId = inRef.*:CodeTable[J].*:PresentationChannelProductIdentifier;
      SET OutputRoot.JSON.Data.codeTables.Item[J].dispositions.Item[1].levelNumber = inRef.*:CodeTable[J].*:DispositionCodes[1].*:LevelNumber;
      SET J = J + 1;
    END WHILE;
    RETURN TRUE;
  END;
END MODULE;

CREATE COMPUTE MODULE getDispositionReferenceData_Failure
  CREATE FUNCTION Main() RETURNS BOOLEAN
  BEGIN
    DECLARE ErrorDesc CHAR;
    SET OutputRoot.Properties.ContentType = 'application/json';
    SET OutputLocalEnvironment.Destination.HTTP.ReplyStatusCode = 500;
    SET OutputRoot.JSON.Data.httpCode = '500';
    SET OutputRoot.JSON.Data.httpMessage = 'Service Error';
    CALL getBrokerErrorShortDescription(InputExceptionList.*[1].Number, ErrorDesc);
    SET OutputRoot.JSON.Data.moreInformation = COALESCE(ErrorDesc, '') || '|transactionID:' || Environment.Variables.transactionId;
    RETURN TRUE;
  END;
  CREATE PROCEDURE getBrokerErrorShortDescription(IN code INTEGER, IN info CHARACTER) RETURNS CHARACTER
  LANGUAGE JAVA EXTERNAL NAME "com.cvscm.esd.common.util.ESDExceptionListUtil.getESDBrokerExceptionShortText";
END MODULE;

CREATE COMPUTE MODULE getDispositionReferenceData_Error
  CREATE FUNCTION Main() RETURNS BOOLEAN
  BEGIN
    SET OutputRoot.JSON.Data.httpCode = '500';
    SET OutputRoot.JSON.Data.httpMessage = 'Internal Service Error';
    RETURN TRUE;
  END;
END MODULE;
'''

PROPS = {'DEV': ('dev1-eoms.caremark.com', 'http://esl-wiremock.corp.cvscaremark.com:9000'),
         'QA': ('sit1-eoms.caremark.com', 'http://esl-wiremock.corp.cvscaremark.com:9000'),
         'UAT': ('sit1-eoms.caremark.com', 'http://esl-wiremock.corp.cvscaremark.com:9000'),
         'PROD': ('eoms.caremark.com', 'http://esl-wiremock.corp.cvscaremark.com:9000')}


def properties(host, harness):
    return '\n'.join([
        'gen.getDispositionReferenceData#additionalInstances=10',
        'gen.getDispositionReferenceData#HTTP Input.timeoutForClient=55',
        'getDispositionReferenceData#EOMSurl=https://%s/EOMSConfigurationServiceWEB/OpportunityConfigurationService' % host,
        'getDispositionReferenceData#testHarnessURL=' + harness,
        'getDispositionReferenceData#InvokeHEE.sslProtocol=TLSv1.2',
        'getDispositionReferenceData#InvokeHEE.timeoutForServer=30', ''])


def write(root):
    root = Path(root)
    (root / 'gen').mkdir(parents=True, exist_ok=True)
    top = flow([node('http', 'ComIbmHTTPInput.msgnode:FCMComposite_1', 'HTTP Input',
                     URLSpecifier='/pbmpharmacy_24/api/v1/edb/opportunitydispositioncode*', useHTTPS='true',
                     messageDomainProperty='JSON', timeoutForClient='55', faultFormat='HTML', parseQueryString='true'),
                node('route', 'ComIbmRouteToLabel.msgnode:FCMComposite_1', 'Route To Label'),
                node('label', 'ComIbmLabel.msgnode:FCMComposite_1', 'Label getDispositionReferenceData',
                     labelName='getDispositionReferenceData'),
                node('sub', 'getDispositionReferenceData.subflow:FCMComposite_1', 'getDispositionReferenceData (Implementation)',
                     EOMSurl='http://dummyurl.com', testHarnessURL='http://dummytesturl.com'),
                node('reply', 'ComIbmWSReply.msgnode:FCMComposite_1', 'HTTP Reply'),
                node('catchH', 'getDispositionReferenceDataInputCatchHandler.subflow:FCMComposite_1', 'CatchHandler'),
                node('failH', 'getDispositionReferenceDataInputFailureHandler.subflow:FCMComposite_1', 'FailureHandler'),
                node('timeH', 'getDispositionReferenceDataInputTimeoutHandler.subflow:FCMComposite_1', 'TimeoutHandler')],
               [wire('http', 'out', 'route'), wire('route', 'label', 'label'), wire('label', 'out', 'sub'),
                wire('sub', 'out', 'reply'), wire('http', 'catch', 'catchH'), wire('http', 'failure', 'failH'),
                wire('http', 'timeout', 'timeH'), wire('catchH', 'out', 'reply'), wire('failH', 'out', 'reply'),
                wire('timeH', 'out', 'reply')],
               note='<stickyNote body="This flow was automatically generated. Do not edit."/>')
    (root / 'gen' / 'getDispositionReferenceData.msgflow').write_text(top, encoding='utf-8')
    sub = flow([node('i', 'eflow:FCMSource', 'Input_1'),
                node('req', 'ComIbmCompute.msgnode:FCMComposite_1', 'DIMtoHEE',
                     computeExpression='esql://routine/#getDispositionReferenceData_Request.Main', computeMode='all'),
                node('call', 'ComIbmWSRequest.msgnode:FCMComposite_1', 'InvokeHEE', URLSpecifier='http://dummyurl.com',
                     webServiceURL='http://dummyurl.com', sslProtocol='TLSv1.2', timeoutForServer='60',
                     validateMaster='none'),
                node('resp', 'ComIbmCompute.msgnode:FCMComposite_1', 'HEEtoDIM',
                     computeExpression='esql://routine/#getDispositionReferenceData_Response.Main', computeMode='all'),
                node('err', 'ComIbmCompute.msgnode:FCMComposite_1', 'ComposeHEE_Error',
                     computeExpression='esql://routine/#getDispositionReferenceData_Error.Main', computeMode='all'),
                node('fail', 'ComIbmCompute.msgnode:FCMComposite_1', 'ComposeHEE_Failure',
                     computeExpression='esql://routine/#getDispositionReferenceData_Failure.Main', computeMode='all'),
                node('o', 'eflow:FCMSink', 'Output_1')],
               [wire('i', 'out', 'req'), wire('req', 'out', 'call'), wire('call', 'out', 'resp'),
                wire('call', 'error', 'err'), wire('call', 'failure', 'fail'), wire('resp', 'out', 'o'),
                wire('err', 'out', 'o'), wire('fail', 'out', 'o')],
               extra='<propertyOrganizer><propertyDescriptor propertyName="EOMSurl"/>'
                     '<propertyDescriptor propertyName="testHarnessURL"/></propertyOrganizer>')
    (root / 'getDispositionReferenceData.subflow').write_text(sub, encoding='utf-8')
    for name in ('Catch', 'Failure', 'Timeout'):
        (root / ('getDispositionReferenceDataInput%sHandler.subflow' % name)).write_text(handler(name), encoding='utf-8')
    (root / 'getDispositionReferenceData.esql').write_text(ESQL, encoding='utf-8')
    for env, (host, harness) in PROPS.items():
        (root / ('getDispositionReferenceData.%s.properties' % env)).write_text(properties(host, harness), encoding='utf-8')
    (root / 'README.md').write_text('# getDispositionReferenceData\n', encoding='utf-8')
    return root
