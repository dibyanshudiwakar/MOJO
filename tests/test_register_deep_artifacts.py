"""Generated files deep in the package tree must reach ``generated_artifacts``.

On Windows a plain read of a path past MAX_PATH (260) raises FileNotFoundError.
Registration swallowed that and moved on, so every file under
``src/main/java/<package>/...`` went unregistered while the shallow POMs and
config were kept, and variance analysis then skipped the hardcoding check with
``no_ms_java_files`` on a project that was full of Java.
"""
import contextlib
import hashlib
import os
import sqlite3
from pathlib import Path

from app.services.migration_lifecycle_v6 import MappingAndPlanningService


class _Repo:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('create table generated_artifacts(id,job_id,plan_id,path,content,provenance,'
                        'region_policy,content_hash,created_at)')

    @contextlib.contextmanager
    def connect(self):
        yield self.db
        self.db.commit()


def _extended(path):
    text = str(path)
    return Path('\\\\?\\' + text) if os.name == 'nt' else path


def test_a_file_past_max_path_is_registered(tmp_path):
    deep = Path('EIPMS_service/service-web/src/main/java/com/cvshealth/eip/microservices'
                '/swagger/model/response/a/very/long/package/segment/to/cross/the/limit/'
                'and_then_some_more/directories_to_be_sure/MemberResponse.java')
    shallow = Path('EIPMS_service/service-web/pom.xml')
    root = tmp_path.joinpath(*(['segment_padding_directory'] * 8))
    assert len(str(root / deep)) > 300
    for rel, text in ((deep, 'class MemberResponse {}'), (shallow, '<project/>')):
        target = _extended(root / rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    digest = hashlib.sha256(b'x').hexdigest()
    manifest = [{'path': deep.as_posix(), 'hash': digest}, {'path': shallow.as_posix(), 'hash': digest}]

    repo = _Repo()
    registered = MappingAndPlanningService(repo)._register_artifacts(
        'job', 'plan', {'root': str(root), 'manifest': manifest})

    stored = {row[0]: row[1] for row in repo.db.execute('select path,content from generated_artifacts')}
    assert registered == 2
    assert stored[deep.as_posix()] == 'class MemberResponse {}'
    assert stored[shallow.as_posix()] == '<project/>'
