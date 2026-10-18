"""Model-authored artefacts. There is no deterministic substitute.

Agents used to call ``ReasoningGateway.reason()`` and discard the answer into a
metrics counter while deterministic Python built the artefact a gate actually
approved. The previous pass inverted that but kept the Python path as a recorded
fallback. This module removes the fallback.

**Why it had to go.** A recorded fallback is a *successful* run: the step
completes, the gate approves a computed artefact, and the pipeline reads as
healthy. For an entire session every agent ran that path because the gateway
token was invalid, and nothing anywhere said the factory was not using Claude.
A substitute that silently replaces the thing being measured does not degrade
gracefully -- it conceals an outage.

So when the model cannot produce the artefact, ``produce`` raises
``ArtefactUnavailable``. ``AgentRegistry.dispatch`` records AGENT_RUN_FAILED and
re-raises, the pipeline step goes FAILED, and strict fail-fast halts the run.
That is what an outage is.

**Why a retry, and only for some reasons.** Failing hard on the first attempt
would mean one truncated response, or one 401 arriving mid-pipeline, ends a
migration that has already spent an hour. So a reason that could plausibly
answer differently next time is retried up to ``ATTEMPTS``, and a schema failure
carries the violation back to the model as a correction so it gets to fix its
own shape rather than be asked the identical question again. A reason that will
repeat identically -- a DLP refusal on the same payload, an unapproved prompt,
no configured provider -- fails on the first attempt, because spending two more
model calls on a certainty is just a slower failure.

What this module still does not do is decide anything by model. Hashing, the
audit chain, RBAC, the tool allow-list, DLP, path confinement and the
independent pass/fail verdicts (compile, quality gate, differential comparison)
stay in Python: the first group is arithmetic a model cannot perform, the second
is enforcement that ACE source text must not be able to argue its way past, and
the third is the evidence that model output was correct in the first place.
Removing the fallback does not move that boundary -- those were never fallbacks.
"""
import json
import os
import time
from pathlib import Path

from jsonschema import Draft202012Validator

from app.agents.reasoning import MODEL_GATEWAY, NOT_CONSULTED, ReasoningGateway
from app.services.feedback import redact

SCHEMA_FILE = Path(__file__).resolve().parents[2] / 'config' / 'agent-response-schemas.v1.json'

#: Ceiling on one payload's serialised size. A whole ACE repository in a single
#: request is both refused and useless; ``produce_many`` exists to split the
#: work per source file instead of pushing this number up.
MAX_PAYLOAD_CHARS = 180_000

#: How many times one artefact is asked for before the agent fails.
ATTEMPTS = 3

#: Reason prefixes a second attempt could plausibly answer differently: a
#: network blip, an expired token refreshed between calls, a truncated or
#: unparseable answer, and a response whose shape was wrong -- that last one
#: gets the violation handed back to it, so attempt two is a better-informed
#: question rather than a repeat of the first.
RETRYABLE = ('provider_execution_failed', 'model_response_schema_invalid')

#: Reason prefixes guaranteed to repeat. The same payload trips the same DLP
#: rule; an unapproved prompt stays unapproved; a missing provider stays
#: missing. These fail on the first attempt so the error names the real blocker
#: instead of arriving three model calls later.
TERMINAL = ('no_approved_prompt_or_model', 'no_provider_execution_adapter',
            'gateway_refused', 'provider_alias_unavailable', 'payload_too_large')

#: Substrings that identify a credential failure in a provider's error text.
#:
#: A 401 is worth retrying once -- the CVS gateway rejects a valid key
#: intermittently, measured at roughly one call in thirty-seven. But a 401 on
#: *every* attempt is a different fact: the credential is dead, not flaky, and
#: no further attempt will fix it. Observed exactly that way -- two source files
#: authored successfully at 07:34 and 07:35, then every call from 07:36:03
#: onward returning "Invalid token" across a 47-second retry window, because
#: the token expired mid-run and ``get_cvs_api_key`` kept re-fetching the same
#: expired one.
#:
#: The retry is still spent, because the first 401 is indistinguishable from the
#: transient kind. What changes is the error: it names the credential as the
#: cause and says what to do, instead of handing the operator the raw SDK text
#: three times over.
AUTH_MARKERS = ('authenticationerror', '401', 'invalid token', 'unauthorized',
                'authorization header missing', 'api_key_unavailable')


def _is_auth_failure(reason):
    text = str(reason or '').lower()
    return any(marker in text for marker in AUTH_MARKERS)


#: Seconds to wait before the 2nd and 3rd attempt at a *provider* failure.
#:
#: Observed against the live CVS gateway: a valid key is intermittently rejected
#: with 401 "Invalid token", and calls either side of the failure succeed with
#: the same key fingerprint. Three attempts fired back-to-back inside a few
#: hundred milliseconds all landed in the same window and the run failed anyway,
#: which makes the retry decorative for the one failure it most needs to absorb.
#:
#: Deliberately not applied to a schema violation. That failure is in the
#: model's output, not in time -- waiting changes nothing, and the correction is
#: what makes the next attempt different. Sleeping there would only slow every
#: repair down.
#:
#: ``MOJO_RETRY_BACKOFF_SECONDS`` overrides it as a comma-separated list; the
#: test suite sets it to zeros so the retry paths stay fast to exercise.
BACKOFF_SECONDS = (1.0, 3.0)


def backoff(attempt):
    """Seconds to wait before ``attempt`` (1-based). 0 for the first."""
    raw = os.environ.get('MOJO_RETRY_BACKOFF_SECONDS')
    delays = BACKOFF_SECONDS
    if raw is not None:
        try:
            delays = tuple(float(x) for x in raw.split(',') if x.strip() != '')
        except ValueError:
            delays = BACKOFF_SECONDS
    index = attempt - 2
    if index < 0 or not delays:
        return 0.0
    return float(delays[min(index, len(delays) - 1)])

_schemas = None


class StepCancelled(RuntimeError):
    """Someone asked this run to stop, and it stopped at a safe point.

    Not a failure and not an outage: the operator cancelled the pipeline. It is
    a distinct exception from ``ArtefactUnavailable`` precisely so the worker
    can tell "nobody wants this any more" from "the model could not answer" --
    the first is a CANCELLED step and the second is a FAILED one, and recording
    a cancellation as a failure would put a fault on the record where there was
    a decision.

    Whatever the run had produced so far is discarded. ``produce_many`` is
    all-or-nothing, so there is no valid partial artefact to keep: a semantic
    model covering 15 of 17 source files cannot be told from a complete one by
    anything downstream, which is the same reason ``PARTIAL_MODEL_GATEWAY`` was
    removed.
    """

    def __init__(self, prompt_name, produced=0, total=0):
        self.prompt_name = prompt_name
        self.produced = produced
        self.total = total
        super().__init__('step_cancelled:%s:%d_of_%d_produced_and_discarded' % (
            prompt_name, produced, total))


class ArtefactUnavailable(RuntimeError):
    """The model could not produce this artefact, and nothing may stand in.

    Carries the prompt, the reason and how many attempts were made so the run's
    ``error`` column names all three. ``provenance`` holds whatever the gateway
    resolved before it failed -- the prompt and model versions, the request id,
    the skill hash -- which is what makes a failed run diagnosable after the
    fact rather than just "the model did not answer".
    """

    def __init__(self, prompt_name, reason, attempts, provenance=None):
        self.prompt_name = prompt_name
        self.reason = reason
        self.attempts = attempts
        self.provenance = dict(provenance or {})
        super().__init__('artefact_unavailable:%s:%d_attempt%s:%s' % (
            prompt_name, attempts, '' if attempts == 1 else 's', reason))


def schemas():
    """The response schema per prompt name, loaded once."""
    global _schemas
    if _schemas is None:
        _schemas = json.loads(SCHEMA_FILE.read_text(encoding='utf-8'))['schemas']
    return _schemas


def schema_for(name):
    return schemas().get(name)


def retryable(reason):
    """Whether ``reason`` is worth another attempt.

    Matched on the prefix before the first colon, because every reason the
    gateway produces is ``<name>:<detail>``. An unrecognised reason is treated
    as terminal: it is a path nobody anticipated, and failing with a precise
    name beats spending the full budget discovering the same thing.
    ``tests/agents/test_artefact_retry.py`` asserts every reason the gateway can
    actually emit is classified here, so that default never fires in practice.
    """
    text = str(reason or '')
    # A reply cut off at max_tokens is the one provider failure that repeats:
    # the request is unchanged, so the model writes the same length again.
    # Observed twice (the specification, then the mapping catalogue), three
    # attempts of about ten minutes each and the same result every time. The
    # fix is a smaller request or a more compact answer, never another try.
    if 'provider_response_truncated' in text:
        return False
    return text.split(':', 1)[0] in RETRYABLE


def scrub(value):
    """Redact a payload in place of shape, not of structure.

    ``AIRegistryService.gateway_request`` refuses the whole request when its
    DLP rules match, and ``CREDENTIAL_ASSIGNMENT`` matches *any* ``password:``
    line -- including one correctly externalised as ``${PASSWORD}``. ESQL and
    Spring configuration carry those constantly, so sending source verbatim
    means every run is a refusal (``repository_intake`` says exactly this in a
    comment). Values are blanked and the structure the model reasons about
    survives.

    This matters more now than it did: a DLP refusal used to degrade to the
    deterministic path, and now it fails the agent. Scrubbing is what keeps a
    legitimate ``password:`` line in an ESQL file from ending a migration.

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
    return '%s: %s' % (where, _describe(first))


def _describe(error):
    """The rule that failed, in words, rather than the value that failed it.

    jsonschema's own message for a size rule is the whole offending value
    followed by "is too long". Cut to a few hundred characters, that left
    nothing but the value -- the error read "risks: [{'category': 'BLOCKING',
    ..." with the rule lost, so neither the operator nor the correction sent
    back to the model said what to change, and all three attempts failed the
    same way.
    """
    rule, limit, value = error.validator, error.validator_value, error.instance
    try:
        if rule == 'maxItems':
            return 'has %d items; at most %s allowed' % (len(value), limit)
        if rule == 'minItems':
            return 'has %d items; at least %s required' % (len(value), limit)
        if rule == 'maxLength':
            return 'is %d characters; at most %s allowed' % (len(value), limit)
        if rule == 'minLength':
            return 'is %d characters; at least %s required' % (len(value), limit)
    except TypeError:
        pass
    return error.message[:160]


def _log(context):
    logger = getattr(context, 'logger', None)
    return (logger.info if logger else (lambda m: None),
            logger.warn if logger else (lambda m: None))


def _stopping(context):
    """Whether this run has been cancelled. False when nothing supplied a check.

    Deliberately forgiving: a context without ``cancelled`` -- a direct
    ``run()``, an older caller, a test double -- is simply never cancelled. A
    predicate that raises is treated the same way rather than failing the agent,
    because a broken cancellation check must not be able to stop work that is
    otherwise fine.
    """
    check = getattr(context, 'cancelled', None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False


def _correction(problem, answer):
    """What to tell the model about the response it just got wrong.

    The keys it did return are included because a shape failure is usually a
    wrapper -- the artefact nested under ``result``, or prose around it -- and
    naming what arrived is what lets the model see the difference between what
    it sent and what was asked for.
    """
    keys = sorted(answer)[:12] if isinstance(answer, dict) else type(answer).__name__
    return ('The previous response was rejected and not used. Schema violation: '
            '%s. It contained these top-level keys: %s. Return a single JSON '
            'object satisfying the schema exactly, with no wrapper key and no '
            'prose around it.' % (problem, keys))


def _diagnose(reason, failures, budget):
    """The reason, plus what repeating it across every attempt tells us.

    A single 401 is ambiguous: the gateway rejects a valid key often enough that
    the first one is probably transient. A 401 on every attempt is not
    ambiguous, and the operator needs to be told the difference -- the raw SDK
    text repeated three times reads as a flaky call, when what happened is that
    the credential died and no retry could have helped.

    Only ever adds to the message. The reason keeps its prefix, so
    ``retryable``, ``TERMINAL`` and every test that matches on it are unaffected.
    """
    if len(failures) < max(2, budget) or not all(_is_auth_failure(f) for f in failures):
        return reason
    return ('%s [all %d attempts failed authentication: the gateway credential '
            'is rejected, not flaky. get_cvs_api_key() re-reads it on every '
            'attempt, so retrying cannot help -- re-authenticate (cvscode auth) '
            'and run the step again.]' % (reason, len(failures)))


def produce(context, prompt_name, payload, *, schema_name=None, skill=None,
            attempts=ATTEMPTS, validator=None):
    """The artefact, authored by the model. No substitute, no silent degradation.

    Returns ``(artefact, provenance)``. ``provenance['attempts']`` says how many
    requests it took and ``provenance['corrections']`` carries what the model
    was told when its first shape was wrong, so a one-attempt artefact and a
    repaired one are distinguishable downstream.

    Raises ``ArtefactUnavailable`` when the model cannot answer -- which fails
    the agent and halts the pipeline. Nothing here returns a computed artefact.

    ``validator`` is an optional second check, taking the artefact and returning
    ``''`` or a description of what is wrong. It exists for a constraint the
    response schema cannot express: ``migration-specification`` must satisfy
    ``schemas/migration-specification.schema.json``, which is a different
    document with its own rules. A failure there travels the same retryable path
    as a schema failure, so the model is re-asked with the violation instead of
    the agent giving up on an answer it could plausibly correct.
    """
    info, warn = _log(context)
    schema_name = schema_name or prompt_name
    cleared = scrub(payload)
    serialised = json.dumps(cleared, sort_keys=True, default=str)
    if len(serialised) > MAX_PAYLOAD_CHARS:
        # Terminal by construction: the payload does not get smaller by asking
        # again. The caller's job is to split it -- that is what produce_many is.
        reason = 'payload_too_large:%d_chars_over_%d' % (len(serialised), MAX_PAYLOAD_CHARS)
        warn('Payload for "%s" is %d chars — over the %d limit; cannot ask the model'
             % (prompt_name, len(serialised), MAX_PAYLOAD_CHARS))
        raise ArtefactUnavailable(prompt_name, reason, 0,
                                  {'prompt_name': prompt_name, 'schema': schema_name})
    gateway = ReasoningGateway(context.repo)
    budget = max(1, int(attempts or 1))
    corrections, provenance = [], {'prompt_name': prompt_name, 'schema': schema_name}
    failures = []
    for attempt in range(1, budget + 1):
        # Checked before each attempt, not only before the first: a retry with
        # its backoff can add seconds after the operator pressed Stop, and
        # spending another model call on work nobody wants is the thing this
        # exists to prevent.
        if _stopping(context):
            raise StepCancelled(prompt_name)
        # Logged before the call, with the attempt number. Three identical
        # "LLM invoking provider" lines are indistinguishable from one, which is
        # how a run that retried correctly got read as a run that never
        # retried -- the only clue was a count in the final error.
        if attempt > 1:
            info('Retrying "%s" — attempt %d of %d' % (prompt_name, attempt, budget))
        # The correction rides in the payload rather than the template: the
        # template is the approved prompt and must not be rewritten per attempt.
        request = dict(cleared, _correction=corrections[-1]) if corrections else cleared
        answer, provenance = gateway.reason(context, prompt_name, request, skill=skill)
        provenance.update({'schema': schema_name, 'attempts': attempt})
        if corrections:
            provenance['corrections'] = list(corrections)
        if answer is None:
            # reason() already recorded and logged why.
            reason = provenance.get('reason') or 'model_unavailable'
            failures.append(reason)
            if not retryable(reason) or attempt >= budget:
                raise ArtefactUnavailable(prompt_name, _diagnose(reason, failures, budget),
                                          attempt, provenance)
            # A provider failure is temporal, so the next attempt waits. The
            # live gateway rejects a valid key intermittently, and retrying
            # inside the same few hundred milliseconds just fails three times.
            pause = backoff(attempt + 1)
            warn('Model could not answer "%s" (%s) — attempt %d of %d%s'
                 % (prompt_name, reason, attempt, budget,
                    ', waiting %.1fs' % pause if pause else ''))
            if pause:
                time.sleep(pause)
            continue
        problem = validate(answer, schema_name)
        if not problem and validator is not None:
            problem = validator(answer) or ''
        if not problem:
            provenance['mode'] = MODEL_GATEWAY
            provenance['artefact_source'] = 'MODEL'
            info('Artefact for "%s" authored by the model and schema-valid%s'
                 % (prompt_name, '' if attempt == 1 else ' on attempt %d' % attempt))
            return answer, provenance
        # A response that does not match its schema is treated exactly like no
        # response: it is about to be hashed and bound to a gate approval. The
        # keys it did return are named because complete_request persists only a
        # truncated body, and without them a schema failure is undiagnosable.
        keys = sorted(answer)[:12] if isinstance(answer, dict) else type(answer).__name__
        reason = 'model_response_schema_invalid:%s; response keys: %s' % (problem, keys)
        provenance['reason'] = reason
        if attempt >= budget:
            raise ArtefactUnavailable(prompt_name, reason, attempt, provenance)
        corrections.append(_correction(problem, answer))
        warn('Model response for "%s" failed its schema (%s) — re-asking with the '
             'violation, attempt %d of %d' % (prompt_name, problem, attempt, budget))
    # Unreachable: the loop either returns or raises on its final attempt. Kept
    # so a future edit to the loop bounds cannot fall through into returning None.
    raise ArtefactUnavailable(prompt_name, provenance.get('reason') or 'model_unavailable',
                              budget, provenance)


def produce_many(context, prompt_name, items, *, schema_name=None, skill=None,
                 attempts=ATTEMPTS, validator=None):
    """One request per item, all of them or none.

    This is how reading source stays inside the gateway's response limit: a
    request per ``.msgflow`` or ``.esql`` file rather than one per repository.

    All-or-nothing, where it used to degrade per item to a partial artefact.
    ``code_generation`` already refused partial authorship -- "a project missing
    its parent POM does not build" -- and a half-authored semantic model is the
    same problem one layer up: the next agent cannot tell which flows are
    missing from one that genuinely has none. So any item the model cannot
    produce fails the agent, and ``PARTIAL_MODEL_GATEWAY`` no longer exists.

    ``items`` is a sequence of ``(key, payload)``. Returns
    ``(dict keyed as given, provenance)``.

    ``validator``, if given, is called as ``validator(key, answer)`` and travels
    the same retry-with-correction path as ``produce``'s.
    """
    info, warn = _log(context)
    produced, provenance = {}, {}
    pending = list(items)
    for index, (key, payload) in enumerate(pending):
        # The checkpoint that matters. This loop is where a long agent spends
        # its time -- ace-analysis sends one request per source file and a real
        # repository takes half an hour -- so checking between items is what
        # makes cancelling a pipeline reach an agent already in flight. It lands
        # within one model call, because the call itself is not interrupted.
        if _stopping(context):
            warn('Cancelled after %d of %d artefact(s) for "%s" — discarding them'
                 % (index, len(pending), prompt_name))
            raise StepCancelled(prompt_name, index, len(pending))
        artefact, provenance = produce(context, prompt_name, payload,
                                       schema_name=schema_name, skill=skill,
                                       attempts=attempts,
                                       validator=(lambda a, k=key: validator(k, a)) if validator else None)
        produced[key] = artefact
    if not produced:
        # Not a failure. Nothing was asked because there was nothing to ask
        # about -- an ACE repository with no message flows in it. The caller
        # decides whether an empty result is acceptable for its artefact; that
        # is a domain question, not this module's.
        return {}, {'mode': NOT_CONSULTED, 'prompt_name': prompt_name,
                    'reason': 'no_items_to_produce', 'artefact_source': 'NONE',
                    'schema': schema_name or prompt_name, 'item_count': 0}
    # The last call's identifiers describe the batch: every request used the
    # same approved prompt, model and skill.
    provenance = dict(provenance)
    provenance['item_count'] = len(produced)
    info('Produced %d artefact(s) for "%s", all model-authored'
         % (len(produced), prompt_name))
    return produced, provenance
