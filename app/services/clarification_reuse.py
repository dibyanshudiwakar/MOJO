"""Answering a question once.

Every re-run of an agent raises its questions again, and the model words them a
little differently each time. One job held the same four questions 4, 4, 3 and 2
times over, each copy waiting on its own answer -- a person answering the same
thing repeatedly because nothing remembered that they had.

``PriorAnswers`` finds an earlier answer to the same question so it can be used
instead of asked for again. Matching is deliberately narrow, because reusing the
wrong answer is worse than asking twice:

* the question has to match once case, spacing and punctuation are ignored, or
  be a close rewording (``SIMILARITY``) *within the same stage*;
* only answered questions with a real answer count;
* the same job is searched first, then other jobs of the same service -- a
  re-created job for one service raises the same questions as the last one;
* an answer a person gave beats one the model gave, and newer beats older.

A reused answer keeps a pointer to the one it came from (``reused_from``), so
the chain back to a human decision is never lost.
"""
import re
from difflib import SequenceMatcher

from app.services import automode

#: Marks an answer taken from an earlier question rather than given afresh.
ACTOR = 'REUSED_ANSWER'

#: How alike two wordings must be (0-1) to count as the same question. High on
#: purpose: the two rewordings seen in practice ("...error bodies and statuses
#: that consumers..." / "...error bodies that consumers parse...") are about
#: this close, and a lower threshold starts matching different questions about
#: the same construct.
SIMILARITY = 0.85

#: Answers considered, newest first. A bound, not a policy.
LIMIT = 3000


def normalise(text):
    """Case, spacing and punctuation removed: what two phrasings must share."""
    return re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).strip()


class PriorAnswers:
    """The earlier answers relevant to one job, loaded once per dispatch."""

    def __init__(self, repo, job_id):
        job = repo.one('select service from jobs where id=?', (job_id,)) or {}
        service = job.get('service') or ''
        rows = repo.rows(
            "select c.id,c.job_id,c.stage,c.question,c.answer,c.answered_by,c.answered_at,c.reused_from"
            " from clarifications c join jobs j on j.id=c.job_id"
            " where c.status='ANSWERED' and trim(c.answer)<>''"
            " and (c.job_id=? or (?<>'' and j.service=?))"
            " order by c.answered_at desc limit ?", (job_id, service, service, LIMIT))
        # Best first, so the first match found is the best one: same job, then a
        # person's answer over a machine's, then the newest (already the order).
        def rank(row):
            by = str(row.get('answered_by') or '')
            return (0 if row['job_id'] == job_id else 1,
                    1 if by in (automode.ACTOR, ACTOR) else 0)
        self._rows = sorted(rows, key=rank)
        self._norm = [normalise(r['question']) for r in self._rows]

    def match(self, stage, question):
        """The best earlier answer to this question, or ``None``."""
        wanted = normalise(question)
        if not wanted or not self._rows:
            return None
        for row, norm in zip(self._rows, self._norm):
            if norm == wanted:
                return row
        for row, norm in zip(self._rows, self._norm):
            if row['stage'] == stage and SequenceMatcher(None, wanted, norm).ratio() >= SIMILARITY:
                return row
        return None


def origin(row):
    """The first answer in a chain of reuses."""
    return row.get('reused_from') or row['id']
