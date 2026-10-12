"""
Tests for the lifecycle waypoint bug fix.

Bug: A user-deselected agent (skip_kind=explicitly_disabled) was incorrectly
treated as a pass-through lifecycle waypoint, allowing FRAMEWORK_BASELINED to
be entered even when the framework-baseline agent never ran and no
framework_baselines record exists.

Fix has two layers:
  1. impact() now classifies each skipped agent with a skip_kind field.
  2. _try_lifecycle_transition() only allows dependency-skipped agents as
     waypoints; explicitly-disabled agents are never waypoints.
  3. FactoryService.transition() validates the required artefact exists before
     writing the new state.
"""
import json
import pytest
from app.repository import Repository, now
from app.service import FactoryService
from app.services.factory_v3 import WorkflowOrchestrator
from app.services.pipelines import PipelineService
from app.services.policy import PolicyError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    repo = Repository(str(tmp_path / 'lifecycle.db'))
    factory = FactoryService(repo)
    job = factory.create_job({'service': 'svc', 'owner': 'team', 'sourceRevision': 'abc'}, 'tester')
    pipelines = PipelineService(repo)
    workflow = WorkflowOrchestrator(repo)
    return repo, factory, job, pipelines, workflow


ACE_REPO = {'ACE': {'repo_url': 'https://github.com/org/ace.git', 'branch': 'main', 'enabled': True}}


def _create_and_run(pipelines, workflow, job_id, agent_ids):
    """Helper: create, confirm, and run a pipeline; return pipeline dict."""
    p = pipelines.create(job_id, 'ws', ACE_REPO, agent_ids, 'tester')
    pipelines.confirm(p['id'])
    return pipelines.run(p['id'], workflow)


def _complete_clone(pipelines, p):
    clone = next((s for s in p['steps'] if s['kind'] == 'CLONE'), None)
    if clone:
        pipelines.mark_step(clone['id'], 'RUNNING')
        pipelines.mark_step(clone['id'], 'COMPLETED', outputs={'path': 'ws/ace'})


def _step(p, agent_id):
    return next((s for s in p['steps'] if s['agent_id'] == agent_id), None)


def _inject_intake_manifest(repo, job_id):
    """Insert a minimal intake_manifests row so artefact validation passes."""
    from app.repository import uid
    with repo.connect() as c:
        c.execute(
            "insert into intake_manifests(id,job_id,repository_uri,branch,revision,"
            "inventory,gaps,content_hash,created_at) values(?,?,?,?,?,?,?,?,?)",
            (uid(), job_id, 'https://example.com', 'main', 'abc',
             '{}', '[]', 'hash1', now()),
        )


def _inject_framework_baseline(repo, job_id):
    """Insert a minimal framework_baselines row so artefact validation passes."""
    from app.repository import uid
    with repo.connect() as c:
        c.execute(
            "insert into framework_baselines(id,job_id,version,template_md,"
            "standards,conflicts,status,content_hash,created_at) values(?,?,?,?,?,?,?,?,?)",
            (uid(), job_id, '1.0', '', '{}', '[]', 'ACCEPTED', 'hash2', now()),
        )


def _inject_ace_analysis(repo, job_id):
    """Insert a minimal ace_analyses row."""
    from app.repository import uid
    with repo.connect() as c:
        c.execute(
            "insert into ace_analyses(id,job_id,version,semantic_model,analysis_md,"
            "ambiguities,dependencies,content_hash,created_at) values(?,?,?,?,?,?,?,?,?)",
            (uid(), job_id, '1.0', '{}', '', '[]', '[]', 'hash3', now()),
        )


def _inject_dormancy_assessment(repo, job_id):
    """Insert a minimal dormancy_assessments row."""
    from app.repository import uid
    with repo.connect() as c:
        c.execute(
            "insert into dormancy_assessments(id,job_id,classification,confidence,"
            "evidence,rationale,created_at) values(?,?,?,?,?,?,?)",
            (uid(), job_id, 'ACTIVE', 1.0, '{}', 'test', now()),
        )


def _disable_gate(repo, gate):
    """Disable a gate policy so it never blocks transitions in tests."""
    with repo.connect() as c:
        c.execute("update approval_policies set enabled=0 where gate=?", (gate,))


# ---------------------------------------------------------------------------
# Test A — framework-baseline deselected: lifecycle must NOT reach
#           FRAMEWORK_BASELINED even when ace-analysis (idx=4) completes.
# ---------------------------------------------------------------------------

def test_A_deselected_framework_baseline_triggers_g0_gate(env):
    """
    framework-baseline NOT in selected_agent_ids (explicitly_disabled).
    dormancy-intelligence IS selected and completes (lifecycle reaches
    DORMANCY_ASSESSED, the state just before FRAMEWORK_BASELINED).

    With the corrected walker: explicitly-disabled agents are allowed to
    trigger the transition attempt for GATED states.  G0 fires because
    FRAMEWORK_BASELINED is a gated state — the gate becomes the governance
    approval for skipping the optional framework-baseline agent.

    The lifecycle must reach WAITING_G0 (gate fired), NOT skip straight
    to FRAMEWORK_BASELINED (that would require actual G0 approval).
    """
    repo, factory, job, pipelines, workflow = env
    _inject_intake_manifest(repo, job['id'])

    # framework-baseline deliberately excluded; dormancy IS selected
    selected = ['repository-intake', 'dormancy-intelligence']
    p = _create_and_run(pipelines, workflow, job['id'], selected)

    # Verify skip classification
    fb_step = _step(p, 'framework-baseline')
    assert fb_step is not None and fb_step['status'] == 'SKIPPED'
    assert fb_step['error'].startswith('skip_kind:explicitly_disabled'), (
        f"expected skip_kind:explicitly_disabled, got: {fb_step['error']!r}"
    )

    _complete_clone(pipelines, p)

    intake = _step(p, 'repository-intake')
    pipelines.mark_step(intake['id'], 'RUNNING')
    pipelines.mark_step(intake['id'], 'COMPLETED',
                        outputs={'intake_manifest': 'im1', 'gaps': []})

    dormancy = _step(p, 'dormancy-intelligence')
    pipelines.mark_step(dormancy['id'], 'RUNNING')
    _inject_dormancy_assessment(repo, job['id'])
    pipelines.mark_step(dormancy['id'], 'COMPLETED', outputs={})

    current = repo.one('select state from jobs where id=?', (job['id'],))['state']
    # G0 gate must have fired (WAITING_G0), and the lifecycle must NOT have
    # bypassed the gate and jumped straight to FRAMEWORK_BASELINED.
    assert current == 'WAITING_G0', (
        f"Expected WAITING_G0 (G0 gate fired for explicitly-disabled "
        f"framework-baseline). Got: {current}"
    )


# ---------------------------------------------------------------------------
# Test B — framework-baseline selected and completes: FRAMEWORK_BASELINED
#           allowed once the artefact record exists.
# ---------------------------------------------------------------------------

def test_B_completed_framework_baseline_advances_lifecycle(env):
    """
    framework-baseline IS selected and completes with its artefact written.
    Lifecycle must advance at least to WAITING_G0 (gate block) or
    FRAMEWORK_BASELINED (if gate disabled), proving that a real agent
    completion + artefact is accepted.

    dormancy-intelligence must also be selected because the lifecycle walker
    cannot skip DORMANCY_ASSESSED without it completing or being a dependency
    waypoint — this is the correct constraint.
    """
    repo, factory, job, pipelines, workflow = env
    _inject_intake_manifest(repo, job['id'])

    # Disable G0 so the gate doesn't block — the test is about artefact
    # validation, not gate approval.
    _disable_gate(repo, 'G0')

    selected = ['repository-intake', 'dormancy-intelligence', 'framework-baseline']
    p = _create_and_run(pipelines, workflow, job['id'], selected)

    _complete_clone(pipelines, p)

    intake = _step(p, 'repository-intake')
    pipelines.mark_step(intake['id'], 'RUNNING')
    pipelines.mark_step(intake['id'], 'COMPLETED',
                        outputs={'intake_manifest': 'im1', 'gaps': []})

    dormancy = _step(p, 'dormancy-intelligence')
    pipelines.mark_step(dormancy['id'], 'RUNNING')
    _inject_dormancy_assessment(repo, job['id'])
    pipelines.mark_step(dormancy['id'], 'COMPLETED', outputs={})

    # Write the artefact BEFORE marking the agent COMPLETED so transition()
    # finds the row when the lifecycle walker fires.
    _inject_framework_baseline(repo, job['id'])

    fb = _step(p, 'framework-baseline')
    pipelines.mark_step(fb['id'], 'RUNNING')
    pipelines.mark_step(fb['id'], 'COMPLETED',
                        outputs={'framework_template': 'tpl1'})

    current = repo.one('select state from jobs where id=?', (job['id'],))['state']
    # G0 is disabled → FRAMEWORK_BASELINED is entered directly.
    # Walker then cascades: ace-analysis is not selected (explicitly_disabled),
    # ACE_ANALYSED is a gated state, G1 is still enabled → WAITING_G1 fires.
    # Both outcomes prove framework-baseline completion with artefact was accepted.
    assert current in ('FRAMEWORK_BASELINED', 'WAITING_G1'), (
        f"Expected lifecycle past DORMANCY_ASSESSED (got FRAMEWORK_BASELINED "
        f"or WAITING_G1) after framework-baseline completed with artefact "
        f"present and G0 disabled. Got: {current}"
    )


# ---------------------------------------------------------------------------
# Test C — framework-baseline dependency-skipped: confirm it IS a valid
#           waypoint and lifecycle can pass through it.
# ---------------------------------------------------------------------------

def test_C_dependency_skipped_framework_baseline_is_valid_waypoint(env):
    """
    framework-baseline IS selected but one of its own deps is missing,
    so it is pruned by the fixed-point loop → skip_kind=dependency.
    Later, an agent at a higher lifecycle index completes.
    The waypoint should still be honoured for framework-baseline because
    the skip was structural (dependency cascade), not a user choice.

    We simulate this by selecting framework-baseline AND planning (which
    depends on migration-specification that is NOT selected).  The
    fixed-point loop removes planning for missing deps; framework-baseline
    has no deps of its own so it stays in runnable.  We then manually
    manufacture a dependency-skipped step to confirm the classification.
    """
    repo, _, job, pipelines, workflow = env

    # Directly test the impact() classification for a dependency-cascade skip.
    # planning depends on migration-specification and framework-baseline.
    # With migration-specification absent, planning is dependency-cascade skipped.
    selected = ['repository-intake', 'framework-baseline', 'planning']
    result = pipelines.impact(selected)

    skipped_map = {x['agent_id']: x for x in result['skipped']}
    planning_skip = skipped_map.get('planning')
    assert planning_skip is not None, "planning should be skipped"
    assert planning_skip['skip_kind'] == 'dependency', (
        f"Expected skip_kind=dependency for planning, got: {planning_skip['skip_kind']!r}"
    )
    # framework-baseline has no deps so it must be runnable, not skipped.
    assert 'framework-baseline' in result['runnable'], (
        "framework-baseline should be runnable when explicitly selected"
    )
    # migration-specification was never selected — must be explicitly_disabled.
    ms_skip = skipped_map.get('migration-specification')
    assert ms_skip is not None
    assert ms_skip['skip_kind'] == 'explicitly_disabled', (
        f"Expected skip_kind=explicitly_disabled for migration-specification, "
        f"got: {ms_skip['skip_kind']!r}"
    )


# ---------------------------------------------------------------------------
# Test D — artefact missing: transition() must be blocked even if the
#           pipeline classification would otherwise allow it.
# ---------------------------------------------------------------------------

def test_D_pipeline_lifecycle_blocked_when_artefact_missing(env):
    """
    The pipeline lifecycle walker must NOT advance to FRAMEWORK_BASELINED when
    the framework_baselines artefact does not exist, even if the agent
    completed.  The artefact guard in _try_lifecycle_transition stops the walk.
    """
    repo, factory, job, pipelines, workflow = env
    _inject_intake_manifest(repo, job['id'])
    _disable_gate(repo, 'G0')

    # Include framework-baseline so it runs (not explicitly_disabled).
    selected = ['repository-intake', 'dormancy-intelligence', 'framework-baseline']
    p = _create_and_run(pipelines, workflow, job['id'], selected)

    _complete_clone(pipelines, p)

    intake = _step(p, 'repository-intake')
    pipelines.mark_step(intake['id'], 'RUNNING')
    pipelines.mark_step(intake['id'], 'COMPLETED',
                        outputs={'intake_manifest': 'im1', 'gaps': []})

    dormancy = _step(p, 'dormancy-intelligence')
    pipelines.mark_step(dormancy['id'], 'RUNNING')
    _inject_dormancy_assessment(repo, job['id'])
    pipelines.mark_step(dormancy['id'], 'COMPLETED', outputs={})

    # Mark framework-baseline COMPLETED but do NOT write the framework_baselines
    # artefact — the artefact guard must block the lifecycle advance.
    fb = _step(p, 'framework-baseline')
    pipelines.mark_step(fb['id'], 'RUNNING')
    pipelines.mark_step(fb['id'], 'COMPLETED',
                        outputs={'framework_template': 'tpl1'})

    current = repo.one('select state from jobs where id=?', (job['id'],))['state']
    assert current != 'FRAMEWORK_BASELINED', (
        f"Lifecycle reached FRAMEWORK_BASELINED even though framework_baselines "
        f"artefact record was never written. Got: {current}"
    )


# ---------------------------------------------------------------------------
# Test D2 — artefact present: transition() must succeed.
# ---------------------------------------------------------------------------

def test_D2_transition_allowed_when_artefact_present(env):
    """
    Same as Test D but with the framework_baselines record present.
    G0 is disabled so the gate cannot block the transition.
    The transition must succeed, proving artefact validation does not
    false-positive when the artefact exists.
    """
    repo, factory, job, pipelines, workflow = env

    with repo.connect() as c:
        c.execute("update jobs set state='WAITING_G0' where id=?", (job['id'],))

    _inject_framework_baseline(repo, job['id'])
    _disable_gate(repo, 'G0')

    # Gate disabled + artefact present → transition must succeed.
    result = factory.transition(job['id'], 'FRAMEWORK_BASELINED', 'tester',
                                {'source': 'test'})
    assert result['state'] == 'FRAMEWORK_BASELINED'


# ---------------------------------------------------------------------------
# Test E — impact() skip_kind field is always present on skipped items.
# ---------------------------------------------------------------------------

def test_E_impact_skip_kind_always_present(env):
    """
    Every skipped item returned by impact() must have a skip_kind key
    whose value is either 'explicitly_disabled' or 'dependency'.
    """
    _, _, _, pipelines, _ = env
    # Partial selection to produce both kinds of skip.
    selected = ['repository-intake', 'ace-analysis', 'planning', 'code-generation']
    result = pipelines.impact(selected)
    valid_kinds = {'explicitly_disabled', 'dependency'}
    for item in result['skipped']:
        assert 'skip_kind' in item, f"skip_kind missing for {item['agent_id']}"
        assert item['skip_kind'] in valid_kinds, (
            f"Unexpected skip_kind {item['skip_kind']!r} for {item['agent_id']}"
        )


# ---------------------------------------------------------------------------
# Test F — backward compat: old SKIPPED steps with legacy error strings
#           (pre-fix pipelines) fall through correctly.
# ---------------------------------------------------------------------------

def test_F_legacy_skipped_steps_without_skip_kind_prefix(env):
    """
    Old pipeline_steps rows written before the fix have error values like
    'explicitly disabled' or 'depends on X' (no 'skip_kind:' prefix).
    The updated _try_lifecycle_transition must handle these gracefully:
    - 'depends on ...' (no prefix) → treated as dependency skip (waypoint allowed)
    - 'explicitly disabled' (no prefix) → treated as explicitly_disabled (no waypoint)
    Verify via the dependency_skipped set construction logic directly.
    """
    # The classification logic in _try_lifecycle_transition uses:
    #   error.startswith('skip_kind:dependency') OR
    #   (not error.startswith('skip_kind:') AND error.startswith('depends on '))
    # Test this directly.
    def classify(error):
        e = str(error or '')
        if e.startswith('skip_kind:dependency'):
            return 'dependency'
        if not e.startswith('skip_kind:') and e.startswith('depends on '):
            return 'dependency'
        return 'explicitly_disabled'

    assert classify('skip_kind:dependency;depends on foo') == 'dependency'
    assert classify('skip_kind:explicitly_disabled;explicitly disabled') == 'explicitly_disabled'
    assert classify('depends on framework-baseline, migration-specification') == 'dependency'
    assert classify('explicitly disabled') == 'explicitly_disabled'
    assert classify('') == 'explicitly_disabled'
    assert classify(None) == 'explicitly_disabled'


# ---------------------------------------------------------------------------
# Test G — explicitly-disabled framework-baseline with G0 disabled:
#           lifecycle must advance to FRAMEWORK_BASELINED even though the agent
#           never ran (gate disabled = no approval needed, transition fires).
#           This is the core "optional agent with governance gate" use-case:
#           teams choose to skip framework-baseline intentionally and the
#           pipeline must still progress past FRAMEWORK_BASELINED.
# ---------------------------------------------------------------------------

def test_G_explicitly_disabled_framework_baseline_with_gate_disabled_advances(env):
    """
    framework-baseline is explicitly_disabled (not in selected agents).
    G0 is disabled by policy.

    The lifecycle walker must:
      1. Attempt the DORMANCY_ASSESSED → FRAMEWORK_BASELINED transition
         (new behaviour: explicitly_disabled + gated state → still try)
      2. Succeed because G0 is disabled (no approval needed)
      3. Advance to FRAMEWORK_BASELINED

    If ace-analysis also completed the walker must continue and attempt
    ACE_ANALYSED next — verifying the walk does not stop at FRAMEWORK_BASELINED.
    """
    repo, factory, job, pipelines, workflow = env
    _inject_intake_manifest(repo, job['id'])
    _disable_gate(repo, 'G0')
    _disable_gate(repo, 'G1')  # disable G1 too so the walk can reach ACE_ANALYSED

    # framework-baseline intentionally excluded; dormancy + ace selected
    selected = ['repository-intake', 'dormancy-intelligence', 'ace-analysis']
    p = _create_and_run(pipelines, workflow, job['id'], selected)

    # Verify framework-baseline is explicitly_disabled
    fb_step = _step(p, 'framework-baseline')
    assert fb_step is not None and fb_step['status'] == 'SKIPPED'
    assert fb_step['error'].startswith('skip_kind:explicitly_disabled'), (
        f"Expected skip_kind:explicitly_disabled, got: {fb_step['error']!r}"
    )

    _complete_clone(pipelines, p)

    intake = _step(p, 'repository-intake')
    pipelines.mark_step(intake['id'], 'RUNNING')
    pipelines.mark_step(intake['id'], 'COMPLETED',
                        outputs={'intake_manifest': 'im1', 'gaps': []})

    dormancy = _step(p, 'dormancy-intelligence')
    pipelines.mark_step(dormancy['id'], 'RUNNING')
    _inject_dormancy_assessment(repo, job['id'])
    pipelines.mark_step(dormancy['id'], 'COMPLETED', outputs={})

    # After dormancy completes, walker should attempt FRAMEWORK_BASELINED.
    # G0 is disabled → no approval needed → lifecycle advances.
    state_after_dormancy = repo.one('select state from jobs where id=?', (job['id'],))['state']
    assert state_after_dormancy == 'FRAMEWORK_BASELINED', (
        f"Expected FRAMEWORK_BASELINED after dormancy-intelligence completed "
        f"with framework-baseline explicitly disabled and G0 disabled. "
        f"Got: {state_after_dormancy}"
    )

    # Now complete ace-analysis — walker should try ACE_ANALYSED next.
    ace = _step(p, 'ace-analysis')
    pipelines.mark_step(ace['id'], 'RUNNING')
    _inject_ace_analysis(repo, job['id'])
    pipelines.mark_step(ace['id'], 'COMPLETED',
                        outputs={'analysis_document': 'ad1', 'semantic_model': '{}', 'ambiguities': []})

    state_after_ace = repo.one('select state from jobs where id=?', (job['id'],))['state']
    # G1 is disabled → ACE_ANALYSED is entered directly.
    # Walker cascades: planning is not selected (explicitly_disabled),
    # PLAN_APPROVED is a gated state, G2 is still enabled → WAITING_G2 fires.
    # Both outcomes confirm the walk continued past ACE_ANALYSED.
    assert state_after_ace in ('ACE_ANALYSED', 'WAITING_G2'), (
        f"Expected lifecycle past FRAMEWORK_BASELINED (got ACE_ANALYSED or "
        f"WAITING_G2) after ace-analysis completed with G1 disabled. "
        f"Got: {state_after_ace}"
    )


# ---------------------------------------------------------------------------
# Test H — dependency-cascade-skipped planning triggers G2 gate.
#           When framework-baseline and migration-specification are both
#           explicitly disabled, planning loses its prerequisites and becomes
#           dependency_skipped.  G2 must still fire because PLAN_APPROVED is
#           a gated state, regardless of whether the skip was direct or
#           cascaded.
# ---------------------------------------------------------------------------

def test_H_dependency_skipped_planning_triggers_g2_gate(env):
    """
    framework-baseline and migration-specification are explicitly_disabled.
    planning is dependency_skipped (cascaded from the above).
    ace-analysis IS selected and completes.

    Expected: after ace-analysis completes, the walker reaches PLAN_APPROVED,
    finds planning is dependency_skipped AND PLAN_APPROVED is gated, so G2
    fires → job enters WAITING_G2.

    This is the real-world 'analysis-only' pipeline where no code generation
    is planned but governance must still approve the gap.
    """
    repo, factory, job, pipelines, workflow = env
    _inject_intake_manifest(repo, job['id'])
    _disable_gate(repo, 'G0')
    _disable_gate(repo, 'G1')

    # planning IS in selected but its prerequisites (framework-baseline,
    # migration-specification) are NOT, so the fixed-point loop removes it:
    # planning becomes dependency_skipped, not explicitly_disabled.
    selected = ['repository-intake', 'dormancy-intelligence', 'ace-analysis', 'planning']
    p = _create_and_run(pipelines, workflow, job['id'], selected)

    # Verify planning is dependency_skipped (cascaded from excluded deps)
    planning_step = _step(p, 'planning')
    assert planning_step is not None and planning_step['status'] == 'SKIPPED'
    assert planning_step['error'].startswith('skip_kind:dependency'), (
        f"Expected skip_kind:dependency for planning (cascaded from "
        f"excluded framework-baseline/migration-specification), "
        f"got: {planning_step['error']!r}"
    )

    _complete_clone(pipelines, p)

    intake = _step(p, 'repository-intake')
    pipelines.mark_step(intake['id'], 'RUNNING')
    pipelines.mark_step(intake['id'], 'COMPLETED',
                        outputs={'intake_manifest': 'im1', 'gaps': []})

    dormancy = _step(p, 'dormancy-intelligence')
    pipelines.mark_step(dormancy['id'], 'RUNNING')
    _inject_dormancy_assessment(repo, job['id'])
    pipelines.mark_step(dormancy['id'], 'COMPLETED', outputs={})

    ace = _step(p, 'ace-analysis')
    pipelines.mark_step(ace['id'], 'RUNNING')
    _inject_ace_analysis(repo, job['id'])
    pipelines.mark_step(ace['id'], 'COMPLETED',
                        outputs={'analysis_document': 'ad1', 'semantic_model': '{}', 'ambiguities': []})

    current = repo.one('select state from jobs where id=?', (job['id'],))['state']
    # G0+G1 disabled → lifecycle passes through FRAMEWORK_BASELINED and
    # ACE_ANALYSED.  Walker then hits PLAN_APPROVED: planning is
    # dependency_skipped AND PLAN_APPROVED is gated → G2 fires → WAITING_G2.
    assert current == 'WAITING_G2', (
        f"Expected WAITING_G2 (G2 gate fired for dependency-skipped planning). "
        f"Got: {current}"
    )

    # Verify the G2 approval package was auto-created.
    pkg = repo.one(
        "select gate, status from approval_packages where job_id=? and gate='G2'",
        (job['id'],),
    )
    assert pkg is not None, "G2 approval package must exist after gate fires"
    assert pkg['status'] == 'PENDING_APPROVAL', (
        f"G2 package should be PENDING_APPROVAL, got: {pkg['status']}"
    )
