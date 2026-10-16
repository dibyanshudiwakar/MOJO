import json
import pytest
from pathlib import Path
from app.agents.code_generation import CodeGenerationAgent


def _params(planned, tmp_path):
    return {'plan_id': planned['plan_id'], 'output_root': str(tmp_path / 'out'),
            'service_name': 'orders', 'package_name': 'com.example.orders'}


def test_generation_writes_a_project_with_provenance_and_a_quality_run(repo, job, planned, tmp_path):
    result = CodeGenerationAgent(repo).run(job['id'], params=_params(planned, tmp_path))
    assert result.status == 'COMPLETED'
    root = Path(result.outputs['source_code'])
    assert (root / 'pom.xml').exists()
    manifest = json.loads(Path(result.outputs['manifest']).read_text())
    assert manifest['plan_id'] == planned['plan_id']
    # Every generated file is traceable to the plan that authorised it.
    assert manifest['analysis_hash'] and manifest['framework_hash'] and manifest['specification_hash']
    assert result.outputs['tests'], 'a generated flow should come with a generated test'
    quality = repo.one('select * from quality_runs where job_id=?', (job['id'],))
    assert quality['status'] == 'PASS_WITH_PENDING_TOOLS'


def test_the_evidence_hash_matches_the_generated_manifest_hash(repo, job, planned, tmp_path):
    """generate_spring hashes the manifest canonically but writes it indented."""
    result = CodeGenerationAgent(repo).run(job['id'], params=_params(planned, tmp_path))
    ref, = result.evidence
    assert ref.content_hash == result.outputs['provenance']


def test_the_generated_files_are_registered_not_only_written(repo, job, planned, tmp_path):
    """On disk is not enough: two consumers read ``generated_artifacts``.

    The agent output panel resolves a code-generation run through that table, and
    ``variance-analysis`` diffs the registered content against the IIB source.
    Nothing wrote it on this path, so the panel showed an empty file list and
    every structural variance check skipped with ``no_generated_artifacts`` --
    while the project sat complete in the workspace.
    """
    result = CodeGenerationAgent(repo).run(job['id'], params=_params(planned, tmp_path))
    rows = repo.rows('select path,content,content_hash,plan_id from generated_artifacts'
                     ' where job_id=? order by path', (job['id'],))
    paths = {r['path'] for r in rows}
    assert 'pom.xml' in paths
    assert any(p.endswith('Application.java') for p in paths)
    assert any(p.startswith('src/test/') for p in paths)
    assert all(r['plan_id'] == planned['plan_id'] and r['content'] for r in rows)
    # The registered content is the file that was written, by hash.
    manifest = json.loads(Path(result.outputs['manifest']).read_text())
    by_path = {f['path']: f['hash'] for f in manifest['files']}
    assert all(r['content_hash'] == by_path[r['path']] for r in rows)


def test_regenerating_the_same_plan_replaces_its_artefacts(repo, job, planned, tmp_path):
    """A re-run must not leave the previous run's files alongside the new ones."""
    CodeGenerationAgent(repo).run(job['id'], params=_params(planned, tmp_path))
    first = repo.one('select count(*) n from generated_artifacts where job_id=?', (job['id'],))['n']
    CodeGenerationAgent(repo).run(job['id'], params=_params(planned, tmp_path))
    assert repo.one('select count(*) n from generated_artifacts where job_id=?',
                    (job['id'],))['n'] == first


def test_generation_refuses_without_the_g3_plan_approval(repo, job, approved_inputs, dispatch, tmp_path):
    plan = dispatch(job['id'], 'planning', approved_inputs)
    with pytest.raises(PermissionError, match='g3_plan_approval_required'):
        CodeGenerationAgent(repo).run(job['id'], params={
            'plan_id': plan['outputs']['plan'], 'output_root': str(tmp_path / 'out'),
            'service_name': 'orders', 'package_name': 'com.example.orders'})


def test_generation_requires_its_parameters(repo, job, planned, tmp_path):
    for missing in ('plan_id', 'output_root', 'service_name', 'package_name'):
        params = _params(planned, tmp_path)
        del params[missing]
        with pytest.raises(ValueError, match='missing_param:' + missing):
            CodeGenerationAgent(repo).run(job['id'], params=params)
