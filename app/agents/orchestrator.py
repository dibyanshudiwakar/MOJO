"""Migration Orchestrator Agent.

Steps a job through the lifecycle: for the current state it asks the governance
agent what the next transition requires, invokes the agents that produce the
missing artefacts, and transitions when the gate is satisfied. It keeps going
until governance, a clarification or a guard stops it.

What bounds an autonomous run is the governance it cannot reach around. Every
transition still goes through ``FactoryService.transition``, so gate quorum,
approval iteration, artefact bindings and the ``evidence_bundle`` preconditions
apply exactly as they would to a human operator. The loop therefore stops at G0
with nothing signed rather than walking a job to COMPLETED; that is the
architecture working, not a failure.

The guards here are for runaway protection rather than governance: a step budget,
one visit per (iteration, state, agent) so a state cannot re-run its agents
forever while still allowing rework to re-run them on a new iteration, and a halt
on a paused checkpoint or a cancelled queue activity so an operator can stop a
run in flight.

**RBAC is not relaxed for autonomy.** Each invoked agent's ``required_role`` is
checked against the *orchestration principal's* roles, because the run acts as
that principal throughout. An autonomous run therefore needs a service principal
holding the roles of the agents it invokes; when one is missing the loop stops
with ``role_missing_for:<agent>`` rather than escalating its own privileges.
"""
from app.agents.base import AgentResult, BaseAgent, Clarification
from app.domain.state_machine import TRANSITIONS
from app.service import FactoryService
from app.services.agents import AgentRegistry
from app.services.factory_v3 import WorkflowOrchestrator

TERMINAL = {'COMPLETED', 'STOPPED', 'ROLLED_BACK', 'FAILED'}

#: Which agents produce what a state needs before it can advance.
PIPELINE = {
    'CREATED': ('repository-intake',),
    'INTAKE_VALIDATED': ('dormancy-intelligence',),
    'DORMANCY_ASSESSED': ('framework-baseline',),
    'FRAMEWORK_BASELINED': ('ace-analysis', 'migration-specification'),
    'ACE_ANALYSED': ('planning',),
    'PLAN_APPROVED': ('code-generation',),
    'IMPLEMENTING': (),
    'BUILD_VALIDATED': ('test-engineering',),
    'DIFFERENTIAL_TESTING': ('variance-analysis',),
    'VARIANCE_REVIEW': ('rca',),
    'REMEDIATING': ('remediation',),
    'READY_FOR_PROMOTION': (),
}

NEXT_STATE = {
    'CREATED': 'INTAKE_VALIDATED', 'INTAKE_VALIDATED': 'DORMANCY_ASSESSED',
    'DORMANCY_ASSESSED': 'FRAMEWORK_BASELINED', 'FRAMEWORK_BASELINED': 'ACE_ANALYSED',
    'ACE_ANALYSED': 'PLAN_APPROVED', 'PLAN_APPROVED': 'IMPLEMENTING',
    'IMPLEMENTING': 'BUILD_VALIDATED', 'BUILD_VALIDATED': 'DIFFERENTIAL_TESTING',
    'DIFFERENTIAL_TESTING': 'VARIANCE_REVIEW', 'REMEDIATING': 'IMPLEMENTING',
    'READY_FOR_PROMOTION': 'COMPLETED',
}

#: States whose agents refuse to run until the gate is already signed, because
#: their own services require an artefact-bound approval: ``planning`` needs G1
#: and G2, ``generate_spring`` needs G3. Invoking them earlier would surface a
#: governance refusal as an agent failure.
#:
#: Everywhere else the agents run *before* the gate is evaluated, because what
#: they produce is what the gate decides on -- RCA informs the G4 decision, and
#: the variance analysis produces the differential evidence the VARIANCE_REVIEW
#: precondition looks for. Requiring the gate first would deadlock those states.
GATE_FIRST = {'ACE_ANALYSED', 'PLAN_APPROVED'}

APPROVAL_MISSING = 'required_gate_approval_missing'


class MigrationOrchestratorAgent(BaseAgent):
    AGENT_ID = 'migration-orchestrator'
    TOOLS = ('workflow.read', 'workflow.transition', 'agent.invoke', 'clarification.create')
    OUTPUTS = ('commands', 'next_state_proposal', 'clarification_request')
    PROMPT = 'agent/migration-orchestrator/next-action'

    def _latest(self, table, job_id, where=''):
        return self.repo.one('select * from %s where job_id=?%s order by created_at desc limit 1'
                             % (table, where), (job_id,))

    def _target(self, job):
        """The next state, branching where the lifecycle genuinely branches."""
        if job['state'] == 'VARIANCE_REVIEW':
            # An open variance is rework; none left means the differential is
            # settled and promotion is the honest next step.
            open_variances = self.repo.one(
                "select count(*) n from differential_evidence where job_id=? and disposition='OPEN'",
                (job['id'],))['n']
            return 'REMEDIATING' if open_variances else 'READY_FOR_PROMOTION'
        return NEXT_STATE.get(job['state'], '')

    def _params(self, agent_id, seed, job):
        """Seed parameters plus the artefact ids produced by earlier steps."""
        job_id = job['id']
        if agent_id == 'repository-intake':
            return {'path': seed.get('ace_path'), 'branch': seed.get('branch', 'main'),
                    'revision': seed.get('revision', '')}
        if agent_id == 'dormancy-intelligence':
            return {k: seed.get(k) for k in ('traffic_count', 'last_seen_days', 'deployed', 'already_migrated')}
        if agent_id == 'framework-baseline':
            return {'path': seed.get('framework_path'), 'version': seed.get('framework_version', '1.0.0'),
                    'golden_path': seed.get('golden_path')}
        if agent_id == 'ace-analysis':
            return {'path': seed.get('ace_path'), 'version': seed.get('analysis_version', '1.0.0')}
        analysis = self._latest('ace_analyses', job_id)
        framework = self._latest('framework_baselines', job_id)
        specification = self._latest('migration_specs', job_id, " and validation_status='VALID'")
        plan = self._latest('migration_plans', job_id)
        if agent_id == 'migration-specification':
            return {'analysis_id': (analysis or {}).get('id'),
                    'framework_id': (framework or {}).get('id', ''),
                    'ace_version': seed.get('ace_version', '12')}
        if agent_id == 'planning':
            return {'framework_id': (framework or {}).get('id'), 'analysis_id': (analysis or {}).get('id'),
                    'specification_id': (specification or {}).get('id'),
                    'overrides': seed.get('overrides')}
        if agent_id == 'code-generation':
            return {'plan_id': (plan or {}).get('id'), 'output_root': seed.get('output_root'),
                    'service_name': seed.get('service_name') or job['service'],
                    'package_name': seed.get('package_name', 'com.example.migration'),
                    'execute_build': seed.get('execute_build', False)}
        if agent_id == 'test-engineering':
            return {'plan_id': (plan or {}).get('id'), 'cases': seed.get('cases'),
                    'policy': seed.get('policy', {}), 'ace_adapter': seed.get('ace_adapter'),
                    'spring_adapter': seed.get('spring_adapter'),
                    'ace_base_url': seed.get('ace_base_url', ''),
                    'spring_base_url': seed.get('spring_base_url', '')}
        if agent_id == 'variance-analysis':
            package = self._latest('differential_packages', job_id)
            return {'package_id': (package or {}).get('id'), 'policy': seed.get('policy')}
        if agent_id == 'remediation':
            cause = self._latest('root_causes', job_id)
            return {'root_cause_id': (cause or {}).get('id'),
                    'impact_bound': seed.get('impact_bound', 10)}
        if agent_id == 'knowledge-evidence':
            return {'query': seed.get('query') or job['service']}
        return {}

    def _halted(self, job_id):
        """An operator's stop signal, through either existing mechanism."""
        if self.repo.one("select id from workflow_checkpoints where job_id=? and status='PAUSED' limit 1", (job_id,)):
            return 'paused'
        if self.repo.one('select id from workflow_queue where job_id=? and cancel_requested=1 limit 1', (job_id,)):
            return 'cancel_requested'
        return ''

    def _completed(self, job_id, iteration):
        """Pipeline agents already completed for this job at this iteration.

        An orchestration run is normally one of several -- it stops at a gate, a
        human signs, and it is invoked again. Without this, the second run would
        re-run the agents the first one completed, producing a second framework
        baseline or analysis that the existing gate approval is not bound to, and
        the artefact the approval covers would silently stop being the one the
        next agent reads. Rework is exempt because it opens a new iteration.
        """
        return {x['agent_id'] for x in self.repo.rows(
            "select distinct agent_id from agent_runs where job_id=? and status='COMPLETED'"
            ' and iteration=? and agent_id is not null', (job_id, iteration))}

    def execute(self, context):
        registry = AgentRegistry(self.repo)
        factory = FactoryService(self.repo)
        workflow = WorkflowOrchestrator(self.repo)
        seed = dict(context.params)
        budget = max(1, int(seed.get('max_steps', 24)))
        invoke = bool(seed.get('invoke_agents', True))
        apply_transition = bool(seed.get('apply_transition', True))
        commands = []
        visited = set()
        question = None
        stop = 'step_budget_exhausted'
        proposal = ''

        def run(agent_id):
            # A key present but None would satisfy ``AgentContext.param`` and reach
            # the service as None, so an absent seed input has to be absent here
            # too -- that is what turns it into ``missing_param`` and then into a
            # clarification, rather than a TypeError deep inside a service.
            params = {k: v for k, v in self._params(agent_id, seed, context.job()).items() if v is not None}
            outcome = registry.dispatch(context.job_id, agent_id, '1.0.0', context.roles,
                                        context.identity, params,
                                        provider=context.provider, channel=context.channel)
            commands.append({'agent_id': agent_id, 'run_id': outcome['run_id'],
                             'status': outcome['status'], 'outputs': outcome['outputs'],
                             'evidence_ids': [x['id'] for x in outcome['evidence']],
                             'reasoning': (outcome.get('reasoning') or {}).get('mode', '')})
            return outcome

        for _ in range(budget):
            job = context.job()
            state = job['state']
            iteration = int(job['iteration'] or 1)
            if state in TERMINAL:
                stop = 'terminal_state:' + state
                break
            halt = self._halted(context.job_id)
            if halt:
                stop = halt
                break
            target = self._target(job)
            if not target:
                stop = 'no_next_state:' + state
                break
            proposal = target
            workflow.checkpoint(context.job_id, state, {'target': target, 'step': len(commands)})
            try:
                evaluation = run('governance')
            except PermissionError as exc:
                stop = 'role_missing_for:governance:' + str(exc)
                break
            allow = (evaluation['outputs'].get('allow_deny_decision') or {}).get(target) == 'ALLOW'
            reasons = ((evaluation['outputs'].get('evidence_requirement') or {}).get(target) or {}).get('reasons', [])
            if state in GATE_FIRST and any(r.startswith(APPROVAL_MISSING) for r in reasons):
                stop = 'gate_required:%s:%s' % (target, ';'.join(reasons))
                break
            # ``governance`` and ``knowledge-evidence`` are deliberately not
            # subject to this: both are idempotent utilities that must re-run as
            # the job moves, and a stale governance evaluation would be worse
            # than none.
            done = self._completed(context.job_id, iteration)
            pending = [a for a in PIPELINE.get(state, ())
                       if a not in done and (iteration, state, a) not in visited]
            if pending and invoke:
                blocked = False
                for agent_id in pending:
                    visited.add((iteration, state, agent_id))
                    try:
                        outcome = run(agent_id)
                    except PermissionError as exc:
                        stop = 'blocked:%s:%s' % (agent_id, exc)
                        blocked = True
                        break
                    except ValueError as exc:
                        # A missing input is a question for a person: the agent is
                        # entitled to run, but the factory has not been told
                        # something it needs.
                        question = Clarification(
                            'ORCHESTRATION',
                            'Agent %s cannot run in state %s: %s. Which input should it use?' % (agent_id, state, exc),
                            'ORCHESTRATOR', 'Autonomous migration is halted', 'job://' + context.job_id)
                        stop = 'input_required_for:' + agent_id
                        blocked = True
                        break
                    if outcome['status'] == 'FAILED':
                        stop = 'agent_failed:%s:%s' % (agent_id, outcome['error'])
                        blocked = True
                        break
                if blocked:
                    break
                try:
                    run('knowledge-evidence')
                except PermissionError:
                    # Indexing is a retrieval aid, not a gate. A principal without
                    # the curator role should not lose the whole run over it.
                    commands.append({'agent_id': 'knowledge-evidence', 'status': 'SKIPPED', 'outputs': {},
                                     'evidence_ids': [], 'reasoning': 'required_agent_role_missing'})
                # Artefacts changed, so the gate must be re-evaluated against them.
                continue
            if not allow:
                stop = 'gate_required:%s:%s' % (target, ';'.join(reasons) or 'not_satisfied')
                break
            if not apply_transition:
                stop = 'transition_not_applied:' + target
                break
            factory.transition(context.job_id, target, context.identity,
                               {'source': self.AGENT_ID, 'step': len(commands)})

        clarifications = ()
        if question and not self.repo.one(
                "select id from clarifications where job_id=? and question=? and status='OPEN'",
                (context.job_id, question.question)):
            clarifications = (question,)
        final = context.job()
        return AgentResult(
            status='CLARIFICATION_REQUIRED' if clarifications else 'COMPLETED',
            outputs={'commands': commands, 'next_state_proposal': proposal,
                     'clarification_request': question.question if question else ''},
            clarifications=clarifications,
            next_state=proposal,
            commands=tuple(x['agent_id'] for x in commands),
            metrics={'steps': len(commands), 'stop_reason': stop, 'final_state': final['state'],
                     'iteration': int(final['iteration'] or 1),
                     'legal_targets': sorted(TRANSITIONS.get(final['state'], set()))},
            reasoning={'mode': 'DETERMINISTIC_FALLBACK', 'prompt_name': self.PROMPT,
                       'reason': 'the next action follows the governed pipeline, not a model proposal'})


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(MigrationOrchestratorAgent))
