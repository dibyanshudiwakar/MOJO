"""LLM-first artefact production, with a recorded deterministic fallback.

Every agent used to call ``ReasoningGateway.reason()`` and then discard the
answer into a metrics counter -- ``metrics['model_ambiguity_count'] =
len(answer['ambiguities'])`` -- while the artefact a gate actually approves was
built by deterministic Python. This module inverts that: the model, guided by
the agent's skill, produces the artefact, and the Python path becomes the
fallback for when it cannot.

Three properties make that safe to rely on.

**It never raises.** A missing provider, a DLP refusal, a provider error or a
response that does not match its schema all degrade to the deterministic path.
An agent therefore always completes, which matters because the alternative is
14 agents that cannot run without a configured gateway.

**Every degradation is recorded.** ``mode`` is ``MODEL_GATEWAY``,
``PARTIAL_MODEL_GATEWAY`` or ``DETERMINISTIC_FALLBACK`` and ``reason`` says
which of the above happened, on the run row and in the evidence metadata. An
auditor can tell a model-authored artefact from a computed one, which is the
whole point of allowing a fallback at all.

**The response is schema-checked before it is believed.** A model answer is
untrusted input: it is about to be persisted, hashed, bound to a gate approval
and read by the next agent. ``config/agent-response-schemas.v1.json`` is what
stops a malformed or inventive artefact getting that far, so an unvalidated
answer is treated exactly like no answer.

What this module does *not* do is decide anything by model. Hashing, the audit
chain, RBAC, the tool allow-list, DLP, path confinement and the independent
pass/fail verdicts (compile, quality gate, differential comparison) stay in
Python: the first group is arithmetic a model cannot perform, the second is
enforcement that ACE source text must not be able to argue its way past, and
the third is the evidence that model output was correct in the first place.
"""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from app.agents.reasoning import DETERMINISTIC, MODEL_GATEWAY, ReasoningGateway
from app.services.feedback import redact

SCHEMA_FILE = Path(__file__).resolve().parents[2] / 'config' / 'agent-response-schemas.v1.json'

#: Some items came from the model and some fell back. Distinct from both pure
#: modes because "the semantic model is half model-authored" is a different
#: provenance claim from either, and a reviewer needs to see which.
PARTIAL = 'PARTIAL_MODEL_GATEWAY'

#: Ceiling on one payload's serialised size. A whole ACE repository in a single
#: request is both refused and useless; ``produce_many`` exists to split the
#: work per source file instead of pushing this number up.
MAX_PAYLOAD_CHARS = 180_000

_schemas = None


def schemas():
    """The response schema per prompt name, loaded once."""
    global _schemas
    if _schemas is None:
        _schemas = json.loads(SCHEMA_FILE.read_text(encoding='utf-8'))['schemas']
    return _schemas


def schema_for(name):
    return schemas().get(name)


def scrub(value):
    """Redact a payload in place of shape, not of structure.

    ``AIRegistryService.gateway_request`` refuses the whole request when its
    DLP rules match, and ``CREDENTIAL_ASSIGNMENT`` matches *any* ``password:``
    line -- including one correctly externalised as ``${PASSWORD}``. ESQL and
    Spring configuration carry those constantly, so sending source verbatim
    means every run is a refusal (``repository_intake`` says exactly this in a
    comment). Values are blanked and the structure the model reasons about
    survives.

    The cost is real and worth knowing: the ``PAN`` rule matches any run of 13
    or more digits, so a long numeric literal in ESQL reaches the model as
    ``<REDACTED_NUMBER>``. That is recorded rather than hidden -- an artefact
    built from redacted input is still reviewed against the original source.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def validate(artefact, name):
    """``''`` when the artefact matches its schema, else why it does not."""
    schema = schema_for(name)
    if schema is None:
        return 'no_schema_registered:' + name
    if not isinstance(artefact, dict):
        return 'response_not_an_object'
    errors = sorted(Draft202012Validator(schema).iter_errors(artefact),
                    key=lambda e: list(e.absolute_path))
    if not errors:
        return ''
    first = errors[0]
    where = '/'.join(str(p) for p in first.absolute_path) or '(root)'
    return '%s: %s' % (where, first.message[:160])


def _log(context):
    logger = getattr(context, 'logger', None)
    return (logger.info if logger else (lambda m: None),
            logger.warn if logger else (lambda m: None))


def produce(context, prompt_name, payload, fallback, *, schema_name=None, skill=None):
    """Ask the model for the artefact; fall back to ``fallback()`` if it cannot.

    ``fallback`` is a zero-argument callable holding the deterministic path. It
    is called only when the model did not supply a usable artefact, and its own
    exceptions propagate -- a broken fallback is a defect, not a degradation.

    Returns ``(artefact, provenance)``.
    """
    info, warn = _log(context)
    schema_name = schema_name or prompt_name
    cleared = scrub(payload)
    serialised = json.dumps(cleared, sort_keys=True, default=str)
    if len(serialised) > MAX_PAYLOAD_CHARS:
        provenance = {'mode': DETERMINISTIC, 'prompt_name': prompt_name,
                      'reason': 'payload_too_large:%d_chars' % len(serialised),
                      'artefact_source': 'DETERMINISTIC', 'schema': schema_name}
        warn('Payload for "%s" is %d chars — over the %d limit; computing instead'
             % (prompt_name, len(serialised), MAX_PAYLOAD_CHARS))
        return fallback(), provenance

    answer, provenance = ReasoningGateway(context.repo).reason(
        context, prompt_name, cleared, skill=skill)
    provenance['schema'] = schema_name
    if answer is None:
        # reason() already recorded why, and already logged it.
        provenance['artefact_source'] = 'DETERMINISTIC'
        return fallback(), provenance

    problem = validate(answer, schema_name)
    if problem:
        # An answer that does not match its schema is treated exactly like no
        # answer: it is about to be hashed and bound to an approval.
        provenance['mode'] = DETERMINISTIC
        # The keys that did come back. complete_request persists only the
        # response hash, so without this a schema failure is undiagnosable
        # after the fact -- the run says a required property was missing and
        # nothing anywhere says what the model actually answered.
        keys = sorted(answer)[:12] if isinstance(answer, dict) else type(answer).__name__
        provenance['reason'] = 'model_response_schema_invalid:%s; response keys: %s' % (
            problem, keys)
        provenance['artefact_source'] = 'DETERMINISTIC'
        warn('Model response for "%s" failed its schema (%s) — computing instead'
             % (prompt_name, problem))
        return fallback(), provenance

    provenance['mode'] = MODEL_GATEWAY
    provenance['artefact_source'] = 'MODEL'
    info('Artefact for "%s" authored by the model and schema-valid' % prompt_name)
    return answer, provenance


def produce_many(context, prompt_name, items, fallback, *, schema_name=None, skill=None):
    """One call per item, each with its own fallback, merged by the caller.

    This is how reading source fits inside the gateway's 16,384-token response
    limit: a request per ``.msgflow`` or ``.esql`` file rather than one per
    repository. It also bounds the damage -- a file the model cannot read
    degrades to the deterministic parse of *that file*, leaving the rest of a
    500-file repository model-authored instead of failing the lot.

    ``items`` is a sequence of ``(key, payload)``. ``fallback`` takes the key.
    Returns ``(dict keyed as given, provenance)``, with the per-key source
    recorded under ``sources``.
    """
    info, _ = _log(context)
    produced, sources, reasons = {}, {}, {}
    for key, payload in items:
        artefact, provenance = produce(context, prompt_name, payload,
                                       lambda k=key: fallback(k),
                                       schema_name=schema_name, skill=skill)
        produced[key] = artefact
        sources[key] = provenance.get('artefact_source', 'DETERMINISTIC')
        if provenance.get('reason'):
            reasons[key] = provenance['reason']
        last = provenance
    if not produced:
        return {}, {'mode': DETERMINISTIC, 'prompt_name': prompt_name,
                    'reason': 'no_items_to_produce', 'artefact_source': 'DETERMINISTIC',
                    'schema': schema_name or prompt_name, 'sources': {}}
    from_model = [k for k, v in sources.items() if v == 'MODEL']
    # Carry the last call's prompt/model/skill identifiers: every call in the
    # batch used the same approved prompt and model, so they describe the batch.
    provenance = dict(last)
    provenance.update({
        'sources': sources, 'item_count': len(produced),
        'items_from_model': len(from_model),
        'items_from_fallback': len(produced) - len(from_model),
    })
    if not from_model:
        provenance['mode'] = DETERMINISTIC
        provenance['artefact_source'] = 'DETERMINISTIC'
    elif len(from_model) == len(produced):
        provenance['mode'] = MODEL_GATEWAY
        provenance['artefact_source'] = 'MODEL'
        provenance.pop('reason', None)
    else:
        provenance['mode'] = PARTIAL
        provenance['artefact_source'] = 'MIXED'
        provenance['reason'] = 'partial_model_authorship:' + ','.join(
            '%s=%s' % (k, v) for k, v in sorted(reasons.items())[:6])
    info('Produced %d artefact(s) for "%s": %d from the model, %d computed'
         % (len(produced), prompt_name, len(from_model), len(produced) - len(from_model)))
    return produced, provenance
