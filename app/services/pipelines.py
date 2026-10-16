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
from pathlib import Path
from app.repository import uid, now, digest
from app.services import workspace
from app.services.factory_v3 import WorkflowOrchestrator
from app.service import FactoryService, _WAITING_TARGETS
from app.services.policy import PolicyError, gate_enabled
from app.domain.state_machine import TransitionError

_log = logging.getLogger(__name__)


def _gate_from_error(error_str):
    """Extract gate label (e.g. 'G2') from a gate-block error message.

    Agent services raise a variety of error patterns when a gate approval is
    missing.  This function must recognise all of them so the pipeline parks
    the step at WAITING_APPROVAL instead of leaving it FAILED.
    """
    m = _re.search(r'required_gate_approval_missing:(G\d)', error_str)
    if m:
        return m.group(1)
    m = _re.search(r'g(\d)_\w+_approval_required', error_str)
    if m:
        return 'G' + m.group(1)
    # Internal service errors that map to a known gate but do not carry the Gx
    # label in the string.  PlanningGenerationService and
    # MappingAndPlanningService raise these when their artefact-bound approval
    # check fails.
    _KNOWN_GATE_ERRORS = {
        'framework_not_approved': 'G1',
        'analysis_or_specification_not_approved': 'G2',
        'analysis_not_approved': 'G2',
        'specification_not_approved': 'G2',
        'plan_not_approved': 'G3',
    }
    for pattern, gate in _KNOWN_GATE_ERRORS.items():
        if pattern in error_str:
            return gate
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
# planning.py (plan), test_engineering.py (test_suites).
# framework_id is optional: when framework-baseline was skipped, the resolved
# value stays '' and the downstream service proceeds with default conventions.
#
# Deliberately NOT auto-wired here: remediation's ``root_cause_id``. RCA's
# ``rca_hypotheses`` output is a list (one hypothesis per variance kind, each
# with its own id) rather than a single value a source/key pair can resolve to
# -- picking which hypothesis to act on is the reviewer's decision, not
# something a prior step's output alone determines.
PARAM_SOURCES = {
    'migration-specification': {'analysis_id': ('ace-analysis', 'analysis_document'),
                                 'framework_id': ('framework-baseline', 'framework_template')},
    'planning': {'analysis_id': ('ace-analysis', 'analysis_document'),
                 'framework_id': ('framework-baseline', 'framework_template'),
                 'specification_id': ('migration-specification', 'migration_specification_yaml')},
    'code-generation': {'plan_id': ('planning', 'plan')},
    'test-engineering': {'plan_id': ('planning', 'plan')},
    'variance-analysis': {'package_id': ('test-engineering', 'test_suites')},
    # The feedback agent repairs what code-generation produced, so it needs the
    # plan the project was generated from and the root it was written to.
    # 'source_code' is code-generation's own declared output and holds that path.
    'feedback': {'plan_id': ('planning', 'plan'),
                 'project_root': ('code-generation', 'source_code')},
}
# Extra depends_on edges this scheduler adds on top of the catalogue's own
# dependency graph, keyed by role: an agent that reads a repository directly
# must wait for that repository's CLONE step even though the catalogue's
# dependency graph (agent-to-agent only) has no way to express that.
REPO_CONSUMERS = {'repository-intake': 'ACE', 'ace-analysis': 'ACE', 'framework-baseline': 'FRAMEWORK'}

# A governance hold, derived from a step parked at an unapproved gate. Distinct
# from the operator's PAUSED: an operator may undo their own pause, but only an
# approver releases a hold, so the two must never be the same value.
HELD = 'WAITING_APPROVAL'
# Pipeline statuses in which the scheduler still owns the pipeline.
_SCHEDULER_OWNED = ('RUNNING', HELD)

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
    # 'build-validation' above has no entry in config/agent-catalogue.v1.json,
    # so no step for it can ever exist and _LIFECYCLE_AGENT['BUILD_VALIDATED']
    # named an agent that could never complete -- the walk stopped dead there.
    # feedback is listed after it so the reverse map resolves BUILD_VALIDATED to
    # this agent instead: it re-runs the quality gate over the generated project
    # and only completes when the gate is clean, which is exactly what reaching
    # BUILD_VALIDATED is supposed to mean.
    'feedback':              'BUILD_VALIDATED',
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
    # The feedback agent's own re-validation writes this row, so the advance to
    # BUILD_VALIDATED is backed by a gate verdict rather than taken on trust.
    'BUILD_VALIDATED':     ('quality_runs',        'job_id'),
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


class PipelineContextAccumulator:
    """Shared context that accumulates across every agent in a pipeline.

    Each agent's execution result — outputs, evidence, reasoning provenance,
    metrics, and the skill that guided it — is recorded after the agent
    completes.  Before the next agent runs, the full accumulated snapshot is
    loaded and passed to it via ``AgentContext.params['_pipeline_context']``,
    so every agent sees what every prior agent produced and which skill drove
    each decision.  Nothing is lost at agent boundaries.
    """

    def __init__(self, repo):
        self.repo = repo

    def record(self, pipeline_id, step_id, agent_id, job_id, dispatch_result, skill=None):
        """Write what this agent produced into the shared context store."""
        evidence_ids = [e.get('id', '') for e in (dispatch_result.get('evidence') or [])]
        skill_summary = ''
        if skill:
            # First paragraph of SKILL.md as a compact summary.
            lines = (skill.text or '').strip().split('\n\n')
            skill_summary = lines[0].strip() if lines else ''
            # Cap at 500 chars to keep the accumulated payload reasonable.
            if len(skill_summary) > 500:
                skill_summary = skill_summary[:497] + '...'
        row = {
            'id': uid(),
            'pipeline_id': pipeline_id,
            'step_id': step_id,
            'agent_id': agent_id,
            'job_id': job_id,
            'outputs': json.dumps(dispatch_result.get('outputs') or {}, default=str),
            'evidence_ids': json.dumps(evidence_ids),
            'metrics': json.dumps(dispatch_result.get('metrics') or {}, default=str),
            'reasoning': json.dumps(dispatch_result.get('reasoning') or {}, default=str),
            'error': dispatch_result.get('error') or '',
            'skill_name': skill.name if skill else '',
            'skill_hash': skill.content_hash if skill else '',
            'skill_summary': skill_summary,
            'run_id': dispatch_result.get('run_id') or '',
            'status': dispatch_result.get('status') or '',
            'created_at': now(),
        }
        with self.repo.connect() as c:
            # Upsert: a retried step replaces its previous context row.
            c.execute('delete from pipeline_context where pipeline_id=? and agent_id=?',
                      (pipeline_id, agent_id))
            self.repo.insert(c, 'pipeline_context', row)

    def snapshot(self, pipeline_id, job_id):
        """Build the full accumulated context from all completed agents.

        Returns a dict with:
          'agents'          — dict keyed by agent_id, each carrying outputs, evidence,
                              metrics, reasoning, skill info
          'agent_chain'     — ordered list of agent_ids in execution order
          'skills_summary'  — combined skill summaries from all agents
          'evidence_summary'— flat list of {kind, id, producer} across all agents
        """
        rows = self.repo.rows(
            'select * from pipeline_context where pipeline_id=? order by created_at',
            (pipeline_id,))
        if not rows:
            return {}
        agents = {}
        agent_chain = []
        all_skills = []
        all_evidence = []
        for r in rows:
            aid = r['agent_id']
            agent_chain.append(aid)
            evidence_ids = json.loads(r['evidence_ids'] or '[]')
            entry = {
                'agent_id': aid,
                'status': r['status'],
                'run_id': r['run_id'],
                'outputs': json.loads(r['outputs'] or '{}'),
                'evidence_ids': evidence_ids,
                'metrics': json.loads(r['metrics'] or '{}'),
                'reasoning': json.loads(r['reasoning'] or '{}'),
                'skill_name': r['skill_name'],
                'skill_hash': r['skill_hash'],
                'skill_summary': r['skill_summary'],
                'error': r['error'],
            }
            agents[aid] = entry
            if r['skill_name'] and r['skill_summary']:
                all_skills.append('[%s] %s' % (aid, r['skill_summary']))
            # Build evidence summary from the evidence table for richer detail.
            for eid in evidence_ids:
                ev = self.repo.one('select id, kind, producer from evidence where id=?', (eid,))
                if ev:
                    all_evidence.append({'id': ev['id'], 'kind': ev['kind'], 'producer': ev['producer']})
        return {
            'agents': agents,
            'agent_chain': agent_chain,
            'skills_summary': '\n'.join(all_skills),
            'evidence_summary': all_evidence,
        }


class StepLogger:
    """Append-only structured log for a single pipeline step.

    Each call writes one row to ``pipeline_step_logs``.  The front-end's Logs
    tab already renders ``[timestamp, level, message]`` tuples with CSS classes
    for INFO, WARN, ERROR and SUCCESS — this is the back-end half of that
    contract.  The 5-second auto-refresh on a RUNNING pipeline means entries
    appear live while the step is executing.
    """

    def __init__(self, repo, step_id):
        self.repo = repo
        self.step_id = step_id

    def info(self, msg):
        self._write('INFO', msg)

    def warn(self, msg):
        self._write('WARN', msg)

    def error(self, msg):
        self._write('ERROR', msg)

    def success(self, msg):
        self._write('SUCCESS', msg)

    def _write(self, level, message):
        with self.repo.connect() as c:
            self.repo.insert(c, 'pipeline_step_logs', {
                'id': uid(), 'step_id': self.step_id,
                'level': level, 'message': str(message),
                'created_at': now()})


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

    def _role_content_path(self, by_role, role, base):
        """Where a role's actual repository content lands.

        ``RepoCloneService.clone`` nests a role's clone under its own
        repository name (``<role-dir>/<repo_name>``) rather than dumping it
        directly into the fixed role directory, so every step reading that
        role's content must join the same way. Falls back to the bare role
        directory when the role has no ``repo_url`` to derive a name from
        (an unconfigured role, or one deliberately skipped) -- there is no
        clone to point at either way, so this is only ever a harmless default.
        """
        r = by_role.get(role, {})
        url = r.get('repo_url', '')
        role_dir = base + '/' + role.lower()
        if not url:
            return role_dir
        try:
            return role_dir + '/' + workspace.repo_name(url)
        except workspace.WorkspaceError:
            return role_dir

    def _seed_params(self, agent_id, by_role, workspace_dir, service_name, package_name):
        ace = by_role.get('ACE', {})
        base = workspace_dir.rstrip('/')
        ace_path = self._role_content_path(by_role, 'ACE', base)
        framework_path = self._role_content_path(by_role, 'FRAMEWORK', base)
        seeds = {
            'repository-intake': {'path': ace_path, 'branch': ace.get('branch', 'main')},
            'ace-analysis': {'path': ace_path, 'version': '1.0.0'},
            'framework-baseline': {'path': framework_path, 'golden_path': framework_path, 'version': '1.0.0'},
            'migration-specification': {'version': '1.0.0'},
            'planning': {'version': '1.0.0'},
            # ace_path is the cloned ACE repository, read so the generated
            # transformers carry the real ESQL assignments rather than a
            # passthrough. This step runs after ace-analysis, which REPO_CONSUMERS
            # makes wait for CLONE:ACE, and the workspace is only cleared once
            # the pipeline ends -- so the tree is still there at this point.
            'code-generation': {'output_root': base + '/springboot-output',
                                 'ace_path': base + '/ace',
                                 'service_name': service_name or 'migrated-service',
                                 'package_name': package_name or 'com.example.migrated'},
            # project_root is normally resolved from code-generation's output by
            # PARAM_SOURCES; this is the fallback for the same location.
            'feedback': {'project_root': base + '/springboot-output', 'max_passes': 3},
            # Both static checks are opt-in at the agent level (``context.param(name,
            # False)``); seeding them True here is what turns them on for a pipeline
            # run, so the parity/hardcoding HTML reports actually get generated
            # instead of silently staying off by default.
            'variance-analysis': {'parity': True, 'hardcoding': True},
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
        if p['status'] not in _SCHEDULER_OWNED:
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
        self._set_status(pipeline_id, 'RUNNING')
        # advance() rather than _schedule_ready(): it re-derives the halt reason
        # from the steps, so resuming a pipeline whose gate is still unapproved
        # lands back on WAITING_APPROVAL and schedules nothing. That makes
        # "an operator cannot resume past a gate" structural rather than a
        # separate check that could be forgotten.
        self.advance(pipeline_id, workflow)
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
        if p['status'] not in ('DRAFT', 'READY', 'RUNNING', 'PAUSED', HELD):
            raise PipelineError('pipeline_not_cancellable:' + p['status'])
        rows = self.repo.rows('select * from pipeline_steps where pipeline_id=?', (pipeline_id,))
        with self.repo.connect() as c:
            for step in rows:
                if step['status'] == 'QUEUED':
                    if step['queue_id']:
                        workflow.cancel(step['queue_id'])
                    c.execute("update pipeline_steps set status='CANCELLED',queue_id='',updated_at=? where id=?", (now(), step['id']))
                elif step['status'] in ('PENDING', HELD):
                    # A held step is cancellable like an unstarted one. The job
                    # stays at WAITING_Gx and its approval package stays open:
                    # closing one is a governed act for an approver, not
                    # something cancelling a pipeline may do.
                    c.execute("update pipeline_steps set status='CANCELLED',queue_id='',updated_at=? where id=?", (now(), step['id']))
            c.execute("update pipelines set status='CANCELLED',completed_at=?,updated_at=? where id=?", (now(), now(), pipeline_id))
        return self.detail(pipeline_id)

    def _ready_steps(self, pipeline_id):
        """Steps whose dependencies are all satisfied and that can be scheduled.

        A dependency is satisfied when it is COMPLETED or SKIPPED.  When every
        dependency of a PENDING step is either COMPLETED or SKIPPED, and at
        least one is SKIPPED, the step is cascade-skipped rather than
        scheduled — an intentionally omitted agent must not block its
        dependents, but those dependents must not run either since they would
        be operating on missing inputs.
        """
        rows = self.repo.rows('select * from pipeline_steps where pipeline_id=? order by sequence', (pipeline_id,))
        status = {r['id']: r['status'] for r in rows}
        ready = []
        cascade_skip = []
        for r in rows:
            if r['status'] != 'PENDING':
                continue
            deps = json.loads(r['depends_on'] or '[]')
            dep_statuses = [status.get(d, 'PENDING') for d in deps]
            if not deps or all(s in ('COMPLETED', 'SKIPPED') for s in dep_statuses):
                if any(s == 'SKIPPED' for s in dep_statuses):
                    # At least one dependency was skipped — cascade-skip this step.
                    cascade_skip.append(r)
                else:
                    ready.append(r)
        # Apply cascade skips immediately so downstream steps see the SKIPPED
        # status on the next _ready_steps call.
        if cascade_skip:
            with self.repo.connect() as c:
                for r in cascade_skip:
                    skipped_dep = ''
                    for d in json.loads(r['depends_on'] or '[]'):
                        if status.get(d) == 'SKIPPED':
                            # Find the agent name of the skipped dependency for a readable reason.
                            dep_step = next((s for s in rows if s['id'] == d), None)
                            skipped_dep = dep_step['agent_id'] if dep_step and dep_step['agent_id'] else d[:12]
                            break
                    reason = 'skip_kind:dependency;depends on skipped agent %s' % skipped_dep
                    c.execute("update pipeline_steps set status='SKIPPED',error=?,updated_at=? where id=? and status='PENDING'",
                              (reason, now(), r['id']))
                    status[r['id']] = 'SKIPPED'
        return ready, rows

    def _schedule_ready(self, pipeline_id, workflow):
        """Schedule the single next ready step.

        Only one step is queued at a time so agents execute strictly
        sequentially -- no parallel execution.  CLONE steps that are both
        ready are also serialised: the worker processes them one at a time.
        """
        job_id = self.repo.one('select job_id from pipelines where id=?', (pipeline_id,))['job_id']
        ready, rows = self._ready_steps(pipeline_id)
        if self._halt_reason(rows):
            return
        # Sequential execution: if any step is already in flight, wait for it
        # to finish before scheduling the next one.
        if any(r['status'] in ('QUEUED', 'RUNNING') for r in rows):
            return
        # Schedule only the first ready step (lowest sequence number).
        if not ready:
            return
        step = ready[0]
        q = workflow.schedule(job_id, 'pipeline_step', {'pipeline_id': pipeline_id, 'step_id': step['id']},
                              idempotency_key='pipeline-step:' + step['id'])
        with self.repo.connect() as c:
            claimed = c.execute("update pipeline_steps set status='QUEUED',queue_id=?,updated_at=? where id=? and status='PENDING'",
                                (q['id'], now(), step['id'])).rowcount
        # ``schedule`` is idempotent on the key, so for a step that has been
        # through the queue before it returns the *pre-existing* row
        # untouched -- including one cancelled by a pause, a hold or a halt,
        # which ``claim`` ignores forever, and one sitting on a retry
        # backoff. Reviving it here is what makes a resumed step actually
        # run instead of sitting at QUEUED behind a dead row. Guarded by the
        # rowcount above so a row another worker claimed in the same instant
        # is never yanked out from under it.
        if claimed and q['status'] != 'READY':
            workflow.revive(q['id'])

    def _set_status(self, pipeline_id, status, final=False):
        """The one place ``pipelines.status`` is written.

        Mirrors how ``FactoryService.transition`` is the only authority for
        ``jobs.state``: a status can then never be written without its
        ``updated_at``, and the hold cannot be set from two places under two
        different rules. ``final`` stamps ``completed_at``, which means "nothing
        is executing any more" -- so a hold never gets one.
        """
        with self.repo.connect() as c:
            if final:
                c.execute('update pipelines set status=?,completed_at=?,updated_at=? where id=?',
                          (status, now(), now(), pipeline_id))
            else:
                c.execute("update pipelines set status=?,completed_at='',updated_at=? where id=?",
                          (status, now(), pipeline_id))

    @staticmethod
    def _halt_reason(rows):
        """Why nothing further may be scheduled: 'FAILED', the hold, or ''.

        Strict fail-fast: one FAILED step stops the whole pipeline, including
        branches that never depended on it. A failure part-way through a
        migration means the result is wrong, not that one leaf is wrong, so
        continuing would spend gate approvals and agent runs on something nobody
        will ship.

        FAILED outranks a hold because the two need different people. A hold is
        cleared by an approver and then resumes by itself; a failure is cleared
        only by ``retry_step``. Reporting the hold while a step has already
        failed would send the operator to find an approver for a pipeline that
        cannot continue even once the approval lands.
        """
        statuses = {r['status'] for r in rows}
        return 'FAILED' if 'FAILED' in statuses else (HELD if HELD in statuses else '')

    def _park_queue_row(self, queue_id, workflow):
        """Stop this step's queue row from running, reversibly.

        ``WorkflowOrchestrator.complete`` turns a failed activity into a RETRY
        scheduled ``2**attempt`` seconds out, so without this a step the
        pipeline has deliberately halted -- or parked at a gate -- is re-claimed
        and re-run seconds later, trampling the park. ``cancel`` sets
        ``cancel_requested``, which both hides the row from ``claim`` and makes
        ``complete`` resolve it to CANCELLED instead of RETRY.
        ``_schedule_ready`` revives it when the halt or hold lifts.
        """
        if not queue_id:
            return
        try:
            workflow.cancel(queue_id)
        except ValueError:
            pass

    def _retract_queued(self, rows, workflow):
        """Pull every scheduled-but-unclaimed step back to PENDING.

        A step the worker already claimed (RUNNING) cannot be preempted, but one
        merely QUEUED must not start after the halt -- otherwise "everything
        that has not run stays PENDING" stops being true at the worker's next
        poll.
        """
        for step in rows:
            if step['status'] != 'QUEUED':
                continue
            self._park_queue_row(step['queue_id'], workflow)
            with self.repo.connect() as c:
                c.execute("update pipeline_steps set status='PENDING',queue_id='',updated_at=? where id=? and status='QUEUED'",
                          (now(), step['id']))

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

        PAUSED/CANCELLED/COMPLETED pipelines must not be touched here: a step
        that was RUNNING when cancel() was called cannot be preempted, and a
        PAUSED pipeline must wait for an explicit resume(). FAILED pipelines are
        an exception: if every step has since recovered via retry (no step
        remains FAILED and no work is still in flight), the pipeline is upgraded
        to COMPLETED so the UI does not permanently show FAILED after a
        successful retry resolves all errors.
        """
        p = self.repo.one('select status,job_id from pipelines where id=?', (pipeline_id,))
        if not p or p['status'] in ('PAUSED', 'CANCELLED', 'COMPLETED'):
            return
        # A gate switched off after a step was already parked on it must release
        # that step, or the pipeline stays held on a gate nobody has to sign.
        self._release_disabled_gate_holds(p['job_id'], pipeline_id)
        _, rows = self._ready_steps(pipeline_id)
        halt = self._halt_reason(rows)
        if halt:
            # Strict fail-fast, or a governance hold. Either way nothing further
            # is scheduled and anything merely queued comes back to PENDING. The
            # hold is never stamped terminal: an approval has to be able to
            # drive it back to RUNNING, which is why HELD is absent from the
            # early-return above.
            self._retract_queued(rows, workflow)
            running = any(r['status'] == 'RUNNING' for r in rows)
            self._set_status(pipeline_id, halt, final=(halt == 'FAILED' and not running))
            return
        self._schedule_ready(pipeline_id, workflow)
        _, rows = self._ready_steps(pipeline_id)
        blocked = self._blocked(rows)
        still_active = any(r['status'] in ('QUEUED', 'RUNNING') for r in rows) or \
            any(r['status'] == 'PENDING' and r['id'] not in blocked for r in rows)
        if still_active:
            # A retry that cleared the last failure, or an approval that
            # released the last hold, leaves the pipeline readable as RUNNING
            # again rather than stuck on the status that halted it.
            if p['status'] != 'RUNNING':
                self._set_status(pipeline_id, 'RUNNING')
            return
        self._set_status(pipeline_id, 'COMPLETED', final=True)

    def mark_step(self, step_id, status, outputs=None, error=''):
        with self.repo.connect() as c:
            if status == 'RUNNING':
                c.execute("update pipeline_steps set status=?,started_at=?,updated_at=? where id=?", (status, now(), now(), step_id))
                return
            # A step already parked at a gate must not be dragged back out of
            # it. ``app/worker.py`` records the same failure twice -- once in
            # ``_execute`` and again in ``process_one``'s catch-all -- and
            # without this guard the second write replaced WAITING_APPROVAL with
            # FAILED and then re-attempted the WAITING_Gx transition the job had
            # already made, which the state machine rejects. The visible effect
            # was that a gate-blocked step ended FAILED and the hold was lost
            # every time it ran through the worker.
            written = c.execute("update pipeline_steps set status=?,completed_at=?,outputs=?,error=?,updated_at=? where id=? and status!=?",
                                (status, now(), json.dumps(outputs or {}, default=str), error, now(), step_id, HELD)).rowcount
        if not written:
            return
        if status == 'COMPLETED':
            self._try_lifecycle_transition(step_id)
        elif status == 'FAILED':
            # A failure halts the pipeline, so this step's own queue row has to
            # stop as well. Left alone, ``WorkflowOrchestrator.complete`` turns
            # it into a RETRY scheduled 2**attempt seconds out and the worker
            # re-runs the step behind the operator's back -- and for a gate
            # failure that re-run tramples the park that is about to be made
            # just below. Parking first makes ``complete`` resolve the row to
            # CANCELLED instead.
            row = self.repo.one('select queue_id from pipeline_steps where id=?', (step_id,))
            self._park_queue_row((row or {}).get('queue_id'), WorkflowOrchestrator(self.repo))
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
        # The queue row is revived by _schedule_ready when this step is next
        # scheduled, which also defeats the 2**attempt backoff -- that backoff
        # is for a transient blip, not for a human who just fixed the cause.
        # Doing it here instead would let the queue run the step even while
        # another failure still halts the pipeline.
        self._set_status(step['pipeline_id'], 'RUNNING')
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
        """Park the step, create an approval package, and optionally move the job to WAITING_Gx.

        The approval package and the step park are pipeline-level operations
        that must happen regardless of whether the job lifecycle state machine
        accepts the WAITING_Gx transition.  The lifecycle transition is
        attempted best-effort — when the job is not yet at the state that
        permits WAITING_Gx (e.g. the job is at FRAMEWORK_BASELINED but
        WAITING_G2 is only legal from ACE_ANALYSED), the transition is
        skipped but the step is still parked and the approval inbox still
        shows the pending gate.  This was the root cause of the "logs show
        the gate hold but the inbox is empty" bug.
        """
        gate = _gate_from_error(error_msg)
        if not gate:
            return
        waiting_state = 'WAITING_' + gate
        # Best-effort lifecycle transition.  The job may not be at the right
        # state for WAITING_Gx yet (the state machine constrains which edges
        # exist), so a failure here is logged but does not prevent the step
        # park and approval package creation below.
        try:
            FactoryService(self.repo).transition(
                job_id, waiting_state, 'pipeline-system',
                {'source': 'gate_block', 'blocked_target': blocked_target, 'gate': gate},
            )
        except Exception as exc:
            _log.info('lifecycle transition to %s deferred for job %s: %s '
                      '(step will still be parked and approval package created)',
                      waiting_state, job_id, exc)
        # Always create the approval package so the inbox shows the pending
        # gate.  Idempotent: a second call for the same gate returns the
        # existing package.
        try:
            self._auto_create_approval_package(job_id, gate)
        except Exception as exc:
            _log.warning('failed to create approval package for job %s gate %s: %s', job_id, gate, exc)
        # The tag records which gate parked this step; the original message is
        # kept after it so the UI's error pane still has something to show and
        # ``_gate_from_error`` can still read the gate back out.
        tag = ('gate_retry:' if needs_retry else 'gate_blocked:') + gate
        if error_msg:
            tag = tag + ';' + error_msg
        with self.repo.connect() as c:
            # Only park the step if it has not already completed — a step that
            # ran successfully and then triggered a gate-blocked lifecycle advance
            # must stay COMPLETED so the lifecycle query can still count it as a
            # waypoint after the gate is approved.
            parked = c.execute(
                "update pipeline_steps set status='WAITING_APPROVAL',error=?,updated_at=? "
                "where id=? and status!='COMPLETED'",
                (tag, now(), step_id),
            ).rowcount
            row = c.execute('select pipeline_id,queue_id from pipeline_steps where id=?', (step_id,)).fetchone()
        if not parked or not row:
            # Nothing was parked: the step stayed COMPLETED, so there is nothing
            # to re-run and the governance hold lives on the job as WAITING_Gx.
            # Marking the pipeline held here would claim a hold that no step
            # backs, and the next ``advance`` would flip it straight back.
            return
        # Its queue row has to stop too, or the retry backoff re-runs the step a
        # couple of seconds from now and overwrites the park.
        self._park_queue_row(row['queue_id'], WorkflowOrchestrator(self.repo))
        if self._halt_reason(self.repo.rows('select * from pipeline_steps where pipeline_id=?', (row['pipeline_id'],))) == HELD:
            self._set_status(row['pipeline_id'], HELD)

    def _release_disabled_gate_holds(self, job_id, pipeline_id):
        """Free steps parked at a gate that has since been switched off in the database.

        Without this, disabling a gate only affects steps the pipeline has not
        reached yet. ``_halt_reason`` reports HELD for any WAITING_APPROVAL step
        and ``advance`` returns before scheduling anything, while the only
        un-park path (``resume_gate_step``) is reachable solely from the approval
        endpoints and ``retry_step`` refuses a step that is not FAILED. A step
        parked before its gate was disabled would therefore stay parked forever,
        which is the opposite of what disabling the gate is for.

        Release uses the same rule as ``resume_gate_step``: a step that actually
        executed is completed, one that never ran goes back to PENDING so it
        runs now.
        """
        released = []
        for s in self.repo.rows('select * from pipeline_steps where pipeline_id=? and status=?',
                                (pipeline_id, HELD)):
            gate = _gate_from_error(str(s['error'] or ''))
            if not gate or gate_enabled(self.repo, gate):
                continue
            needs_retry = str(s['error'] or '').startswith('gate_retry:')
            ran = bool(s['started_at']) and not needs_retry
            with self.repo.connect() as c:
                if ran:
                    c.execute("update pipeline_steps set status='COMPLETED',completed_at=?,error='',updated_at=? where id=?",
                              (now(), now(), s['id']))
                else:
                    c.execute("update pipeline_steps set status='PENDING',error='',updated_at=? where id=?",
                              (now(), s['id']))
            released.append(gate)
        if not released:
            return False
        _log.info('released %d step(s) held at disabled gate(s) %s for job %s',
                  len(released), ','.join(sorted(set(released))), job_id)
        self._leave_waiting_state(job_id)
        return True

    def _leave_waiting_state(self, job_id):
        """Walk a job off WAITING_Gx once that gate no longer requires a signature.

        ``resume_after_clarification`` cannot do this: WAITING_Gx is absent from
        ``_LIFECYCLE_ORDER``, so its index lookup returns -1 and it bails out.
        The post-gate targets are the same ones ``FactoryService.approve`` uses.
        """
        job = self.repo.one('select state from jobs where id=?', (job_id,))
        for target in (_WAITING_TARGETS.get((job or {}).get('state', '')) or []):
            try:
                FactoryService(self.repo).transition(
                    job_id, target, 'pipeline-system', {'source': 'gate_disabled_by_policy'})
                return
            except Exception as exc:
                _log.debug('cannot leave %s for %s via %s: %s',
                           (job or {}).get('state'), job_id, target, exc)

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

    def resume_gate_step(self, job_id, workflow, gate=''):
        """Unblock the pipeline after a gate approval lands.

        Finds the WAITING_APPROVAL step for the running pipeline of this job,
        marks it COMPLETED (or PENDING for a step that needs to re-run), and
        triggers advance() so downstream steps get scheduled.

        If no RUNNING pipeline exists (the pipeline already finished before the
        gate approval arrived) fall back to re-walking the lifecycle by job_id
        so the job still advances past the gate.
        """
        pl = self.repo.one(
            "select id from pipelines where job_id=? and status in ('RUNNING',?) order by created_at desc limit 1",
            (job_id, HELD),
        )
        if not pl:
            # Pipeline already completed before the gate was approved — re-walk
            # the lifecycle from the current job state so it advances now.
            self.resume_after_clarification(job_id)
            return True
        parked = self.repo.rows(
            "select * from pipeline_steps where pipeline_id=? and status=?",
            (pl['id'], HELD),
        )
        # Release only what *this* gate was blocking. The tag records the gate
        # that parked each step, so approving G1 must not release a step parked
        # at G2 -- which the old unfiltered ``limit 1`` did, handing the later
        # gate's work an approval nobody granted.
        if gate:
            parked = [s for s in parked if _gate_from_error(str(s['error'] or '')) == gate]
        if not parked:
            # No parked step for this gate (e.g. the triggering step stayed
            # COMPLETED) — still re-walk the lifecycle to advance the job.
            self.resume_after_clarification(job_id)
            return True
        for step in parked:
            needs_retry = str(step['error'] or '').startswith('gate_retry:')
            # A step that never actually executed must not be recorded as
            # COMPLETED: ``_try_lifecycle_transition`` counts completed agent
            # steps as lifecycle waypoints, so doing that would let an approval
            # advance the lifecycle past work that never ran.
            ran = bool(step['started_at']) and not needs_retry
            with self.repo.connect() as c:
                if ran:
                    c.execute(
                        "update pipeline_steps set status='COMPLETED',completed_at=?,error='',updated_at=? where id=?",
                        (now(), now(), step['id']),
                    )
                else:
                    c.execute(
                        "update pipeline_steps set status='PENDING',error='',updated_at=? where id=?",
                        (now(), step['id']),
                    )
        self.advance(pl['id'], workflow)
        return True

    def resolved_params(self, step):
        """A step's params with any forward-references filled from completed predecessors.

        When a source agent was SKIPPED (intentionally disabled), the param is
        set to ``''`` so the downstream service receives an explicit empty value
        and can proceed with default behaviour, rather than raising
        ``missing_param`` because the key was never set.
        """
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
            elif source and source['status'] == 'SKIPPED':
                # Source agent was intentionally skipped — provide an empty
                # value so the downstream agent can proceed with defaults.
                params.setdefault(param, '')
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
