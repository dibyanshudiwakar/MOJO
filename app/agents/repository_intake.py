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
from app.agents.artefact import produce
from app.services.factory_v3 import IntakeService


class RepositoryIntakeAgent(BaseAgent):
    AGENT_ID = 'repository-intake'
    SKILL = 'iib-to-ms-repository-intake'
    TOOLS = ('repository.read', 'evidence.write')
    OUTPUTS = ('intake_manifest', 'gaps')
    PROMPT = 'agent/repository-intake/inventory'

    def execute(self, context):
        service = IntakeService(self.repo)
        branch = context.param('branch', 'main')
        root = service.root(context.param('path'))
        listing = service.listing(root)
        git_gaps, revision = service.git_gaps(root, branch, context.param('revision', ''))
        # Paths and sizes, never the per-file hashes: the listing is almost
        # nothing but hashes, and a payload of them trips the PAN data-loss
        # rule often enough to make every run a refusal. The hashes are
        # computed and stamped in record_manifest either way -- they are what
        # an approval binds, and no model can produce a SHA-256 of bytes.
        classified, provenance = produce(
            context, self.PROMPT,
            {'branch': branch,
             'files': [{'path': x['path'], 'bytes': x['bytes']} for x in listing]},
            lambda: service.compute_inventory(listing))
        manifest = service.record_manifest(context.job_id, root, branch, revision,
                                            classified, listing, git_gaps)
        gaps = json.loads(manifest['gaps'] or '[]')
        inventory = json.loads(manifest['inventory'])
        metrics = {'file_count': len(inventory.get('files', [])),
                   'flow_count': len(inventory.get('flows', [])),
                   'gap_count': len(gaps),
                   'inventory_source': provenance.get('artefact_source', 'DETERMINISTIC')}
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
