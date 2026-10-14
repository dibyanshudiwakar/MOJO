"""Repository Intake Agent.

Inspects the source repositories named for a job and records an intake manifest:
the file inventory with a hash per file, plus the gaps that make the intake
incomplete. Wraps ``IntakeService``; the inventory and gap detection live there.

Note that gaps are returned as an *output* rather than raised as clarifications,
even though an unresolved gap is exactly the kind of thing a clarification
exists for. This agent's approved contract grants ``repository.read`` and
``evidence.write`` and nothing else -- creating a clarification blocks every
lifecycle transition, so it is a governed capability that this contract
withholds. The orchestrator, which does hold ``clarification.create``, is
responsible for turning a reported gap into a question.
"""
import json
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import ReasoningGateway
from app.services.factory_v3 import IntakeService


class RepositoryIntakeAgent(BaseAgent):
    AGENT_ID = 'repository-intake'
    TOOLS = ('repository.read', 'evidence.write')
    OUTPUTS = ('intake_manifest', 'gaps')
    PROMPT = 'agent/repository-intake/gap-triage'

    def execute(self, context):
        manifest = IntakeService(self.repo).scan(
            context.job_id,
            context.param('path'),
            context.param('branch', 'main'),
            context.param('revision', ''))
        gaps = json.loads(manifest['gaps'] or '[]')
        inventory = json.loads(manifest['inventory'])
        # Counts and gap labels only. The inventory itself carries a hash per
        # file, and a payload of hashes trips the PAN data-loss rule often
        # enough to make every run a refusal.
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, {
            'gaps': gaps,
            'file_count': len(inventory.get('files', [])),
            'flow_count': len(inventory.get('flows', [])),
            'branch': manifest['branch']})
        metrics = {'file_count': len(inventory.get('files', [])),
                   'flow_count': len(inventory.get('flows', [])),
                   'gap_count': len(gaps)}
        if isinstance(answer, dict) and answer.get('ranked_gaps'):
            metrics['model_ranked_gaps'] = answer['ranked_gaps']
            metrics['model_blocking_gaps'] = [x.get('gap') for x in answer['ranked_gaps']
                                              if isinstance(x, dict) and x.get('severity') == 'BLOCKING']
        return AgentResult(
            status='COMPLETED',
            outputs={'intake_manifest': manifest['id'], 'gaps': gaps},
            # The manifest's own content_hash is the digest of this exact
            # inventory string, so an approval binding this evidence id also
            # binds the manifest.
            evidence=(EvidenceRef('INTAKE_MANIFEST', '1.0.0', 'intake://' + manifest['id'],
                                  manifest['inventory'],
                                  {'repository_uri': manifest['repository_uri'],
                                   'branch': manifest['branch'],
                                   'revision': manifest['revision'],
                                   'gaps': gaps}),),
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(RepositoryIntakeAgent))
