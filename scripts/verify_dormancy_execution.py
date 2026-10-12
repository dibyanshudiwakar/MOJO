"""End-to-end proof of real system-default model execution.

Proves the path:
    dormancy-intelligence -> reasoning-small -> HttpProviderAdapter -> real HTTP call

Usage:
    # With Ollama
    python scripts/verify_dormancy_execution.py

    # With any OpenAI-compatible endpoint
    python scripts/verify_dormancy_execution.py \
        --api-base https://your-endpoint/v1 \
        --api-key  your-key \
        --model    your-model-name

    # Dry run: shows what would happen without a live endpoint
    python scripts/verify_dormancy_execution.py --no-provider

Pass/fail output:
    PASS  model_provenance.mode = MODEL_GATEWAY   (real HTTP call was made)
    FAIL  model_provenance.mode = DETERMINISTIC_FALLBACK + reason explaining which gate failed
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.repository import Repository
from app.services.agents import AgentRegistry
from app.providers.http_adapter import HttpProviderAdapter


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def create_job(repo, service_name='demo-ace-service'):
    """Insert a minimal job row in state INTAKE_VALIDATED."""
    job_id = uid()
    with repo.connect() as c:
        c.execute(
            "INSERT INTO jobs(id,service,state,iteration,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (job_id, service_name, 'INTAKE_VALIDATED', 1, now(), now()),
        )
    print('[setup] created job %s  state=INTAKE_VALIDATED' % job_id)
    return job_id


def fake_repository_intake_completion(repo, job_id):
    """Insert a synthetic COMPLETED run for repository-intake.

    dormancy-intelligence depends on repository-intake having completed.
    Repository-intake is fully deterministic (no model call) so faking its
    completion here is the minimal-friction way to satisfy the dependency check
    in AgentRegistry.dispatch() without needing a real ACE repository path.
    """
    run_id = uid()
    with repo.connect() as c:
        c.execute(
            "INSERT INTO agent_runs(id,job_id,agent_name,agent_version,agent_id,status,"
            "input_evidence,output_evidence,correlation_id,error,started_at,completed_at,"
            "operator_identity,requested_tools,iteration) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, job_id, 'Repository Intake', '1.0.0', 'repository-intake',
             'COMPLETED', '[]', '[]', uid(), '', now(), now(), 'seed', '[]', 1),
        )
    print('[setup] faked repository-intake COMPLETED  run_id=%s' % run_id)


def print_result(result):
    reasoning = result.get('reasoning') or {}
    mode      = reasoning.get('mode', 'UNKNOWN')
    passed    = mode == 'MODEL_GATEWAY'

    print('\n' + '=' * 60)
    print('RESULT: %s' % ('PASS' if passed else 'FAIL'))
    print('=' * 60)
    print('mode:            ', mode)
    print('model_alias:     ', reasoning.get('model_alias', ''))
    print('binding_source:  ', reasoning.get('binding_source', ''))
    print('prompt_name:     ', reasoning.get('prompt_name', ''))
    print('prompt_version:  ', reasoning.get('prompt_version', ''))
    print('request_id:      ', reasoning.get('request_id', ''))
    print('response_hash:   ', reasoning.get('response_hash', ''))
    print('status:          ', reasoning.get('status', ''))

    if not passed:
        print('\nFAIL reason:     ', reasoning.get('reason', 'none recorded'))
        print('\nDiagnosis:')
        reason = reasoning.get('reason', '')
        if 'no_approved_prompt_or_model' in reason:
            print('  -> Run scripts/seed_model_gateway.py first to register approved records.')
        elif 'gateway_refused' in reason:
            print('  -> gateway_request() was blocked:', reason)
            print('     Check connector_profiles and model_registry rows.')
        elif 'no_provider_execution_adapter' in reason:
            print('  -> provider=None was passed. Use --no-provider to see this intentionally.')
        elif 'provider_execution_failed' in reason:
            print('  -> The adapter was called but raised an error:', reason)
            print('     Check that the endpoint is reachable and the model is loaded.')
        else:
            print('  -> Unexpected reason. Full reasoning:', json.dumps(reasoning, indent=2))
    else:
        metrics = result.get('metrics') or {}
        print('\nAgent metrics:')
        print('  confidence:          ', metrics.get('confidence'))
        print('  known_signals:       ', metrics.get('known_signals'))
        print('  model_classification:', metrics.get('model_classification', '(not parsed as JSON)'))
        print('  model_agrees:        ', metrics.get('model_agrees', '(n/a)'))

    print()
    return passed


def main():
    parser = argparse.ArgumentParser(description='Verify real dormancy-intelligence model execution')
    parser.add_argument('--api-base',    default='http://localhost:11434/v1')
    parser.add_argument('--api-key',     default='ollama')
    parser.add_argument('--model',       default='qwen2.5:3b')
    parser.add_argument('--db',          default='factory-v2.db')
    parser.add_argument('--no-provider', action='store_true',
                        help='Pass provider=None (expect DETERMINISTIC_FALLBACK; useful to confirm '
                             'the gate works without a live endpoint)')
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print('ERROR: database not found at', args.db)
        print('Start the server once (python -m app.server) then run scripts/seed_model_gateway.py')
        sys.exit(1)

    repo     = Repository(args.db)
    registry = AgentRegistry(repo)

    # Setup
    job_id = create_job(repo)
    fake_repository_intake_completion(repo, job_id)

    # Provider
    if args.no_provider:
        provider = None
        print('[adapter] provider=None (expect DETERMINISTIC_FALLBACK)')
    else:
        provider = HttpProviderAdapter(
            api_base=args.api_base,
            api_key=args.api_key,
            model=args.model,
        )
        print('[adapter] HttpProviderAdapter -> %s  model=%s' % (args.api_base, args.model))

    # Dispatch
    print('\n[dispatch] dormancy-intelligence  job=%s' % job_id)
    try:
        result = registry.dispatch(
            job_id            = job_id,
            agent_id          = 'dormancy-intelligence',
            version           = '1.0.0',
            roles             = {'DORMANCY_ANALYST'},
            identity          = 'verify-script',
            params            = {
                'traffic_count':    12,
                'last_seen_days':   90,
                'deployed':         True,
                'already_migrated': False,
            },
            provider          = provider,
            channel           = 'SCRIPT',
        )
    except PermissionError as exc:
        print('\nERROR: dispatch blocked -', exc)
        print('\nCommon causes:')
        if 'model_registry' in str(exc):
            print('  -> Run scripts/seed_model_gateway.py to register the model aliases.')
        elif 'dependency' in str(exc):
            print('  -> Dependency check failed (should not happen; fake run was inserted).')
        elif 'role' in str(exc):
            print('  -> Role check failed; the script uses DORMANCY_ANALYST which is correct.')
        sys.exit(1)
    except Exception as exc:
        print('\nERROR:', type(exc).__name__, '-', exc)
        sys.exit(1)

    passed = print_result(result)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
