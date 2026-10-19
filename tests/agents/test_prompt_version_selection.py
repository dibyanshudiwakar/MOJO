"""The newest approved prompt version is the one used.

``ReasoningGateway._approved`` ordered by ``version`` as text. A legacy seed row
versioned ``'v1'`` therefore sorted above ``'2'``, ``'3'`` and ``'4'``, and for 32
prompts every later version was registered, approved and never used: planning
ran on its original prompt through four revisions of it, and every "make the
answer shorter" fix to it did nothing. The log said so -- ``prompt resolved ...
vv1 (hash: 9a4155600fcb)`` -- but nobody was reading the hash.
"""
import pytest

from app.agents.reasoning import ReasoningGateway, version_key
from app.repository import digest, now, uid

NAME = 'agent/test/versions'


def _register(repo, *versions):
    with repo.connect() as c:
        for i, v in enumerate(versions):
            template = 'template ' + v
            repo.insert(c, 'prompt_registry', {
                'id': uid(), 'name': NAME, 'version': v, 'template': template,
                'content_hash': digest(template), 'status': 'APPROVED',
                'approved_by': 'seed', 'effective_from': now(), 'created_at': now()})


def _chosen(repo):
    return ReasoningGateway(repo)._approved(NAME)[0]['version']


def test_a_legacy_v_prefixed_row_does_not_outrank_a_later_version(repo):
    _register(repo, '1', 'v1', '2', '3', '4')
    assert _chosen(repo) == '4'


def test_ten_outranks_nine(repo):
    _register(repo, '9', '10', '2')
    assert _chosen(repo) == '10'


def test_a_v_prefix_does_not_matter_when_it_is_the_newest(repo):
    _register(repo, '1', 'v2')
    assert _chosen(repo) == 'v2'


def test_an_unapproved_version_is_never_chosen(repo):
    _register(repo, '1', '2')
    with repo.connect() as c:
        c.execute("update prompt_registry set status='DRAFT' where name=? and version='2'", (NAME,))
    assert _chosen(repo) == '1'


def test_no_row_at_all_is_none_not_an_error(repo):
    assert ReasoningGateway(repo)._approved('agent/test/absent')[0] is None


@pytest.mark.parametrize('a,b', [('v1', '1'), ('V1', '1'), ('1.0', '1.0')])
def test_equal_versions_tie(a, b):
    assert version_key(a) == version_key(b)


@pytest.mark.parametrize('lower,higher', [('1', '2'), ('9', '10'), ('v1', '4'), ('1.9', '1.10'), ('', '1')])
def test_ordering(lower, higher):
    assert version_key(lower) < version_key(higher)
