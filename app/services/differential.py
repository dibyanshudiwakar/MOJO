"""Persisting facade over the single differential comparator.

This module previously carried a second, weaker comparison implementation. Two
divergent comparators meant the authority for "behavioural equivalence"
(section 2) was ambiguous, and variances landed in two disjoint stores so RCA
could only ever link to half of them (sections 8 and 12).

There is now one comparator -- ``DifferentialEvidenceEngine`` -- and one
variance store, the ``variances`` table. This class persists the engine's
verdict; it does not implement comparison itself.
"""
import json
from app.repository import uid, now, digest
from app.services.enterprise_v5 import DifferentialEvidenceEngine


class DifferentialService:
    def __init__(self, repo):
        self.repo = repo
        self.engine = DifferentialEvidenceEngine()

    @staticmethod
    def _policy(policy):
        """Accept the legacy policy vocabulary and map it onto the engine's.

        The retired comparator used ``ignored_paths``; the engine uses
        ``ignore``. Callers and stored policies using either spelling keep
        working, and the normalised form is what gets hashed into evidence.
        """
        policy = dict(policy or {})
        if 'ignored_paths' in policy:
            merged = list(policy.pop('ignored_paths') or [])
            merged += [x for x in policy.get('ignore', []) if x not in merged]
            policy['ignore'] = merged
        return policy

    def compare(self, job_id, case_id, expected, actual, policy=None):
        policy = self._policy(policy)
        result = self.engine.compare(case_id, expected, actual, policy)
        cid = uid()
        variances = result['variances']
        with self.repo.connect() as c:
            c.execute(
                'insert into comparisons(id,job_id,case_id,policy,expected_hash,actual_hash,status,created_at)'
                ' values(?,?,?,?,?,?,?,?)',
                (cid, job_id, case_id, json.dumps(policy, sort_keys=True),
                 result['expected_hash'], result['actual_hash'], result['status'], now()))
            for v in variances:
                # Severity and kind come from the engine rather than being
                # hardcoded to MEDIUM, so section 11's structural/type/value
                # classification survives into the store RCA reads.
                c.execute(
                    'insert into variances(id,comparison_id,job_id,path,expected,actual,severity,'
                    'disposition,created_at,kind,normalisation) values(?,?,?,?,?,?,?,?,?,?,?)',
                    (uid(), cid, job_id, v['path'], json.dumps(v['expected']), json.dumps(v['actual']),
                     v['severity'], v['disposition'], now(), v['kind'],
                     json.dumps({'policy_hash': result['policy_hash']})))
        return {'comparison_id': cid, 'status': result['status'],
                'variance_count': len(variances), 'variances': variances,
                'content_hash': result['content_hash'],
                'expected_hash': result['expected_hash'], 'actual_hash': result['actual_hash']}
