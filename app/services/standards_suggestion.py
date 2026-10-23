"""A standards-based suggestion for a clarification the model did not answer.

Every open question carries a suggested answer. The model drafts the specific ones;
when it is unreachable, runs out of room or skips a question, the box used to stay
empty. This supplies the answer the platform's own standards give for that kind of
question (Spring Boot conventions, twelve-factor configuration, the framework
baseline), so a reviewer always starts from something to accept, edit or reject.

It is a default, not a decision: recorded as STANDARDS_BASELINE with LOW confidence,
so auto mode (which accepts only high-confidence suggestions) never acts on it and
the reviewer sees plainly where it came from.
"""
MODE = 'STANDARDS_BASELINE'
CONFIDENCE = 'LOW'
CAVEAT = ('Platform standard for this kind of question, not drafted from the source. '
          'Check it against the service before accepting.')

#: (keywords, answer). The first rule with a keyword in the question wins, so the
#: more specific topics come first.
RULES = (
    (('password', 'secret', 'credential', 'token', 'certificate', 'keystore', 'truststore', 'encrypt'),
     'Do not carry the value into the code or the repository. Read it from the environment or the '
     'platform secret store through Spring configuration properties (twelve-factor), with no default '
     'in application.yml, and keep TLS material in the keystore/truststore the deployment mounts.'),
    (('transaction', 'rollback', 'commit', 'two-phase', 'xa '),
     'Use Spring declarative transactions (@Transactional) at the service boundary, rolling back on '
     'any unchecked exception. Keep one transaction per message or request; use idempotent consumers '
     'rather than distributed (XA) transactions.'),
    (('exception', 'error', 'fault', 'failure', 'catch', 'throw', 'retry', 'backout', 'dead letter'),
     'Handle the failure explicitly: translate it to a typed exception, return a consistent error '
     'response through @ControllerAdvice (REST) or route the message to a dead-letter queue after a '
     'bounded retry with backoff. Log it once with the correlation id, and never swallow it.'),
    (('queue', 'mq', 'jms', 'kafka', 'topic', 'publish', 'subscribe', 'message flow', 'listener'),
     'Use Spring messaging (JmsListener or the Kafka client) with the destination name in '
     'configuration properties, a bounded concurrency, acknowledgement after successful processing, '
     'and a dead-letter destination for messages that keep failing.'),
    (('database', 'sql', 'odbc', 'jdbc', 'table', 'cursor', 'stored procedure', 'insert', 'select '),
     'Use Spring Data JPA or JdbcTemplate with parameterised statements only, a pooled DataSource '
     'configured from properties, and the schema owned by a Flyway/Liquibase migration. Do not build '
     'SQL by string concatenation.'),
    (('http', 'rest', 'soap', 'wsdl', 'endpoint', 'url', 'api', 'web service', 'request'),
     'Expose or call it through Spring MVC controllers or a configured WebClient/RestClient, with '
     'timeouts and the base URL in configuration properties, request validation (Bean Validation), '
     'and the original contract (paths, status codes, payload shape) preserved.'),
    (('xml', 'xpath', 'xsd', 'namespace', 'json', 'transform', 'mapping', 'map ', 'esql', 'compute',
      'cast', 'literal', 'assign', 'variable', 'field', 'type'),
     'Preserve the observable behaviour exactly: map each field one-to-one into a typed Java model '
     '(Jackson/JAXB), keep the original null/empty handling and data types, and cover the construct '
     'with a unit test that compares the output against the legacy sample.'),
    (('log', 'trace', 'audit', 'monitor', 'metric', 'activity'),
     'Use SLF4J structured logging with the correlation id, Micrometer metrics and the Actuator '
     'health endpoint; keep the audit events the legacy flow produced and never log payloads that '
     'carry personal or credential data.'),
    (('config', 'property', 'properties', 'environment', 'deploy', 'bar ', 'policy', 'udp', 'user-defined'),
     'Move the setting into Spring configuration properties (application.yml, overridable by '
     'environment variables), one typed @ConfigurationProperties class per concern, and no '
     'environment-specific values in the code.'),
    (('test', 'verify', 'validate', 'assert', 'sample', 'regression', 'coverage'),
     'Cover it with JUnit 5 unit tests plus a differential test that replays the legacy sample '
     'input and compares the result field by field; the build must pass them before the gate.'),
    (('name', 'package', 'class', 'naming', 'convention'),
     'Follow the framework baseline: one package per domain under the service root package, '
     'UpperCamelCase classes named for the business role, and the legacy flow name kept as a '
     'comment for traceability.'),
)

DEFAULT = ('Follow the platform standard: keep the legacy behaviour unchanged, implement it with '
           'idiomatic Spring Boot (constructor injection, configuration properties for anything '
           'environment-specific, standard error handling and logging), and cover it with a unit '
           'test against the legacy sample. Flag it for review if the source contradicts this.')


def baseline(question, impact='', stage=''):
    """Return the suggestion fields for a question: always something."""
    text = ' %s %s ' % (str(question or '').replace('_', ' ').lower(), str(impact or '').lower())
    answer = next((a for words, a in RULES if any(w in text for w in words)), DEFAULT)
    return {'suggested_answer': answer, 'suggestion_mode': MODE, 'suggestion_confidence': CONFIDENCE,
            'suggestion_severity': '', 'suggestion_caveat': CAVEAT}
