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
from app.services.pipelines import PipelineService
from app.services.repo_clone import RepoCloneService

ACTIVITY = 'pipeline_step'


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
            self.workflow.complete(item['id'], True, worker_id=self.worker_id)
        except Exception as exc:
            # A bug here (not in the step itself -- _execute already catches
            # and records step-level failures) must still resolve the queue
            # row, or it retries forever with no visible record of why.
            traceback.print_exc()
            try:
                self.pipelines.mark_step(step_id, 'FAILED', error=type(exc).__name__ + ':' + str(exc))
            except Exception:
                pass
            self.workflow.complete(item['id'], False, str(exc), worker_id=self.worker_id)
        self.pipelines.advance(pipeline_id, self.workflow)
        return True

    def _execute(self, pipeline_id, step_id):
        step = self.repo.one('select * from pipeline_steps where id=?', (step_id,))
        if not step:
            raise ValueError('pipeline_step_not_found:' + step_id)
        pipeline = self.repo.one('select * from pipelines where id=?', (pipeline_id,))
        job_id = pipeline['job_id']
        self.pipelines.mark_step(step_id, 'RUNNING')
        try:
            if step['kind'] == 'AGENT':
                outputs = self._dispatch_agent(job_id, step, pipeline['created_by'])
            elif step['kind'] == 'CLONE':
                outputs = self._clone(pipeline, step)
            elif step['kind'] == 'DELIVERY':
                outputs = self._deliver(pipeline, step)
            else:
                raise ValueError('unsupported_pipeline_step_kind:' + step['kind'])
        except Exception as exc:
            self.pipelines.mark_step(step_id, 'FAILED', error=type(exc).__name__ + ':' + str(exc))
            raise
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

    def _dispatch_agent(self, job_id, step, created_by):
        user, roles = self._principal(created_by)
        params = self.pipelines.resolved_params(step)
        result = self.agents.dispatch(job_id, step['agent_id'], step['version'], roles, created_by, params, [])
        # Everything the agent itself declared as output stays at the top
        # level -- resolved_params() (and any later step that forwards a
        # value from this one) reads outputs[key] directly. Execution
        # metadata the agent did not itself return is namespaced under an
        # underscore so it can never collide with a real output key.
        return {**result.get('outputs', {}), '_run_id': result.get('run_id'),
                '_agent_status': result.get('status'), '_metrics': result.get('metrics'),
                '_evidence_ids': [e.get('id') for e in result.get('evidence', [])]}

    def _clone(self, pipeline, step):
        params = json.loads(step['params'] or '{}')
        user, _ = self._principal(pipeline['created_by'])
        result = self.repo_clone.clone(user['id'], params['repo_url'], params['branch'], params['dest'])
        self.pipelines.mark_repo_cloned(pipeline['job_id'], params.get('role', ''), result['path'], result['commit'])
        return result

    def _deliver(self, pipeline, step):
        params = json.loads(step['params'] or '{}')
        user, _ = self._principal(pipeline['created_by'])
        return self.repo_clone.push(user['id'], params['repo_url'], params['branch'], params['source_dir'])


if __name__ == '__main__':
    root = __import__('pathlib').Path(__file__).resolve().parent.parent
    repo = Repository(os.getenv('FACTORY_DB', str(root / 'factory-v2.db')))
    sys.stderr.write('ACE Modernization Accelerator pipeline worker starting (standalone process)\n')
    sys.stderr.flush()
    WorkerLoop(repo, worker_id=os.getenv('WORKER_ID', 'pipeline-worker-standalone')).run_forever()
