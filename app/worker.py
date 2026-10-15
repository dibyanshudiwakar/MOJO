"""Background worker: drains ``workflow_queue`` for pipeline steps.

Before this, nothing in the codebase executed anything without an inbound
HTTP request -- every agent dispatch ran synchronously inside the request
that triggered it (``AgentRegistry.dispatch``, called straight from
``app/server.py``), and ``workflow_queue``'s claim/heartbeat/complete
primitives (``WorkflowOrchestrator``) existed but nothing called ``claim()``
except an explicit client request. This is what lets a migration pipeline
keep running once the browser tab that started it is closed: a
claim -> execute -> advance loop over that same queue, driven by a thread
that is not tied to any particular request.

Only ever claims the ``'pipeline_step'`` activity (see the ``activity``
argument on ``WorkflowOrchestrator.claim``), so it cannot steal a queue row an
external worker scheduled for its own purpose through the pre-existing
``/api/v3/workflow/*`` routes.

Runs as a daemon thread inside ``app/server.py``'s process by default. Also
directly runnable as its own process against the same ``FACTORY_DB``
(``python -m app.worker``), for anyone who later wants it separated out --
same code path either way, just a second entry point.
"""
import json
import os
import sys
import time
import traceback

from app.repository import Repository
from app.services.accounts import AccountService
from app.services.agents import AgentRegistry
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService, StepLogger, PipelineContextAccumulator
from app.services.repo_clone import RepoCloneService
from app.services.pipelines import AGENT_LIFECYCLE_TRANSITIONS
from app.agents.skills import skill_for, SkillIntegrityError

ACTIVITY = 'pipeline_step'

# Gates that must be approved BEFORE the agent runs.  These agents' own
# services check for artefact-bound approvals internally and refuse to
# execute without them, so running them without the gate just wastes an
# attempt and produces a FAILED → WAITING_APPROVAL transition.  Pre-checking
# here parks the step immediately as WAITING_APPROVAL without executing the
# agent at all.
#
# G1 (framework) is NOT listed here because framework-baseline is optional.
# When no framework was used, G1 is skipped entirely and planning/code-gen
# proceed without it.  The G1 check in the services is conditional on
# framework_id being present.
AGENT_PREFLIGHT_GATES = {
    'planning':                ['G2'],
    'code-generation':         ['G3'],
}


class WorkerLoop:
    def __init__(self, repo, agents=None, pipelines=None, repo_clone=None, workflow=None,
                worker_id=None, poll_seconds=2):
        self.repo = repo
        self.agents = agents or AgentRegistry(repo)
        self.pipelines = pipelines or PipelineService(repo)
        self.repo_clone = repo_clone or RepoCloneService(repo)
        self.workflow = workflow or WorkflowOrchestrator(repo)
        self.worker_id = worker_id or 'pipeline-worker'
        self.poll_seconds = poll_seconds
        self._stop = False

    def stop(self):
        self._stop = True

    def run_forever(self):
        while not self._stop:
            if not self.process_one():
                time.sleep(self.poll_seconds)

    def process_one(self):
        """Claim and execute exactly one queued pipeline step, if any is ready.

        Returns True if a step was claimed (whether it then succeeded or
        failed) so ``run_forever`` polls again immediately, False if nothing
        was ready so it backs off. Exposed separately from ``run_forever`` so
        tests can single-step the loop deterministically.
        """
        item = self.workflow.claim(self.worker_id, lease_seconds=120, activity=ACTIVITY)
        if not item:
            return False
        payload = json.loads(item['payload'] or '{}')
        step_id = payload['step_id']
        pipeline_id = payload['pipeline_id']
        try:
            self._execute(pipeline_id, step_id)
            # The step may have been parked at WAITING_APPROVAL by the
            # pre-flight gate check rather than completing normally.  In that
            # case the queue row should be cancelled (not completed) so the
            # retry backoff does not re-run the step behind the operator's
            # back.  ``_enter_gate_wait`` already parks the queue row via
            # ``_park_queue_row``, but ``complete`` below would overwrite that,
            # so detect the park and skip the normal completion.
            step = self.repo.one('select status from pipeline_steps where id=?', (step_id,))
            if step and step['status'] == 'WAITING_APPROVAL':
                # The queue row was already parked by _enter_gate_wait; resolve
                # it as cancelled so it stays dormant until the gate is approved.
                try:
                    self.workflow.complete(item['id'], False, 'gate_hold', worker_id=self.worker_id)
                except Exception:
                    pass
            else:
                self.workflow.complete(item['id'], True, worker_id=self.worker_id)
        except Exception as exc:
            # A bug here (not in the step itself -- _execute already catches
            # and records step-level failures) must still resolve the queue
            # row, or it retries forever with no visible record of why.
            #
            # Only record the failure if _execute did not already: it marks the
            # step FAILED and re-raises, so recording it again here used to
            # overwrite a step that _check_gate_block had just parked at
            # WAITING_APPROVAL, and a gate-blocked step ended FAILED with the
            # hold silently lost. mark_step now refuses to overwrite a parked
            # step as well, so this is the belt to that braces.
            traceback.print_exc()
            try:
                step = self.repo.one('select status from pipeline_steps where id=?', (step_id,))
                if not step or step['status'] not in ('FAILED', 'WAITING_APPROVAL'):
                    self.pipelines.mark_step(step_id, 'FAILED', error=type(exc).__name__ + ':' + str(exc))
            except Exception:
                pass
            self.workflow.complete(item['id'], False, str(exc), worker_id=self.worker_id)
        self.pipelines.advance(pipeline_id, self.workflow)
        return True

    def _gate_approved(self, job_id, gate):
        """Return True if there is at least one valid approval for *gate* at the current iteration."""
        job = self.repo.one('select iteration from jobs where id=?', (job_id,))
        iteration = int((job or {}).get('iteration') or 1)
        row = self.repo.one(
            'select id from approvals where job_id=? and gate=? and iteration=?',
            (job_id, gate, iteration),
        )
        return bool(row)

    def _preflight_gate_check(self, job_id, step, log=None):
        """Park the step immediately if its agent needs a gate that is not yet approved.

        Returns True if the step was parked (caller should skip execution),
        False if it is clear to proceed.
        """
        if step['kind'] != 'AGENT':
            return False
        required = AGENT_PREFLIGHT_GATES.get(step['agent_id'])
        if not required:
            return False
        for gate in required:
            if not self._gate_approved(job_id, gate):
                if log:
                    log.warn('Gate %s pre-flight: not approved — holding step until gate is signed' % gate)
                error_msg = 'required_gate_approval_missing:%s' % gate
                self.pipelines._enter_gate_wait(
                    step['id'], job_id, '', error_msg, needs_retry=True)
                return True
            else:
                if log:
                    log.info('Gate %s pre-flight: approved ✓' % gate)
        return False

    def _execute(self, pipeline_id, step_id):
        step = self.repo.one('select * from pipeline_steps where id=?', (step_id,))
        if not step:
            raise ValueError('pipeline_step_not_found:' + step_id)
        pipeline = self.repo.one('select * from pipelines where id=?', (pipeline_id,))
        job_id = pipeline['job_id']
        log = StepLogger(self.repo, step_id)
        kind_label = {'CLONE': 'Clone', 'AGENT': 'Agent', 'DELIVERY': 'Delivery'}.get(step['kind'], step['kind'])
        agent_label = step['agent_id'].replace('-', ' ').title() if step['agent_id'] else ''
        log.info('Step started: %s%s' % (kind_label, (' — ' + agent_label) if agent_label else ''))
        self.pipelines.mark_step(step_id, 'RUNNING')
        # Pre-flight: if the agent needs a gate that is not yet approved, park
        # the step immediately as WAITING_APPROVAL without running the agent.
        if self._preflight_gate_check(job_id, step, log):
            return
        try:
            if step['kind'] == 'AGENT':
                outputs = self._dispatch_agent(job_id, step, pipeline['created_by'], log)
            elif step['kind'] == 'CLONE':
                outputs = self._clone(pipeline, step, log)
            elif step['kind'] == 'DELIVERY':
                outputs = self._deliver(pipeline, step, log)
            else:
                raise ValueError('unsupported_pipeline_step_kind:' + step['kind'])
        except Exception as exc:
            log.error('Step failed: %s' % str(exc)[:300])
            self.pipelines.mark_step(step_id, 'FAILED', error=type(exc).__name__ + ':' + str(exc))
            raise
        log.success('Step completed successfully')
        self.pipelines.mark_step(step_id, 'COMPLETED', outputs=outputs)

    def _principal(self, email):
        """The pipeline creator's roles, re-read at dispatch time.

        A step should run with the roles its owner holds *now*, not the roles
        they held when the pipeline was created -- a role granted or revoked
        in between must take effect, matching how a bearer token's role check
        already behaves everywhere else in this system.
        """
        user = self.repo.one('select * from users where email=?', (email,))
        if not user:
            raise PermissionError('pipeline_owner_account_not_found:' + email)
        return user, AccountService.roles_of(user)

    def _dispatch_agent(self, job_id, step, created_by, log=None):
        user, roles = self._principal(created_by)
        if log:
            log.info('Resolved pipeline owner: %s (%d roles)' % (created_by, len(roles)))
        params = self.pipelines.resolved_params(step)
        # Load accumulated pipeline context from all prior agents and merge it
        # into the params so the agent (and its LLM reasoning) sees everything
        # the pipeline has produced so far.
        accumulator = PipelineContextAccumulator(self.repo)
        prior = accumulator.snapshot(step['pipeline_id'], job_id)
        if prior:
            params['_pipeline_context'] = prior
            if log:
                n_agents = len(prior.get('agent_chain', []))
                n_evidence = len(prior.get('evidence_summary', []))
                log.info('Loaded pipeline context: %d prior agent%s, %d evidence item%s' % (
                    n_agents, '' if n_agents == 1 else 's',
                    n_evidence, '' if n_evidence == 1 else 's'))
                if prior.get('agent_chain'):
                    log.info('Prior agents: %s' % ' → '.join(prior['agent_chain']))
        elif log:
            log.info('No prior pipeline context (first agent in this pipeline)')
        if log:
            log.info('Dispatching agent %s v%s' % (step['agent_id'], step['version']))
        result = self.agents.dispatch(job_id, step['agent_id'], step['version'], roles, created_by, params, [], logger=log)
        evidence_ids = [e.get('id') for e in result.get('evidence', [])]
        if log:
            status = result.get('status', 'UNKNOWN')
            n_evidence = len(evidence_ids)
            log.info('Agent returned %s with %d evidence item%s' % (status, n_evidence, '' if n_evidence == 1 else 's'))
            if result.get('error'):
                log.warn('Agent reported error: %s' % str(result['error'])[:200])
            reasoning = result.get('reasoning') or {}
            if reasoning.get('mode'):
                log.info('Reasoning mode: %s' % reasoning['mode'])
        # Record this agent's full context into the shared store so subsequent
        # agents see it.
        skill = None
        try:
            skill = skill_for(step['agent_id'])
        except SkillIntegrityError:
            pass
        accumulator.record(step['pipeline_id'], step['id'], step['agent_id'], job_id, result, skill)
        if log:
            log.info('Pipeline context updated with %s outputs' % step['agent_id'])
        # Everything the agent itself declared as output stays at the top
        # level -- resolved_params() (and any later step that forwards a
        # value from this one) reads outputs[key] directly. Execution
        # metadata the agent did not itself return is namespaced under an
        # underscore so it can never collide with a real output key.
        return {**result.get('outputs', {}), '_run_id': result.get('run_id'),
                '_agent_status': result.get('status'), '_metrics': result.get('metrics'),
                '_evidence_ids': evidence_ids}

    def _clone(self, pipeline, step, log=None):
        params = json.loads(step['params'] or '{}')
        role = params.get('role', '')
        if log:
            log.info('Resolving GitHub credentials for %s repository' % (role or 'source'))
        user, _ = self._principal(pipeline['created_by'])
        if log:
            log.info('Cloning %s @ %s → %s' % (params['repo_url'], params['branch'], params['dest']))
        result = self.repo_clone.clone(user['id'], params['repo_url'], params['branch'], params['dest'])
        self.pipelines.mark_repo_cloned(pipeline['job_id'], role, result['path'], result['commit'])
        if log:
            log.success('Clone complete — commit %s at %s' % (result.get('commit', '?')[:12], result.get('path', '?')))
        return result

    def _deliver(self, pipeline, step, log=None):
        params = json.loads(step['params'] or '{}')
        if log:
            log.info('Pushing generated code to %s @ %s' % (params['repo_url'], params['branch']))
        user, _ = self._principal(pipeline['created_by'])
        result = self.repo_clone.push(user['id'], params['repo_url'], params['branch'], params['source_dir'])
        if log:
            log.success('Push complete — commit %s on branch %s' % (result.get('commit', '?')[:12], result.get('branch', '?')))
        return result


if __name__ == '__main__':
    root = __import__('pathlib').Path(__file__).resolve().parent.parent
    repo = Repository(os.getenv('FACTORY_DB', str(root / 'factory-v2.db')))
    sys.stderr.write('ACE Modernization Accelerator pipeline worker starting (standalone process)\n')
    sys.stderr.flush()
    WorkerLoop(repo, worker_id=os.getenv('WORKER_ID', 'pipeline-worker-standalone')).run_forever()
