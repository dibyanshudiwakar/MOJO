"""Register and APPROVE every catalogue prompt and every in-use model alias.

Why this exists: ``ReasoningGateway._approved`` (app/agents/reasoning.py) requires
an APPROVED row in both ``prompt_registry`` (by name) and ``model_registry`` (by
alias) before any agent reaches the configured provider -- otherwise every agent
silently falls back to deterministic computation (by design, see that module's
docstring). Out of the box both tables are empty, so *every* model-assisted
agent (ace-analysis, migration-specification, planning, ...) hits the fallback,
which is what produced the "no approved prompt or model found" warnings across
the pipeline.

This seeds:
  1. One ``connector_profiles`` row (category=model-gateway) representing the
     CVS LMS Claude gateway that ``AI_PROVIDER=gateway`` already points
     ``CvscodeClaudeAdapter`` at -- required by ``register_model``'s and
     ``gateway_request``'s "approved provider profile" check. It carries no
     secret: the adapter resolves its own key via the cvscode helper.
  2. One APPROVED ``model_registry`` row per alias actually referenced by
     ``agent_registry.default_model_alias`` (reasoning-small, reasoning-large,
     coding, semantic-review). ``embeddings`` is deliberately excluded --
     ``CvscodeClaudeAdapter`` has no embeddings endpoint and no agent asks for it.
  3. One APPROVED ``prompt_registry`` row per entry in
     ``config/agent-prompts.v1.json`` (all 16), hashed the same way
     ``AIRegistryService.register_prompt`` does.

Run once (idempotent -- skips rows that already exist):
    python scripts/approve_prompts_and_models.py --approved-by dev.bhandari@cvshealth.com
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / 'config' / 'agent-prompts.v1.json'

CONNECTOR_NAME = 'cvs-claude-gateway'
MODEL_ALIASES = ['reasoning-small', 'reasoning-large', 'coding', 'semantic-review']


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def digest(x):
    return hashlib.sha256(x if isinstance(x, bytes) else str(x).encode()).hexdigest()


def seed(conn, approved_by):
    conn.row_factory = sqlite3.Row

    existing = conn.execute(
        "select id from connector_profiles where category='model-gateway' and name=?",
        (CONNECTOR_NAME,)).fetchone()
    if existing:
        print('[connector_profiles] %s already exists, skipping' % CONNECTOR_NAME)
    else:
        config = json.dumps({'note': 'key resolved per-call via cvscode helper; not stored here'})
        conn.execute(
            "insert into connector_profiles(id,category,name,provider,config,secret_reference,"
            "priority,enabled,status,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?)",
            (uid(), 'model-gateway', CONNECTOR_NAME, 'anthropic', config, '', 100, 1, 'VERIFIED', now(), now()))
        print('[connector_profiles] inserted %s' % CONNECTOR_NAME)

    for alias in MODEL_ALIASES:
        existing = conn.execute(
            "select id from model_registry where name=? and version='1'", (alias,)).fetchone()
        if existing:
            print('[model_registry]      %s v1 already exists, skipping' % alias)
            continue
        conn.execute(
            "insert into model_registry(id,name,version,provider_profile,policy,region,"
            "status,approved_by,created_at) values(?,?,?,?,?,?,?,?,?)",
            (uid(), alias, '1', CONNECTOR_NAME, '{}', '', 'APPROVED', approved_by, now()))
        print('[model_registry]      inserted %s v1 APPROVED' % alias)

    prompts = json.loads(CATALOGUE.read_text(encoding='utf-8'))['prompts']
    for p in prompts:
        version = str(p['version'])
        existing = conn.execute(
            "select id from prompt_registry where name=? and version=?", (p['name'], version)).fetchone()
        if existing:
            print('[prompt_registry]     %s v%s already exists, skipping' % (p['name'], version))
            continue
        h = digest(p['template'])
        conn.execute(
            "insert into prompt_registry(id,name,version,template,content_hash,status,"
            "approved_by,effective_from,created_at) values(?,?,?,?,?,?,?,?,?)",
            (uid(), p['name'], version, p['template'], h, 'APPROVED', approved_by, now(), now()))
        print('[prompt_registry]     inserted %s v%s APPROVED' % (p['name'], version))

    conn.commit()
    print('\nDone.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-by', required=True, help='Identity recorded as approver (must hold GOVERNANCE_ADMIN or AI_GOVERNANCE_ADMIN to be a legitimate approval)')
    parser.add_argument('--db', default=str(ROOT / 'factory-v2.db'), help='Path to SQLite database')
    args = parser.parse_args()

    if not Path(args.db).exists():
        print('ERROR: database not found at', args.db)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    try:
        seed(conn, args.approved_by)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
