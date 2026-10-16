"""Dormancy Intelligence Agent.

Classifies whether a service is worth migrating at all, from deployment and
telemetry evidence.

The model classifies, guided by the supplied signals alone, and
``DormancyService.classify`` -- the rule that reads an absent signal as absent
rather than negative -- is the fallback. Both verdicts are kept: the run records
the computed classification alongside the persisted one and whether they agree,
so a disagreement is visible rather than resolved silently. The classification
decides whether a migration proceeds at all, which makes "the rules and the
model differed here" worth more than either answer on its own.

``confidence`` stays computed in every case. It measures how much of the
evidence was supplied, not how sure anyone is of the verdict, so a model's own
confidence is advisory and recorded in the metrics rather than substituted for
the measured figure a gate reads.
"""
import json
from app.agents.artefact import produce
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.services.factory_v3 import DormancyService


class DormancyIntelligenceAgent(BaseAgent):
    AGENT_ID = 'dormancy-intelligence'
    SKILL = None  # deliberately none: the migration skill has no dormancy content (agent-skills/README.md)
    TOOLS = ('telemetry.read', 'registry.read', 'evidence.write')
    OUTPUTS = ('classification', 'evidence', 'confidence')
    PROMPT = 'agent/dormancy-intelligence/classification-rationale'

    def execute(self, context):
        service = DormancyService(self.repo)
        supplied = service.signals(
            context.param('traffic_count', None),
            context.param('last_seen_days', None),
            context.param('deployed', None),
            context.param('already_migrated', None))
        verdict, provenance = produce(context, self.PROMPT, supplied,
                                      lambda: service.classify(supplied))
        assessment = service.record(context.job_id, supplied, verdict)
        computed = service.classify(supplied)
        metrics = {'confidence': assessment['confidence'],
                   'known_signals': sum(v is not None for v in supplied.values()),
                   'computed_classification': computed['classification'],
                   # Whether the model reached the same verdict the rules do.
                   # Recorded either way: a disagreement is the signal worth
                   # having, and the rules are not silently right.
                   'agrees_with_rules':
                       assessment['classification'] == computed['classification'],
                   'model_confidence': verdict.get('confidence'),
                   'missing_evidence': verdict.get('missing_evidence', [])}
        payload = json.dumps({'classification': assessment['classification'],
                              'confidence': assessment['confidence'],
                              'evidence': supplied,
                              'rationale': assessment['rationale']}, sort_keys=True)
        return AgentResult(
            outputs={'classification': assessment['classification'],
                     'evidence': assessment['id'],
                     'confidence': assessment['confidence']},
            evidence=(EvidenceRef('DORMANCY_ASSESSMENT', '1.0.0',
                                  'dormancy://' + assessment['id'], payload,
                                  {'classification': assessment['classification']}),),
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(DormancyIntelligenceAgent))
