"""Auto mode: the agent decides, instead of stopping to ask.

An ``ace-analysis`` of one real application raised 95 clarifications, and a
clarification parks its step. So a migration stopped on 95 questions before
anything downstream could run, and most of them were the sort of thing the
skill already answers.

In auto mode an agent whose artefact is *complete* resolves its own questions
through the model and records each decision, rather than parking. The decision
is not hidden: every question becomes an ANSWERED clarification attributed to
``AUTO_MODE``, carrying the model's answer, its confidence and its caveat, so a
reviewer reads a list of decisions instead of a list of questions.

**What this is not.** It does not touch the G0-G5 gates. A clarification is an
input to the work; a gate is approval of the result, role-bound and bound to an
evidence hash. Auto mode makes the pipeline flow to the gate; the gate is still
where a person signs. That is also what bounds the risk of the threshold below:
every artefact these decisions shaped still needs human sign-off before it
ships.

**Why only a complete artefact.** Two facts about this codebase make the
obvious implementation an infinite loop. No agent reads ``clarifications.answer``
-- nothing consumes an answer to change its behaviour -- and
``resume_clarification_steps`` resumes a parked step by *re-running the agent*.
So answering the questions of an agent that produced nothing makes it run
again, raise the same questions, park, get answered, run again. Each cycle of
``ace-analysis`` is seventeen Opus calls and half an hour.

So the rule is: auto mode answers the questions of an agent that is going on
anyway, and leaves alone the questions of an agent that stopped. ``planning``
raising clarifications means it produced no plan, and no automatic answer can
conjure one -- a person has to decide. That is the "minimal" in "zero to
minimal".

**What reaches a person.** Not everything auto mode could decide is decided:
a question the model rates CRITICAL severity and is not confident about stays OPEN
(``needs_person``). Those are asked, and they do not hold the step -- the step
completed with the model's suggestion attached, and the open question is the
record that a person should confirm it before the gate. ``planning`` follows the
same rule for constructs with no approved pattern: a gap the model rates below
CRITICAL is planned around and recorded as an accepted risk in the register and
the G3 package; a CRITICAL one stops the plan and is asked.
"""
import os

#: Recorded as ``clarifications.answered_by``. Deliberately not an email: it has
#: to be obvious in the audit trail that nobody reviewed this, and a
#: human-looking identity would hide exactly that.
ACTOR = 'AUTO_MODE'

#: Severity at which a question can be put to a person. The model rates each
#: question by what a wrong answer would cost (``suggestion_severity``). It is
#: CRITICAL, above HIGH: at HIGH the model rated 59 of 104 questions from one
#: analysis for a person, which is most of the analysis, not the exception.
ASK_SEVERITY = 'CRITICAL'
#: Confidence that settles a high-severity question without asking. Anything
#: less leaves it open.
SETTLED_CONFIDENCE = 'HIGH'
#: Every confidence is recorded with the decision (the LOW ones are the review
#: queue) but only the combination below reaches a person.
KNOWN_CONFIDENCE = ('HIGH', 'MEDIUM', 'LOW', '')


def enabled():
    """Whether auto mode is on. Global, and on unless switched off.

    ``FACTORY_AUTO_MODE=0`` restores the behaviour where every question waits
    for a human -- which is the right setting for a migration under review,
    and the reason this is a switch rather than a rewrite.
    """
    return (os.getenv('FACTORY_AUTO_MODE', '1') or '').strip().lower() not in (
        '0', 'false', 'no', 'off')


def needs_person(severity, confidence):
    """Whether a question is put to a person rather than decided by the model.

    Only when a wrong answer would be catastrophic (CRITICAL severity) *and* the model is
    not sure of its own (confidence below HIGH). A confident answer to a
    critical question is still a decision the model can make; an unsure
    answer to a low-stakes one is not worth stopping anyone for. Everything
    else is decided and recorded, with severity and confidence, for the gate.

    An unrated severity is treated as not high: the response schema requires a
    rating, so this only arises for rows written before it existed.
    """
    return (str(severity or '').strip().upper() == ASK_SEVERITY
            and str(confidence or '').strip().upper() != SETTLED_CONFIDENCE)


def accepts(confidence, severity=''):
    """Whether a suggestion may be accepted automatically.

    False for an unrecognised confidence (a malformed answer is not a decision)
    and for a question ``needs_person`` says to ask.
    """
    if str(confidence or '').strip().upper() not in KNOWN_CONFIDENCE:
        return False
    return not needs_person(severity, confidence)


def blocks(status):
    """Whether an agent result stops its step regardless of auto mode.

    The agent's own status is the authority. An agent that produced its
    artefact reports COMPLETED in auto mode and its questions become recorded
    decisions; one that produced nothing keeps reporting
    CLARIFICATION_REQUIRED, and its questions stay open because only a person
    can change that outcome.
    """
    return str(status or '').upper() == 'CLARIFICATION_REQUIRED'
