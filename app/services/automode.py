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
"""
import os

#: Recorded as ``clarifications.answered_by``. Deliberately not an email: it has
#: to be obvious in the audit trail that nobody reviewed this, and a
#: human-looking identity would hide exactly that.
ACTOR = 'AUTO_MODE'

#: Confidences auto mode accepts. All three, by instruction.
#:
#: LOW is the interesting one. ``agent/clarification/suggested-answers`` tells
#: the model to answer LOW and say so plainly when a question needs something
#: only a human with live-system access can supply -- a credential, an
#: environment URL, a business decision. Accepting LOW therefore accepts some
#: answers that amount to "this needs a person", which is why the confidence is
#: stored per decision and surfaced rather than averaged away: the LOW list is
#: the review queue, and the gate is where it gets reviewed.
ACCEPTED_CONFIDENCE = ('HIGH', 'MEDIUM', 'LOW', '')


def enabled():
    """Whether auto mode is on. Global, and on unless switched off.

    ``FACTORY_AUTO_MODE=0`` restores the behaviour where every question waits
    for a human -- which is the right setting for a migration under review,
    and the reason this is a switch rather than a rewrite.
    """
    return (os.getenv('FACTORY_AUTO_MODE', '1') or '').strip().lower() not in (
        '0', 'false', 'no', 'off')


def accepts(confidence):
    """Whether a suggestion of this confidence may be accepted automatically."""
    return str(confidence or '').strip().upper() in ACCEPTED_CONFIDENCE


def blocks(status):
    """Whether an agent result stops its step regardless of auto mode.

    The agent's own status is the authority. An agent that produced its
    artefact reports COMPLETED in auto mode and its questions become recorded
    decisions; one that produced nothing keeps reporting
    CLARIFICATION_REQUIRED, and its questions stay open because only a person
    can change that outcome.
    """
    return str(status or '').upper() == 'CLARIFICATION_REQUIRED'
