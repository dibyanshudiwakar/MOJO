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
from app.services.factory_v3 import IntakeService


class RepositoryIntakeAgent(BaseAgent):
    AGENT_ID = 'repository-intake'
    TOOLS = ('repository.read', 'evidence.write')
    OUTPUTS = ('intake_manifest', 'gaps')

    def execute(self, context):
        manifest = IntakeService(self.repo).scan(
            context.job_id,
            context.param('path'),
            context.param('branch', 'main'),
            context.param('revision', ''))
        gaps = json.loads(manifest['gaps'] or '[]')
        inventory = json.loads(manifest['inventory'])
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
            metrics={'file_count': len(inventory.get('files', [])),
                     'flow_count': len(inventory.get('flows', [])),
                     'gap_count': len(gaps)})


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(RepositoryIntakeAgent))
