"""A fresh database comes up with the model path on, and can be told not to.

``tests/conftest.py`` sets ``FACTORY_SEED_MODEL_GATEWAY=0`` for the whole suite,
because a pristine database is what lets other tests exercise the
``no_approved_prompt_or_model`` path and register their own prompts without
colliding with a seeded row. That opt-out means nothing else covers the
bootstrap, so it is covered here by building repositories with the flag set
explicitly.

Why the bootstrap exists: with these three groups empty,
``ReasoningGateway.reason`` returns ``no_approved_prompt_or_model`` on every
call and all 15 agents run their deterministic fallback -- which looks like a
healthy pipeline, because a recorded fallback is a successful run. A new
instance therefore had the model switched off and nothing said so.
"""
import json
from pathlib import Path

import pytest

from app.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE_PROMPTS = json.loads(
    (ROOT / 'config' / 'agent-prompts.v1.json').read_text(encoding='utf-8'))['prompts']
#: Every alias config/agent-catalogue.v1.json assigns an agent, plus embeddings.
AGENT_ALIASES = {a.get('default_model_alias') for a in json.loads(
    (ROOT / 'config' / 'agent-catalogue.v1.json').read_text(encoding='utf-8'))['agents']}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv('FACTORY_SEED_MODEL_GATEWAY', '1')
    return Repository(str(tmp_path / 'seeded.db'))


def test_every_catalogue_prompt_is_registered_and_approved(seeded):
    rows = {r['name']: r['status'] for r in seeded.rows('select name,status from prompt_registry')}
    missing = sorted({p['name'] for p in CATALOGUE_PROMPTS} - set(rows))
    assert missing == [], 'an unregistered prompt can never reach the model'
    assert set(rows.values()) == {'APPROVED'}


def test_every_alias_an_agent_resolves_is_registered(seeded):
    """AT-13 arms for *every* agent once model_registry holds any row.

    So seeding one alias and not the rest would convert a silent fallback into
    ``default_model_alias_not_in_approved_model_registry`` on every other agent
    -- a hard refusal where there had been a working deterministic path.
    """
    registered = {r['name'] for r in seeded.rows("select name from model_registry where status='APPROVED'")}
    assert {a for a in AGENT_ALIASES if a} <= registered


def test_a_model_gateway_profile_exists(seeded):
    """``gateway_request`` refuses without an approved model-gateway connector."""
    row = seeded.one("select name,status from connector_profiles where category='model-gateway'")
    assert row and row['status'] == 'VERIFIED'


def test_the_bootstrap_does_not_claim_a_human_approved_anything(seeded):
    """The honesty requirement, not a cosmetic one.

    The design has an AI_GOVERNANCE_ADMIN approve prompts through the governed
    endpoint. Seeding them APPROVED is self-approval by the system, so the audit
    trail has to say which rows a person signed and which the bootstrap wrote --
    otherwise an auditor cannot tell the difference.
    """
    approvers = {r['approved_by'] for r in seeded.rows('select approved_by from prompt_registry')}
    assert approvers == {'SYSTEM_BOOTSTRAP'}
    assert {r['approved_by'] for r in seeded.rows('select approved_by from model_registry')} \
        == {'SYSTEM_BOOTSTRAP'}


def test_the_flag_turns_it_off(tmp_path, monkeypatch):
    """A deployment that wants the approval step back as a real control."""
    monkeypatch.setenv('FACTORY_SEED_MODEL_GATEWAY', '0')
    repo = Repository(str(tmp_path / 'off.db'))
    assert repo.one('select count(*) n from prompt_registry')['n'] == 0
    assert repo.one('select count(*) n from model_registry')['n'] == 0
    assert repo.one("select count(*) n from connector_profiles where category='model-gateway'")['n'] == 0


@pytest.mark.parametrize('value', ['0', 'false', 'FALSE', 'no', 'No'])
def test_the_flag_accepts_the_obvious_falsey_spellings(tmp_path, monkeypatch, value):
    monkeypatch.setenv('FACTORY_SEED_MODEL_GATEWAY', value)
    repo = Repository(str(tmp_path / ('off-' + value + '.db')))
    assert repo.one('select count(*) n from prompt_registry')['n'] == 0


def test_seeding_twice_changes_nothing(tmp_path, monkeypatch):
    """init() runs on every Repository construction, including reconnects."""
    monkeypatch.setenv('FACTORY_SEED_MODEL_GATEWAY', '1')
    path = str(tmp_path / 'twice.db')
    first = Repository(path).one('select count(*) n from prompt_registry')['n']
    assert Repository(path).one('select count(*) n from prompt_registry')['n'] == first
    assert Repository(path).one('select count(*) n from model_registry')['n'] == len(
        {r['name'] for r in Repository(path).rows('select name from model_registry')})


def test_an_administrators_own_decision_is_never_overwritten(tmp_path, monkeypatch):
    """A prompt a human has since rejected must stay rejected.

    The bootstrap is a floor, not an authority: it fills in what is absent and
    leaves alone whatever anyone has since decided.
    """
    monkeypatch.setenv('FACTORY_SEED_MODEL_GATEWAY', '1')
    path = str(tmp_path / 'decided.db')
    repo = Repository(path)
    name = CATALOGUE_PROMPTS[0]['name']
    with repo.connect() as c:
        c.execute("update prompt_registry set status='REJECTED',approved_by='alice@example'"
                  ' where name=?', (name,))
    Repository(path)          # re-init must not revive it
    row = repo.one('select status,approved_by from prompt_registry where name=?', (name,))
    assert row['status'] == 'REJECTED'
    assert row['approved_by'] == 'alice@example'


def test_the_seeded_prompt_text_matches_the_catalogue(seeded):
    """A drifted template would be approved under a hash nobody reviewed."""
    from app.repository import digest
    # Keyed by (name, version): migration-specification/authoring exists at two
    # versions, v1 kept registered so switching to v2 is an explicit approval
    # rather than a silent change, so name alone is not unique.
    by_key = {(p['name'], str(p['version'])): p['template'] for p in CATALOGUE_PROMPTS}
    rows = seeded.rows('select name,version,template,content_hash from prompt_registry')
    assert len(rows) == len(by_key)
    for row in rows:
        assert row['template'] == by_key[(row['name'], str(row['version']))]
        assert row['content_hash'] == digest(row['template'])
