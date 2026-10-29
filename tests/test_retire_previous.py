"""A regenerated project drops the files the last generation recorded and this one does not contain."""
import json

from app.services.migration_lifecycle_v6 import retire_previous


def _write(base, rel, text='x'):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _record(base, rels):
    (base / 'generated-manifest.json').write_text(json.dumps([{'path': r} for r in rels]))


def test_files_of_an_earlier_package_are_removed_and_their_folders_with_them(tmp_path):
    _write(tmp_path, 'web/src/main/java/com/tcs/A.java')
    _write(tmp_path, 'web/src/main/java/com/cvs/B.java')
    _record(tmp_path, ['web/src/main/java/com/tcs/A.java'])
    kept = {'web/src/main/java/com/cvs/B.java'}
    _write(tmp_path, 'web/src/main/java/com/cvs/B.java')
    removed = retire_previous(tmp_path, kept)
    assert removed == ['web/src/main/java/com/tcs/A.java']
    assert not (tmp_path / 'web/src/main/java/com/tcs').exists()
    assert (tmp_path / 'web/src/main/java/com/cvs/B.java').is_file()


def test_a_file_the_platform_did_not_record_is_never_touched(tmp_path):
    _write(tmp_path, 'notes/handwritten.txt')
    _record(tmp_path, [])
    assert retire_previous(tmp_path, set()) == []
    assert (tmp_path / 'notes/handwritten.txt').is_file()


def test_a_path_outside_the_project_in_the_old_record_is_refused(tmp_path):
    outside = tmp_path / 'outside.txt'
    outside.write_text('keep me')
    base = tmp_path / 'project'
    base.mkdir()
    _record(base, ['../outside.txt'])
    assert retire_previous(base, set()) == []
    assert outside.is_file()


def test_no_earlier_manifest_means_nothing_to_remove(tmp_path):
    assert retire_previous(tmp_path, {'a.java'}) == []
