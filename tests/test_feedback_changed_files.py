"""The feedback output lists each changed file with its changes and current content, as code generation does."""
from app import server


def test_changed_files_groups_fixes_by_file_and_reads_the_content(tmp_path):
    a = tmp_path / 'A.java'
    a.write_text('class A {}\n', encoding='utf-8')
    b = tmp_path / 'application.properties'
    b.write_text('k=v\n', encoding='utf-8')
    fixes = [{'path': str(a), 'check': 'SECRET_SCAN', 'action': 'replaced a credential'},
             {'path': str(b), 'check': 'SKILL_A', 'action': 'copied 2 packaged key(s)'},
             {'path': str(a), 'check': 'MODEL_REPAIR', 'action': 'fixed an import'}]
    files = server._changed_files(fixes)
    by_path = {f['path']: f for f in files}
    assert len(files) == 2
    assert [c['check'] for c in by_path[str(a)]['changes']] == ['SECRET_SCAN', 'MODEL_REPAIR']
    assert by_path[str(a)]['content'] == 'class A {}\n'
    assert by_path[str(b)]['changes'][0]['action'] == 'copied 2 packaged key(s)'


def test_a_file_that_cannot_be_read_keeps_its_changes_with_empty_content(tmp_path):
    files = server._changed_files([{'path': str(tmp_path / 'gone.xml'), 'check': 'PROVENANCE', 'action': 'rebuilt'}])
    assert files[0]['content'] == '' and files[0]['changes'][0]['check'] == 'PROVENANCE'


def test_no_fixes_means_no_files():
    assert server._changed_files([]) == [] and server._changed_files(None) == []
