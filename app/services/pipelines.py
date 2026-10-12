"""Per-migration pipeline: a chosen subset of the agent catalogue, scheduled as a DAG.

``agent_registry.dependencies`` says which agents require which other agents to
have *completed for the job* -- a global, contract-level rule enforced by
``AgentRegistry.dispatch`` regardless of this module. What did not exist before
is a way to pick a subset of the catalogue for one migration and have that
subset actually execute in order, with clone/push woven in as steps alongside
the catalogue agents rather than as a separate up-front action.

This does not touch or bypass the G0-G5 approval gates. A step like
``planning`` or ``code-generation`` will legitimately fail with the same
``g1_framework_approval_required``-style errors ``MappingAndPlanningService``
already raises if nobody has approved the relevant gate yet -- that failure is
retryable (see ``retry_step``) once the operator approves it through the
existing Approval Governance screens, which this module does not reimplement.
"""
import json
import logging
import re as _re
from app.repository import uid, now, digest
from app.services import workspace
from app.service import FactoryService
from app.services.policy import PolicyError
from app.domain.state_machine import TransitionError

_log = logging.getLogger(__name__)


def _gate_from_error(error_str):
    """Extract gate label (e.g. 'G2') from a gate-block error message."""
    m = _re.search(r'required_gate_approval_missing:(G\d)', error_str)
    if m:
        return m.group(1)
    m = _re.search(r'g(\d)_\w+_approval_required', error_str)
    if m:
        return 'G' + m.group(1)
    return ''

# migration-orchestrator is deliberately not offered here: it is its own
# state-driven loop over the lifecycle's PIPELINE stages, and running it
# alongside this scheduler would drive the same job from two directions at
# once. Every other catalogue agent is selectable.
SELECTABLE_AGENTS_EXCLUDED = {'migration-orchestrator'}

# Params only knowable once an earlier step in *this* pipeline has completed.
# (target_agent_id -> {param_name: (source_agent_id, output_key)}). The output
# keys are each agent's own declared AgentResult.outputs, taken verbatim from
# app/agents/*.py -- see ace_analysis.py (analysis_document), framework_baseline.py
# (framework_template), migration_specification.py (migration_specification_yaml),
# planning.py (plan).
PARAM_SOURCES = {
    'migration-specification': {'analysis_id': ('ace-analysis', 'analysis_document'),
                                 'framework_id': ('framework-baseline', 'framework_template')},
    'planning': {'analysis_id': ('ace-analysis', 'analysis_document'),
                 'framework_id': ('framework-baseline', 'framework_template'),
                 'specification_id': ('migration-specification', 'migration_specification_yaml')},
    'code-generation': {'plan_id': ('planning', 'plan')},
}
# Extra depends_on edges this scheduler adds on top of the catalogue's own
# dependency graph, keyed by role: an agent that reads a repository directly
# must wait for that repository's CLONE step even though the catalogue's
# dependency graph (agent-to-agent only) has no way to express that.
REPO_CONSUMERS = {'repository-intake': 'ACE', 'ace-analysis': 'ACE', 'framework-baseline': 'FRAMEWORK'}

# Central mapping: agent_id → the lifecycle state that agent's successful
# completion should drive. FactoryService.transition() remains the single
# authority for all state writes and gate enforcement; this table only records
# *which* transition to request when a pipeline step completes.
#
# Agents not listed here produce no lifecycle side-effect (CLONE/DELIVERY
# steps, downstream reporting agents, etc.). Gate-guarded transitions will be
# silently skipped when the required approval has not yet been recorded --
# the pipeline step still completes normally and the operator can approve the
# gate through the Governance screen to unlock the lifecycle advance.
#
# A future migration-orchestrator calls FactoryService.transition() directly
# using the same target state strings -- no code changes needed here.
AGENT_LIFECYCLE_TRANSITIONS = {
    'repository-intake':     'INTAKE_VALIDATED',
    'dormancy-intelligence': 'DORMANCY_ASSESSED',
    'framework-baseline':    'FRAMEWORK_BASELINED',
    'ace-analysis':          'ACE_ANALYSED',
    'planning':              'PLAN_APPROVED',
    'code-generation':       'IMPLEMENTING',
    'build-validation':      'BUILD_VALIDATED',
    'test-engineering':      'DIFFERENTIAL_TESTING',
    'variance-analysis':     'VARIANCE_REVIEW',
    'remediation':           'REMEDIATING',
}

# Reverse of AGENT_LIFECYCLE_TRANSITIONS: lifecycle state → the agent whose
# completion is required before that state can be auto-advanced to.
_LIFECYCLE_AGENT = {v: k for k, v in AGENT_LIFECYCLE_TRANSITIONS.items()}

# The main forward lifecycle path in order. Used to walk from the job's current
# state to the furthest state reachable given which agents have completed.
# Terminal/error states (STOPPED, FAILED, ROLLED_BACK) are intentionally
# excluded -- nothing in the pipeline auto-advances to those.
_LIFECYCLE_ORDER = [
    'CREATED', 'INTAKE_VALIDATED', 'DORMANCY_ASSESSED', 'FRAMEWORK_BASELINED',
    'ACE_ANALYSED', 'PLAN_APPROVED', 'IMPLEMENTING', 'BUILD_VALIDATED',
    'DIFFERENTIAL_TESTING', 'VARIANCE_REVIEW', 'REMEDIATING',
    'READY_FOR_PROMOTION', 'COMPLETED',
]
_LIFECYCLE_IDX = {s: i for i, s in enumerate(_LIFECYCLE_ORDER)}

# States that are guarded by a governance gate (G0–G5).  Any skipped agent
# (whether explicitly disabled by the user OR dependency-cascade-skipped
# because its prerequisites were disabled) is still allowed to trigger the
# transition attempt for these states so the gate can fire and collect
# approval.  This supports teams that intentionally skip optional agents
# (e.g. framework-baseline, planning) but still need governance sign-off.
_GATED_STATES = frozenset({
    'FRAMEWORK_BASELINED', 'ACE_ANALYSED', 'PLAN_APPROVED',
    'IMPLEMENTING', 'REMEDIATING', 'READY_FOR_PROMOTION', 'COMPLETED',
})

# Artefact guard for pipeline-driven lifecycle advancement.
# Maps lifecycle state → (table, job_id_column) that MUST have a row before
# the pipeline system will attempt the transition.  This is deliberately
# checked only inside _try_lifecycle_transition / resume_after_clarification
# (not in FactoryService.transition itself) so that administrative and test
# direct-transition paths are not blocked.
_PIPELINE_ARTEFACT_REQUIREMENTS = {
    'FRAMEWORK_BASELINED': ('framework_baselines', 'job_id'),
    'ACE_ANALYSED':        ('ace_analyses',        'job_id'),
    'PLAN_APPROVED':       ('migration_plans',     'job_id'),
    'IMPLEMENTING':        ('migration_specs',     'job_id'),
}


def _artefact_ready(repo, job_id, target):
    """Return True iff the artefact required for *target* exists for this job."""
    req = _PIPELINE_ARTEFACT_REQUIREMENTS.get(target)
    if not req:
        return True
    table, col = req
    with repo.connect() as c:
        return bool(c.execute(
            'select id from %s where %s=? limit 1' % (table, col), (job_id,)
        ).fetchone())


class PipelineError(ValueError):
    pass


class PipelineService:
    def __init__(self, repo):
        self.repo = repo

    # -- catalogue graph ---------------------------------------------------
    def _catalogue(self):
        rows = self.repo.rows("select agent_id,display_name,dependencies,required_role from agent_registry where enabled=1 and status='APPROVED'")
        return {r['agent_id']: {**r, 'dependencies': json.loads(r['dependencies'] or '[]')}
                for r in rows if r['agent_id'] not in SELECTABLE_AGENTS_EXCLUDED}

    def selectable_agents(self):
        return list(self._catalogue().values())

    def impact(self, selected_agent_ids):
        """What actually runs and what gets skipped for a proposed selection.

        Used by the wizard's live preview before anything is created, and by
        ``create`` itself, so the preview always matches what gets built.
        """
        catalogue = self._catalogue()
        selected = set(selected_agent_ids) & set(catalogue)
        changed = True
        while changed:
            changed = False
            for agent_id in list(selected):
                deps = set(catalogue[agent_id]['dependencies'])
                if not deps.issubset(selected):
                    selected.discard(agent_id)
                    changed = True
        # Track which agents were originally requested (before the fixed-point
        # dependency prune) so we can tell apart "never selected" from "selected
        # but pruned because a dep was missing".
        originally_selected = set(selected_agent_ids) & set(catalogue)
        skipped = []
        for agent_id in sorted(set(catalogue) - selected):
            deps = catalogue[agent_id]['dependencies']
            missing = [d for d in deps if d not in selected]
            if agent_id not in originally_selected:
                # Agent was never chosen by the user — not a valid lifecycle
                # waypoint; must not substitute for agent execution.
                skip_kind = 'explicitly_disabled'
                reason = 'explicitly disabled'
            else:
                # Agent was selected but the fixed-point loop removed it
                # because a required dependency is absent from the run.
                skip_kind = 'dependency'
                reason = 'depends on ' + ', '.join(missing)
            skipped.append({'agent_id': agent_id, 'display_name': catalogue[agent_id]['display_name'],
                            'reason': reason, 'skip_kind': skip_kind})
        return {'runnable': self._topological(catalogue, selected), 'skipped': skipped}

    @staticmethod
    def _topological(catalogue, selected):
        order, seen = [], set()
        def visit(agent_id):
            if agent_id in seen:
                return
            seen.add(agent_id)
            for dep in catalogue[agent_id]['dependencies']:
                if dep in selected:
                    visit(dep)
            order.append(agent_id)
        for agent_id in sorted(selected):
            visit(agent_id)
        return order

    # -- repositories -------------------------------------------------------
    def save_repositories(self, job_id, repos):
        """Persist the wizard's repo sections. ``repos`` is role -> config dict.

        Existing rows are looked up with ``self.repo.one`` *before* the write
        transaction opens, and the return value is built from the row this
        method just constructed rather than re-reading it from a second
        connection inside the same transaction -- an uncommitted insert on
        one connection is invisible to a fresh ``self.repo.one`` call opened
        while that transaction is still open, so that read would see nothing.
        """
        if any(role not in ('ACE', 'FRAMEWORK', 'TARGET') for role in repos):
            raise PipelineError('unsupported_repository_role:' + ','.join(sorted(set(repos) - {'ACE', 'FRAMEWORK', 'TARGET'})))
        existing_by_role = {row['role']: row for row in
                            self.repo.rows('select id,role,created_at from job_repositories where job_id=?', (job_id,))}
        rows = []
        for role, cfg in repos.items():
            # Validated the same way repo_clone validates before actually
            # cloning, so a typo in the wizard fails immediately rather than
            # only once the pipeline runs and a worker gets to that step.
            if cfg.get('enabled', True) and cfg.get('repo_url'):
                workspace.runtime_url(cfg['repo_url'])
                workspace.git_ref(cfg.get('branch') or 'main')
            existing = existing_by_role.get(role)
            row = {'id': existing['id'] if existing else uid(), 'job_id': job_id, 'role': role,
                   'repo_url': cfg.get('repo_url', ''), 'branch': cfg.get('branch', ''),
                   'enabled': 1 if cfg.get('enabled', True) else 0, 'local_path': cfg.get('local_path', ''),
                   'resolved_commit': '', 'push_mode': cfg.get('push_mode', 'LOCAL_OUTPUT'),
                   'created_at': existing['created_at'] if existing else now(), 'updated_at': now()}
            rows.append(row)
        with self.repo.connect() as c:
            for row in rows:
                c.execute('delete from job_repositories where job_id=? and role=?', (job_id, row['role']))
                self.repo.insert(c, 'job_repositories', row)
        return rows

    def mark_repo_cloned(self, job_id, role, local_path, resolved_commit):
        """Record where a repo actually landed and at what commit, once cloned."""
        if not role:
            return
        with self.repo.connect() as c:
            c.execute('update job_repositories set local_path=?,resolved_commit=?,updated_at=? where job_id=? and role=?',
                      (local_path, resolved_commit, now(), job_id, role))

    # -- pipeline creation ----------------------------------------------------
    def create(self, job_id, workspace_dir, repos, selected_agent_ids, created_by, name='',
              service_name='migrated-service', package_name='com.example.migrated'):
        repo_rows = self.save_repositories(job_id, repos)
        by_role = {r['role']: r for r in repo_rows}
        plan = self.impact(selected_agent_ids)
        catalogue = self._catalogue()

        pid = uid()
        ts = now()
        steps = []
        step_id_by_key = {}  # 'CLONE:ACE' / 'AGENT:<agent_id>' -> step id

        def add(kind, key, agent_id='', depends_on=(), params=None):
            sid = uid()
            step_id_by_key[key] = sid
            steps.append({'id': sid, 'pipeline_id': pid, 'sequence': len(steps), 'kind': kind,
                          'agent_id': agent_id, 'version': '1.0.0',
                          'depends_on': json.dumps(list(depends_on)), 'status': 'PENDING',
                          'agent_run_id': '', 'queue_id': '', 'params': json.dumps(params or {}),
                          'started_at': '', 'completed_at': '', 'error': '', 'created_at': ts, 'updated_at': ts})

        for role in ('ACE', 'FRAMEWORK'):
            r = by_role.get(role)
            if r and r['enabled']:
                dest = workspace_dir.rstrip('/') + '/' + role.lower()
                add('CLONE', 'CLONE:' + role, params={'repo_url': r['repo_url'], 'branch': r['branch'], 'dest': dest, 'role': role})

        for agent_id in plan['runnable']:
            deps = [step_id_by_key['AGENT:' + d] for d in catalogue[agent_id]['dependencies'] if 'AGENT:' + d in step_id_by_key]
            repo_role = REPO_CONSUMERS.get(agent_id)
            if repo_role and 'CLONE:' + repo_role in step_id_by_key:
                deps.append(step_id_by_key['CLONE:' + repo_role])
            params = self._seed_params(agent_id, by_role, workspace_dir, service_name, package_name)
            add('AGENT', 'AGENT:' + agent_id, agent_id=agent_id, depends_on=deps, params=params)

        for skip in plan['skipped']:
            add('AGENT', 'AGENT:' + skip['agent_id'], agent_id=skip['agent_id'])
            steps[-1]['status'] = 'SKIPPED'
            # Encode skip_kind into the error field so _try_lifecycle_transition
            # can distinguish a user-deselected agent from a dependency cascade.
            # Format: "skip_kind:<kind>;<human readable reason>"
            steps[-1]['error'] = 'skip_kind:' + skip['skip_kind'] + ';' + skip['reason']

        target = by_role.get('TARGET')
        if target and target.get('push_mode') == 'PUSH_TO_REPO' and 'AGENT:code-generation' in step_id_by_key:
            add('DELIVERY', 'DELIVERY:TARGET', depends_on=[step_id_by_key['AGENT:code-generation']],
                params={'repo_url': target['repo_url'], 'branch': target['branch'],
                        'source_dir': workspace_dir.rstrip('/') + '/springboot-output'})

        with self.repo.connect() as c:
            self.repo.insert(c, 'pipelines', {'id': pid, 'job_id': job_id, 'name': name, 'workspace_dir': workspace_dir,
                                              'status': 'DRAFT', 'created_by': created_by, 'created_at': ts, 'updated_at': ts,
                                              'started_at': '', 'completed_at': ''})
            for s in steps:
                self.repo.insert(c, 'pipeline_steps', s)
        return self.detail(pid)

    def _seed_params(self, agent_id, by_role, workspace_dir, service_name, package_name):
        ace = by_role.get('ACE', {})
        base = workspace_dir.rstrip('/')
        seeds = {
            'repository-intake': {'path': base + '/ace', 'branch': ace.get('branch', 'main')},
            'ace-analysis': {'path': base + '/ace', 'version': '1.0.0'},
            'framework-baseline': {'path': base + '/framework', 'golden_path': base + '/framework', 'version': '1.0.0'},
            'migration-specification': {'version': '1.0.0'},
            'planning': {'version': '1.0.0'},
            'code-generation': {'output_root': base + '/springboot-output',
                                 'service_name': service_name or 'migrated-service',
                                 'package_name': package_name or 'com.example.migrated'},
        }
        return seeds.get(agent_id, {})

    # -- confirm / run / advance ---------------------------------------------
    def confirm(self, pipeline_id):
        p = self.repo.one('select * from pipelines where id=?', (pipeline_id,))
        if not p:
            raise ValueError('pipeline_not_found')
        if p['status'] != 'DRAFT':
            raise PipelineError('pipeline_not_in_draft:' + p['status'])
        with self.repo.connect() as c:
            c.execute("update pipelines set status='READY',updated_at=? where id=?", (now(), pipeline_id))
        return self.detail(pipeline_id)

    def run(self, pipeline_id, workflow):
        p = self.repo.one('select * from pipelines where id=?', (pipeline_id,))
        if not p:
            raise ValueError('pipeline_not_found')
        if p['status'] not in ('READY', 'RUNNING'):
            raise PipelineError('pipeline_not_ready:' + p['status'])
        with self.repo.connect() as c:
            c.execute("update pipelines set status='RUNNING',started_at=?,updated_at=? where id=? and started_at=''", (now(), now(), pipeline_id))
        self._schedule_ready(pipeline_id, workflow)
        return self.detail(pipeline_id)

    def pause(self, pipeline_id, workflow):
        """Stop scheduling new work, reversibly. A step already claimed by the
        worker (RUNNING) is left to finish -- it cannot be preempted -- but any
        step merely QUEUED (scheduled, not yet claimed) is pulled back to
        PENDING so ``resume`` can re-queue it cleanly later, the same way
        ``retry_step`` already forces a step back through the queue.
        """
        p = self.repo.one('select * from pipelines where id=?', (pipeline_id,))
        if not p:
            raise ValueError('pipeline_not_found')
        if p['status'] != 'RUNNING':
            raise PipelineError('pipeline_not_running:' + p['status'])
        queued = self.repo.rows("select * from pipeline_steps where pipeline_id=? and status='QUEUED'", (pipeline_id,))
        with self.repo.connect() as c:
            for step in queued:
                if step['queue_id']:
                    workflow.cancel(step['queue_id'])
                c.execute("update pipeline_steps set status='PENDING',queue_id='',updated_at=? where id=?", (now(), step['id']))
            c.execute("update pipelines set status='PAUSED',updated_at=? where id=?", (now(), pipeline_id))
        return self.detail(pipeline_id)

    def resume(self, pipeline_id, workflow):
        p = self.repo.one('select * from pipelines where id=?', (pipeline_id,))
        if not p:
            raise ValueError('pipeline_not_found')
        if p['status'] != 'PAUSED':
            raise PipelineError('pipeline_not_paused:' + p['status'])
        with self.repo.connect() as c:
            c.execute("update pipelines set status='RUNNING',updated_at=? where id=?", (now(), pipeline_id))
        self._schedule_ready(pipeline_id, workflow)
        return self.detail(pipeline_id)

    def cancel(self, pipeline_id, workflow):
        """Stop a pipeline. Nothing further is scheduled and everything that has
        not yet started is cancelled immediately. A step already RUNNING (a live
        git clone or an in-flight agent dispatch) cannot be preempted, so it is
        left to finish naturally -- but ``advance``'s guard below means its
        eventual completion can no longer change the pipeline's outcome, since
        the pipeline is already terminal here.
        """
        p = self.repo.one('select * from pipelines where id=?', (pipeline_id,))
        if not p:
            raise ValueError('pipeline_not_found')
        if p['status'] not in ('DRAFT', 'READY', 'RUNNING', 'PAUSED'):
            raise PipelineError('pipeline_not_cancellable:' + p['status'])
        rows = self.repo.rows('select * from pipeline_steps where pipeline_id=?', (pipeline_id,))
        with self.repo.connect() as c:
            for step in rows:
                if step['status'] == 'QUEUED':
                    if step['queue_id']:
                        workflow.cancel(step['queue_id'])
                    c.execute("update pipeline_steps set status='CANCELLED',queue_id='',updated_at=? where id=?", (now(), step['id']))
                elif step['status'] == 'PENDING':
                    c.execute("update pipeline_steps set status='CANCELLED',updated_at=? where id=?", (now(), step['id']))
            c.execute("update pipelines set status='CANCELLED',completed_at=?,updated_at=? where id=?", (now(), now(), pipeline_id))
        return self.detail(pipeline_id)

    def _ready_steps(self, pipeline_id):
        rows = self.repo.rows('select * from pipeline_steps where pipeline_id=? order by sequence', (pipeline_id,))
        status = {r['id']: r['status'] for r in rows}
        ready = []
        for r in rows:
            if r['status'] != 'PENDING':
                continue
            deps = json.loads(r['depends_on'] or '[]')
            if all(status.get(d) == 'COMPLETED' for d in deps):
                ready.append(r)
        return ready, rows

    def _schedule_ready(self, pipeline_id, workflow):
        job_id = self.repo.one('select job_id from pipelines where id=?', (pipeline_id,))['job_id']
        ready, _ = self._ready_steps(pipeline_id)
        for step in ready:
            q = workflow.schedule(job_id, 'pipeline_step', {'pipeline_id': pipeline_id, 'step_id': step['id']},
                                  idempotency_key='pipeline-step:' + step['id'])
            with self.repo.connect() as c:
                c.execute("update pipeline_steps set status='QUEUED',queue_id=?,updated_at=? where id=? and status='PENDING'", (q['id'], now(), step['id']))

    @staticmethod
    def _blocked(rows):
        """Every step that can never become ready: FAILED/CANCELLED, or PENDING behind one.

        A single hop (does this step's *direct* dependency show FAILED) is not
        enough -- a step two or more hops downstream of a failure sits at
        PENDING with an *intermediate* dependency that is itself only
        PENDING, never FAILED, so a one-hop check never marks it blocked and
        the pipeline sits at RUNNING forever. This is the same fixed-point
        closure ``impact()`` already uses for pruning, applied to blockage
        instead of selection. CANCELLED is seeded alongside FAILED so a step
        stopped by ``cancel()`` also permanently blocks its dependents if the
        pipeline is later resumed from a paused state.
        """
        status = {r['id']: r['status'] for r in rows}
        deps = {r['id']: json.loads(r['depends_on'] or '[]') for r in rows}
        blocked = {rid for rid, st in status.items() if st in ('FAILED', 'CANCELLED')}
        changed = True
        while changed:
            changed = False
            for r in rows:
                if r['status'] == 'PENDING' and r['id'] not in blocked and any(d in blocked for d in deps[r['id']]):
                    blocked.add(r['id'])
                    changed = True
        return blocked

    def advance(self, pipeline_id, workflow):
        """Call after any step reaches a terminal state; schedules what is newly ready.

        A PENDING step permanently blocked by a failure (directly or
        transitively) can never become ready -- it is not "still active".
        Counting it as active would leave the pipeline at RUNNING forever the
        moment any step fails, since nothing ever advances a blocked step out
        of PENDING.

        A pipeline already PAUSED/CANCELLED/COMPLETED/FAILED must not be
        touched here: a step that was RUNNING when ``cancel()`` (Stop) was
        called cannot be preempted and keeps executing, and the worker calls
        this same method once it finishes -- without this guard that late
        completion would resurrect an already-stopped pipeline back to
        COMPLETED/FAILED, and a PAUSED pipeline would get re-finalized instead
        of waiting for an explicit ``resume()``.
        """
        p = self.repo.one('select status from pipelines where id=?', (pipeline_id,))
        if not p or p['status'] in ('PAUSED', 'CANCELLED', 'COMPLETED', 'FAILED'):
            return
        self._schedule_ready(pipeline_id, workflow)
        _, rows = self._ready_steps(pipeline_id)
        blocked = self._blocked(rows)
        still_active = any(r['status'] in ('QUEUED', 'RUNNING') for r in rows) or \
            any(r['status'] == 'PENDING' and r['id'] not in blocked for r in rows)
        if still_active:
            return
        failed = any(r['status'] == 'FAILED' for r in rows)
        with self.repo.connect() as c:
            c.execute("update pipelines set status=?,completed_at=?,updated_at=? where id=?",
                      ('FAILED' if failed else 'COMPLETED', now(), now(), pipeline_id))

    def mark_step(self, step_id, status, outputs=None, error=''):
        with self.repo.connect() as c:
            if status == 'RUNNING':
                c.execute("update pipeline_steps set status=?,started_at=?,updated_at=? where id=?", (status, now(), now(), step_id))
            else:
                c.execute("update pipeline_steps set status=?,completed_at=?,outputs=?,error=?,updated_at=? where id=?",
                          (status, now(), json.dumps(outputs or {}, default=str), error, now(), step_id))
        if status == 'COMPLETED':
            self._try_lifecycle_transition(step_id)
        elif status == 'FAILED':
            self._check_gate_block(step_id, error or '')

    def _try_lifecycle_transition(self, step_id):
        """Walk the lifecycle forward as far as completed agents allow.

        Agents in a pipeline can complete out of order (dormancy-intelligence has
        no dependency on repository-intake, so it can finish first while the job
        is still CREATED). A single-jump attempt would then be rejected as an
        illegal transition. Instead, every time any agent step completes this
        method:
          1. reads the full set of COMPLETED agent steps for this pipeline
          2. walks _LIFECYCLE_ORDER forward from the job's current state
          3. for each next state, checks whether its required agent has completed
          4. attempts the transition via FactoryService (which enforces gates and
             the state machine) and stops on the first failure

        This means a late-completing repository-intake will also pick up a
        dormancy-intelligence that already finished, advancing straight through
        INTAKE_VALIDATED → DORMANCY_ASSESSED in one call.

        Any failure (gate not yet approved, illegal transition, open
        clarification) is logged at DEBUG and silently swallowed -- lifecycle
        bookkeeping must never crash or stall pipeline execution.
        """
        try:
            step = self.repo.one('select agent_id, kind, pipeline_id from pipeline_steps where id=?', (step_id,))
            if not step or step['kind'] != 'AGENT':
                return
            if step['agent_id'] not in AGENT_LIFECYCLE_TRANSITIONS:
                return
            pipeline = self.repo.one('select job_id from pipelines where id=?', (step['pipeline_id'],))
            if not pipeline:
                return

            # All agents that have already completed in this pipeline
            completed = {
                r['agent_id'] for r in self.repo.rows(
                    "select agent_id from pipeline_steps where pipeline_id=? and kind='AGENT' and status='COMPLETED'",
                    (step['pipeline_id'],),
                )
            }
            skipped_rows = self.repo.rows(
                "select agent_id, error from pipeline_steps "
                "where pipeline_id=? and kind='AGENT' and status='SKIPPED' and agent_id is not null",
                (step['pipeline_id'],),
            )
            # Agents skipped only due to a dependency cascade — valid waypoints.
            dependency_skipped = {
                r['agent_id'] for r in skipped_rows
                if str(r['error'] or '').startswith('skip_kind:dependency')
                or (not str(r['error'] or '').startswith('skip_kind:') and str(r['error'] or '').startswith('depends on '))
            }
            # Agents the user intentionally excluded from this pipeline run.
            # These are NOT waypoints for non-gated states, but they ARE allowed
            # to trigger a gate transition — the gate itself becomes the
            # governance approval for skipping the optional agent.
            explicitly_disabled = {
                r['agent_id'] for r in skipped_rows
                if r['agent_id'] not in dependency_skipped
            }
            # Furthest lifecycle index reachable from the completed agents alone.
            max_completed_idx = max(
                (_LIFECYCLE_IDX.get(AGENT_LIFECYCLE_TRANSITIONS.get(a, ''), -1) for a in completed),
                default=-1,
            )

            job = self.repo.one('select state from jobs where id=?', (pipeline['job_id'],))
            if not job:
                return
            current_idx = _LIFECYCLE_IDX.get(job['state'], -1)
            if current_idx < 0:
                return

            factory = FactoryService(self.repo)
            for next_state in _LIFECYCLE_ORDER[current_idx + 1:]:
                required_agent = _LIFECYCLE_AGENT.get(next_state)
                if not required_agent:
                    break
                next_state_idx = _LIFECYCLE_IDX.get(next_state, -1)
                agent_explicitly_disabled = required_agent in explicitly_disabled
                # dep_skipped covers cascaded removal: user disabled a prerequisite
                # agent (e.g. framework-baseline), which caused planning to lose its
                # dependencies and get pruned.  Governance gates must still fire for
                # both kinds of skip — the gate is the approval for the intentional
                # (direct or indirect) exclusion of the optional agent.
                agent_skipped = agent_explicitly_disabled or required_agent in dependency_skipped
                # Advance if:
                #   1. Agent completed, OR
                #   2. Agent dependency-skipped and within completion frontier, OR
                #   3. Agent skipped (either kind) AND target state is gated
                #      (the gate approval serves as sign-off for the optional skip)
                if (required_agent not in completed
                        and not (required_agent in dependency_skipped and next_state_idx <= max_completed_idx)
                        and not (agent_skipped and next_state in _GATED_STATES)):
                    break
                # Artefact guard: skip only when the agent did not run by design.
                # If the agent ran but wrote no record that is a data-quality
                # failure that must block the advance.
                if not agent_skipped and not _artefact_ready(self.repo, pipeline['job_id'], next_state):
                    _log.warning('artefact missing for %s job %s — lifecycle advance blocked',
                                 next_state, pipeline['job_id'])
                    break
                try:
                    factory.transition(
                        pipeline['job_id'], next_state, 'pipeline-system',
                        {'source': 'pipeline_step', 'step_id': step_id, 'agent_id': step['agent_id']},
                    )
                except PolicyError as exc:
                    # Gate not yet approved — park the job and this step so the
                    # pipeline waits visibly until an approver acts.
                    self._enter_gate_wait(step_id, pipeline['job_id'], next_state, str(exc))
                    break
                except TransitionError as exc:
                    # Blocked by open clarifications or an illegal transition.
                    # Log at WARNING so operators can see why the lifecycle stalled
                    # without crashing or stalling pipeline execution.
                    _log.warning('lifecycle blocked at %s for job %s: %s',
                                 next_state, pipeline['job_id'], exc)
                    break
                except Exception as exc:
                    _log.debug('lifecycle advance stopped at %s for job %s: %s',
                               next_state, pipeline['job_id'], exc)
                    break

        except Exception as exc:
            _log.debug('lifecycle transition error for step %s: %s', step_id, exc)

    def resume_after_clarification(self, job_id):
        """Re-attempt lifecycle advance once all open clarifications for a job are answered.

        Mirrors the logic of _try_lifecycle_transition but driven by job_id rather
        than a pipeline step completion event. Called by the clarification-answer
        endpoint after it confirms zero open clarifications remain for the job.

        Passing step_id=None to _enter_gate_wait is safe: the UPDATE affects
        0 rows, which is the correct behaviour (no step needs re-parking here;
        the original step already completed as COMPLETED).
        """
        try:
            # Include COMPLETED pipelines — the pipeline may have finished
            # before all clarifications were answered.
            pipeline = self.repo.one(
                "select id from pipelines where job_id=? order by created_at desc limit 1",
                (job_id,),
            )
            if not pipeline:
                return
            completed = {
                r['agent_id'] for r in self.repo.rows(
                    "select agent_id from pipeline_steps where pipeline_id=? and kind='AGENT' and status='COMPLETED'",
                    (pipeline['id'],),
                )
            }
            skipped_rows = self.repo.rows(
                "select agent_id, error from pipeline_steps "
                "where pipeline_id=? and kind='AGENT' and status='SKIPPED' and agent_id is not null",
                (pipeline['id'],),
            )
            dependency_skipped = {
                r['agent_id'] for r in skipped_rows
                if str(r['error'] or '').startswith('skip_kind:dependency')
                or (not str(r['error'] or '').startswith('skip_kind:') and str(r['error'] or '').startswith('depends on '))
            }
            explicitly_disabled = {
                r['agent_id'] for r in skipped_rows
                if r['agent_id'] not in dependency_skipped
            }
            max_completed_idx = max(
                (_LIFECYCLE_IDX.get(AGENT_LIFECYCLE_TRANSITIONS.get(a, ''), -1) for a in completed),
                default=-1,
            )
            job = self.repo.one('select state from jobs where id=?', (job_id,))
            if not job:
                return
            current_idx = _LIFECYCLE_IDX.get(job['state'], -1)
            if current_idx < 0:
                return
            factory = FactoryService(self.repo)
            for next_state in _LIFECYCLE_ORDER[current_idx + 1:]:
                required_agent = _LIFECYCLE_AGENT.get(next_state)
                if not required_agent:
                    break
                next_state_idx = _LIFECYCLE_IDX.get(next_state, -1)
                agent_explicitly_disabled = required_agent in explicitly_disabled
                agent_skipped = agent_explicitly_disabled or required_agent in dependency_skipped
                if (required_agent not in completed
                        and not (required_agent in dependency_skipped and next_state_idx <= max_completed_idx)
                        and not (agent_skipped and next_state in _GATED_STATES)):
                    break
                if not agent_skipped and not _artefact_ready(self.repo, job_id, next_state):
                    _log.warning('artefact missing for %s job %s — lifecycle advance blocked',
                                 next_state, job_id)
                    break
                try:
                    factory.transition(
                        job_id, next_state, 'clarification-resolved',
                        {'source': 'clarification_answered'},
                    )
                except PolicyError as exc:
                    self._enter_gate_wait(None, job_id, next_state, str(exc))
                    break
                except Exception as exc:
                    _log.debug('resume after clarification stopped at %s for job %s: %s',
                               next_state, job_id, exc)
                    break
        except Exception as exc:
            _log.debug('resume after clarification error for job %s: %s', job_id, exc)

    def retry_step(self, step_id):
        """Re-attempt a FAILED step -- typically because a G-gate approval landed since.

        The step's queue row (same id, same idempotency key) is forced back
        to READY immediately rather than left for schedule()'s own retry
        backoff (2**attempt seconds): that backoff is meant for a transient
        infrastructure blip, not for a human who just fixed the actual cause
        (granted a missing role, approved a gate) and wants it to run now.
        """
        step = self.repo.one('select * from pipeline_steps where id=?', (step_id,))
        if not step:
            raise ValueError('pipeline_step_not_found')
        if step['status'] != 'FAILED':
            raise PipelineError('pipeline_step_not_failed:' + step['status'])
        with self.repo.connect() as c:
            c.execute("update pipeline_steps set status='PENDING',error='',updated_at=? where id=?", (now(), step_id))
            if step['queue_id']:
                c.execute("update workflow_queue set status='READY',available_at=?,lease_owner=null,lease_until=null,updated_at=? where id=?",
                          (now(), now(), step['queue_id']))
        return self.repo.one('select * from pipeline_steps where id=?', (step_id,))

    # -- gate-approval waiting -------------------------------------------------

    def _ensure_evidence(self, job_id, gate, content_hash):
        """Return an evidence id for the gate-block snapshot, inserting if absent."""
        existing = self.repo.one(
            "select id from evidence where job_id=? and kind='gate_block' and producer=?",
            (job_id, gate),
        )
        if existing:
            return existing['id']
        eid = uid()
        with self.repo.connect() as c:
            self.repo.insert(c, 'evidence', {
                'id': eid, 'job_id': job_id, 'kind': 'gate_block',
                'version': '1.0', 'uri': 'pipeline://gate/' + gate,
                'content_hash': content_hash, 'producer': gate,
                'metadata': '{}', 'immutable': 1, 'created_at': now(),
            })
        return eid

    def _auto_create_approval_package(self, job_id, gate):
        """Insert a PENDING_APPROVAL package for gate if one does not already exist."""
        existing = self.repo.one(
            "select id from approval_packages where job_id=? and gate=? and status='PENDING_APPROVAL'",
            (job_id, gate),
        )
        if existing:
            return existing['id']
        content = json.dumps({'job_id': job_id, 'gate': gate, 'source': 'pipeline-system'}, sort_keys=True)
        content_hash = digest(content)
        eid = self._ensure_evidence(job_id, gate, content_hash)
        pkg_id = uid()
        with self.repo.connect() as c:
            self.repo.insert(c, 'approval_packages', {
                'id': pkg_id, 'job_id': job_id, 'gate': gate,
                'status': 'PENDING_APPROVAL', 'payload': content,
                'content_hash': content_hash,
                'artefact_bindings': json.dumps({eid: content_hash}),
                'conditions': '', 'created_at': now(),
            })
        return pkg_id

    def _enter_gate_wait(self, step_id, job_id, blocked_target, error_msg, needs_retry=False):
        """Transition job → WAITING_Gx, create approval package, and park the step."""
        gate = _gate_from_error(error_msg)
        if not gate:
            return
        waiting_state = 'WAITING_' + gate
        try:
            FactoryService(self.repo).transition(
                job_id, waiting_state, 'pipeline-system',
                {'source': 'gate_block', 'blocked_target': blocked_target, 'gate': gate},
            )
        except Exception as exc:
            _log.debug('failed to enter %s for job %s: %s', waiting_state, job_id, exc)
            return
        try:
            self._auto_create_approval_package(job_id, gate)
        except Exception as exc:
            _log.debug('failed to create approval package for job %s gate %s: %s', job_id, gate, exc)
        tag = 'gate_retry:' + gate if needs_retry else 'gate_blocked:' + gate
        with self.repo.connect() as c:
            c.execute(
                "update pipeline_steps set status='WAITING_APPROVAL',error=?,updated_at=? where id=?",
                (tag, now(), step_id),
            )

    def _check_gate_block(self, step_id, error):
        """Handle an AGENT step that failed because its gate approval is missing."""
        if not _gate_from_error(error):
            return
        step = self.repo.one('select agent_id, kind, pipeline_id from pipeline_steps where id=?', (step_id,))
        if not step or step['kind'] != 'AGENT':
            return
        pipeline = self.repo.one('select job_id from pipelines where id=?', (step['pipeline_id'],))
        if not pipeline:
            return
        self._enter_gate_wait(step_id, pipeline['job_id'], '', error, needs_retry=True)

    def resume_gate_step(self, job_id, workflow):
        """Unblock the pipeline after a gate approval lands.

        Finds the WAITING_APPROVAL step for the running pipeline of this job,
        marks it COMPLETED (or PENDING for a step that needs to re-run), and
        triggers advance() so downstream steps get scheduled.
        """
        pl = self.repo.one(
            "select id from pipelines where job_id=? and status='RUNNING' order by created_at desc limit 1",
            (job_id,),
        )
        if not pl:
            return False
        step = self.repo.one(
            "select * from pipeline_steps where pipeline_id=? and status='WAITING_APPROVAL' limit 1",
            (pl['id'],),
        )
        if not step:
            return False
        needs_retry = str(step['error'] or '').startswith('gate_retry:')
        with self.repo.connect() as c:
            if needs_retry:
                c.execute(
                    "update pipeline_steps set status='PENDING',error='',updated_at=? where id=?",
                    (now(), step['id']),
                )
            else:
                c.execute(
                    "update pipeline_steps set status='COMPLETED',completed_at=?,error='',updated_at=? where id=?",
                    (now(), now(), step['id']),
                )
        self.advance(pl['id'], workflow)
        return True

    def resolved_params(self, step):
        """A step's params with any forward-references filled from completed predecessors."""
        params = json.loads(step['params'] or '{}')
        sources = PARAM_SOURCES.get(step['agent_id'], {})
        if not sources:
            return params
        pipeline_steps = self.repo.rows('select * from pipeline_steps where pipeline_id=?', (step['pipeline_id'],))
        by_agent = {s['agent_id']: s for s in pipeline_steps if s['agent_id']}
        for param, (source_agent, key) in sources.items():
            source = by_agent.get(source_agent)
            if source and source['status'] == 'COMPLETED':
                outputs = json.loads(source.get('outputs') or '{}')
                if key in outputs:
                    params[param] = outputs[key]
        return params

    def detail(self, pipeline_id):
        p = self.repo.one('select * from pipelines where id=?', (pipeline_id,))
        if not p:
            raise ValueError('pipeline_not_found')
        steps = self.repo.rows('select * from pipeline_steps where pipeline_id=? order by sequence', (pipeline_id,))
        return {**p, 'steps': steps}

    def list_for_job(self, job_id=None):
        if job_id:
            return self.repo.rows('select * from pipelines where job_id=? order by created_at desc', (job_id,))
        return self.repo.rows('select * from pipelines order by created_at desc')
