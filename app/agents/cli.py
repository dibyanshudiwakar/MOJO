"""Standalone runner shared by every agent module.

Each agent is executable on its own::

    python -m app.agents.ace_analysis --db factory-v2.db --job <job-id> \\
        --param path=workspace/ace --param version=1.0.0 --token <jwt>

``--token`` is the governed path: the bearer token is verified by
``IdentityService``, and the identity, roles and project/environment scope come
from its claims exactly as they do over HTTP.

Without a token, ``--roles`` and ``--identity`` are accepted and the actor is
recorded as ``cli:<identity>``. This is not a way around RBAC and is not
pretending to be one -- a caller who can run this command already has write
access to the database file, so role checks at this layer would be theatre. What
matters is that the run is *attributable*: the channel is recorded on the agent
run, in the workflow event and in the audit ledger, so a CLI-produced artefact is
distinguishable from one produced through the governed API. The gates themselves
are unaffected either way; a CLI run can produce artefacts but cannot approve
them or promote a job.

``--direct`` skips the registry entirely and calls ``execute`` for isolated
debugging: no run record, no evidence registration, no audit entry. The default
is a governed dispatch.
"""
import argparse
import json
import sys
from app.agents import IMPLEMENTATIONS
from app.repository import Repository
from app.services.agents import AgentRegistry
from app.services.auth import IdentityService


def _value(text):
    """Parse a ``--param k=v`` value as JSON, falling back to the raw string."""
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _prog(agent_cls):
    """The command that runs this agent.

    ``__module__`` is ``__main__`` under ``python -m``, so the usage line would
    read ``python -m __main__``. The registry knows the real module path.
    """
    target = IMPLEMENTATIONS.get(agent_cls.AGENT_ID, '')
    return 'python -m ' + (target.partition(':')[0] or agent_cls.__module__)


def parse(agent_cls, argv=None):
    parser = argparse.ArgumentParser(
        prog=_prog(agent_cls),
        description='Run the %s agent (%s).' % (agent_cls.AGENT_ID, agent_cls.VERSION))
    parser.add_argument('--db', required=True, help='database dsn, e.g. factory-v2.db')
    parser.add_argument('--job', required=True, help='job id the run belongs to')
    parser.add_argument('--param', action='append', default=[], metavar='KEY=VALUE',
                        help='agent parameter; the value is parsed as JSON when possible')
    parser.add_argument('--params-json', default='', help='all parameters as one JSON object')
    parser.add_argument('--token', default='', help='bearer token; derives identity, roles and scope')
    parser.add_argument('--identity', default='operator', help='actor when no token is supplied')
    parser.add_argument('--roles', default='', help='comma-separated roles when no token is supplied')
    parser.add_argument('--direct', action='store_true',
                        help='call execute() without the registry (no run record or evidence)')
    parser.add_argument('--input-evidence', default='', help='comma-separated registered evidence ids')
    return parser.parse_args(argv)


def _principal(args, repo):
    """Identity, roles and channel for this invocation."""
    if args.token:
        principal = IdentityService().authenticate('Bearer ' + args.token)
        job = repo.one('select id,project_scope,environment from jobs where id=?', (args.job,))
        if not job:
            raise ValueError('job_not_found')
        IdentityService().authorize_job(principal, job)
        return principal.identity, tuple(principal.roles), 'API'
    roles = tuple(x.strip() for x in args.roles.split(',') if x.strip())
    sys.stderr.write(
        'warning: running without a bearer token; the run is recorded as channel=CLI '
        'and attributed to cli:%s\n' % args.identity)
    return 'cli:' + args.identity, roles, 'CLI'


def main(agent_cls, argv=None):
    args = parse(agent_cls, argv)
    params = json.loads(args.params_json) if args.params_json else {}
    for item in args.param:
        key, sep, raw = item.partition('=')
        if not sep:
            sys.stderr.write('error: --param expects KEY=VALUE, got %r\n' % item)
            return 2
        params[key.strip()] = _value(raw)
    repo = Repository(args.db)
    try:
        identity, roles, channel = _principal(args, repo)
    except (PermissionError, ValueError) as exc:
        sys.stderr.write('error: %s\n' % exc)
        return 3
    agent = agent_cls(repo)
    try:
        if args.direct:
            result = agent.run(args.job, identity=identity, roles=roles, params=params, channel=channel)
            payload = {'agent_id': agent.AGENT_ID, 'mode': 'direct', 'status': result.status,
                       'outputs': result.outputs, 'metrics': result.metrics,
                       'reasoning': result.reasoning, 'error': result.error,
                       'evidence': [{'kind': x.kind, 'content_hash': x.content_hash} for x in result.evidence],
                       'clarifications': [x.question for x in result.clarifications]}
        else:
            evidence = [x.strip() for x in args.input_evidence.split(',') if x.strip()]
            payload = AgentRegistry(repo).dispatch(
                args.job, agent.AGENT_ID, agent.VERSION, roles, identity, params,
                input_evidence=evidence, channel=channel)
    except (PermissionError, ValueError) as exc:
        sys.stderr.write('error: %s: %s\n' % (type(exc).__name__, exc))
        return 4
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + '\n')
    return 0 if not payload.get('error') else 1
