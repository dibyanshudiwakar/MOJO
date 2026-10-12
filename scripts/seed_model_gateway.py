"""Seed the three DB record groups required for real model execution.

Run once (idempotent) before verify_dormancy_execution.py:

    python scripts/seed_model_gateway.py
    python scripts/seed_model_gateway.py --api-base http://localhost:11434/v1 --api-key ollama --model qwen2.5:3b

Flags:
    --api-base   Base URL of the OpenAI-compatible endpoint  (default: http://localhost:11434/v1)
    --api-key    API key / token                             (default: ollama)
    --model      Model name served at that endpoint          (default: qwen2.5:3b)
    --db         Path to SQLite database                     (default: factory-v2.db)
    --dry-run    Print SQL without writing

Records inserted
----------------
1. connector_profiles  -- one row: category=model-gateway, name=model-primary
2. model_registry      -- five rows: one per alias (reasoning-small/large, coding,
                          embeddings, semantic-review), all APPROVED
                          Required because AT-13 in agents.py activates for ALL
                          agents once any model_registry row exists.
3. prompt_registry     -- one row: the dormancy classification-rationale prompt,
                          APPROVED, name matching DormancyIntelligenceAgent.PROMPT
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


PROMPT_TEMPLATE = """\
You are a dormancy classification assistant for an IBM ACE to Spring Boot migration factory.

Given deployment and telemetry signals about a service, classify its dormancy status and
explain your reasoning.  Return ONLY a valid JSON object with these fields:

{
  "classification": "<ACTIVE|DORMANT|UNKNOWN>",
  "confidence": <0.0-1.0>,
  "rationale": "<one sentence>",
  "missing_evidence": ["<signal name>", ...]
}

Rules:
- DORMANT: traffic_count=0 or last_seen_days > 180 and deployed=false
- ACTIVE:  traffic_count > 0 or deployed=true and last_seen_days < 90
- UNKNOWN: insufficient evidence to decide
- already_migrated=true always returns DORMANT with confidence 1.0
- List any signal that was null/absent in missing_evidence
"""

MODEL_ALIASES = [
    'reasoning-small',
    'reasoning-large',
    'coding',
    'embeddings',
    'semantic-review',
]

DORMANCY_PROMPT_NAME = 'agent/dormancy-intelligence/classification-rationale'


def seed(conn, api_base, api_key, model, dry_run=False):
    def run(sql, params=()):
        if dry_run:
            print('SQL:', sql, params)
        else:
            conn.execute(sql, params)

    conn.row_factory = lambda c, r: dict(zip([d[0] for d in c.description], r))

    # ------------------------------------------------------------------
    # 1. connector_profiles
    # ------------------------------------------------------------------
    existing_profile = conn.execute(
        "SELECT id FROM connector_profiles WHERE category='model-gateway' AND name='model-primary'"
    ).fetchone()

    if existing_profile:
        print('[connector_profiles] model-primary already exists, skipping')
        profile_name = 'model-primary'
    else:
        config = json.dumps({
            'api_base': api_base,
            'api_key':  api_key,
            'model':    model,
        })
        run(
            "INSERT INTO connector_profiles(id,category,name,provider,config,secret_reference,"
            "priority,enabled,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (uid(), 'model-gateway', 'model-primary', 'openai-compatible',
             config, '', 100, 1, 'VERIFIED', now(), now()),
        )
        profile_name = 'model-primary'
        print('[connector_profiles] inserted model-primary ->', api_base)

    # ------------------------------------------------------------------
    # 2. model_registry  (all 5 aliases, APPROVED)
    # ------------------------------------------------------------------
    for alias in MODEL_ALIASES:
        existing_model = conn.execute(
            "SELECT id FROM model_registry WHERE name=? AND version='v1'", (alias,)
        ).fetchone()
        if existing_model:
            print('[model_registry]      %s v1 already exists, skipping' % alias)
            continue
        run(
            "INSERT INTO model_registry(id,name,version,provider_profile,policy,region,"
            "status,approved_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (uid(), alias, 'v1', profile_name, '{}', '', 'APPROVED', 'seed', now()),
        )
        print('[model_registry]      inserted %s v1 APPROVED' % alias)

    # ------------------------------------------------------------------
    # 3. prompt_registry  (dormancy classification-rationale, APPROVED)
    # ------------------------------------------------------------------
    import hashlib
    prompt_hash = hashlib.sha256(PROMPT_TEMPLATE.encode()).hexdigest()
    existing_prompt = conn.execute(
        "SELECT id FROM prompt_registry WHERE name=? AND version='v1'",
        (DORMANCY_PROMPT_NAME,)
    ).fetchone()
    if existing_prompt:
        print('[prompt_registry]     %s v1 already exists, skipping' % DORMANCY_PROMPT_NAME)
    else:
        run(
            "INSERT INTO prompt_registry(id,name,version,template,content_hash,status,"
            "approved_by,effective_from,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (uid(), DORMANCY_PROMPT_NAME, 'v1', PROMPT_TEMPLATE, prompt_hash,
             'APPROVED', 'seed', now(), now()),
        )
        print('[prompt_registry]     inserted %s v1 APPROVED' % DORMANCY_PROMPT_NAME)

    if not dry_run:
        conn.commit()
        print('\nDone. All required records are in place.')
    else:
        print('\nDry run complete. No changes written.')


def main():
    parser = argparse.ArgumentParser(description='Seed model gateway records for LLM execution proof')
    parser.add_argument('--api-base', default='http://localhost:11434/v1',
                        help='Base URL of OpenAI-compatible endpoint')
    parser.add_argument('--api-key',  default='ollama',
                        help='API key or token')
    parser.add_argument('--model',    default='qwen2.5:3b',
                        help='Model name served at the endpoint')
    parser.add_argument('--db',       default='factory-v2.db',
                        help='Path to SQLite database')
    parser.add_argument('--dry-run',  action='store_true',
                        help='Print SQL without writing')
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print('ERROR: database not found at', args.db)
        print('Start the server once (python -m app.server) to create it, then re-run.')
        sys.exit(1)

    import sqlite3
    conn = sqlite3.connect(args.db)
    try:
        seed(conn, args.api_base, args.api_key, args.model, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
