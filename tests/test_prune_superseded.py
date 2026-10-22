"""A regeneration must not leave the previous run's files next to the new ones.

The membereobpreferences project failed COMPILE_AND_UNIT_TEST because a run that
changed the package from ``com.esl...`` to ``com.cvshealth.eip.microservices``
wrote the new tree beside the old one; Maven compiled both and the old tree
referenced classes that no longer existed.
"""
import json

from app.services.springgen import previous_manifest_paths, prune_superseded


def _write(root, rel, text='x'):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def test_files_the_last_run_wrote_and_this_one_does_not_are_removed(tmp_path):
    old = _write(tmp_path, 'web/src/main/java/com/esl/app/Controller.java')
    kept = _write(tmp_path, 'web/src/main/java/com/cvs/app/Controller.java')
    (tmp_path / 'generated-manifest.json').write_text(json.dumps(
        [{'path': 'web/src/main/java/com/esl/app/Controller.java'},
         {'path': 'web/src/main/java/com/cvs/app/Controller.java'}]))
    previous = previous_manifest_paths(tmp_path)

    removed = prune_superseded(tmp_path, previous, {'web/src/main/java/com/cvs/app/Controller.java'})

    assert removed == ['web/src/main/java/com/esl/app/Controller.java']
    assert not old.exists() and kept.exists()
    # The directories the removal emptied go with it, so no empty package is left.
    assert not (tmp_path / 'web/src/main/java/com/esl').exists()
    assert (tmp_path / 'web/src/main/java/com/cvs').exists()


def test_a_file_the_factory_never_wrote_is_left_alone(tmp_path):
    handmade = _write(tmp_path, 'web/src/main/java/com/esl/app/HandEdit.java')

    removed = prune_superseded(tmp_path, set(), {'web/pom.xml'})

    assert removed == [] and handmade.exists()


def test_a_manifest_path_cannot_reach_outside_the_project(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    outside = _write(tmp_path, 'outside.txt')

    removed = prune_superseded(project, {'../outside.txt'}, set())

    assert removed == [] and outside.exists()


def test_no_manifest_means_nothing_to_prune(tmp_path):
    assert previous_manifest_paths(tmp_path) == set()
    (tmp_path / 'generated-manifest.json').write_text('not json')
    assert previous_manifest_paths(tmp_path) == set()
