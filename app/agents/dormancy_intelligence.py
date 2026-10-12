"""Dormancy Intelligence Agent.

Classifies whether a service is worth migrating at all, from deployment and
telemetry evidence. ``DormancyService.assess`` holds the classification rule and
derives a confidence from how much of the evidence was actually supplied --
which is the point: an absent signal lowers confidence rather than being read as
a negative one.

The model gateway is consulted for a second opinion when an approved prompt and
a provider adapter are configured. The deterministic classification stays
authoritative and is what gets persisted; a disagreement is recorded in the run
metrics rather than resolved silently, because the classification decides
whether a migration proceeds and must be reproducible from the evidence alone.
"""
import json
from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.reasoning import ReasoningGateway
from app.services.factory_v3 import DormancyService


class DormancyIntelligenceAgent(BaseAgent):
    AGENT_ID = 'dormancy-intelligence'
    TOOLS = ('telemetry.read', 'registry.read', 'evidence.write')
    OUTPUTS = ('classification', 'evidence', 'confidence')
    PROMPT = 'agent/dormancy-intelligence/classification-rationale'

    def execute(self, context):
        assessment = DormancyService(self.repo).assess(
            context.job_id,
            context.param('traffic_count', None),
            context.param('last_seen_days', None),
            context.param('deployed', None),
            context.param('already_migrated', None))
        supplied = json.loads(assessment['evidence'] or '{}')
        answer, provenance = ReasoningGateway(self.repo).reason(context, self.PROMPT, supplied)
        metrics = {'confidence': assessment['confidence'],
                   'known_signals': sum(v is not None for v in supplied.values())}
        if isinstance(answer, dict) and answer.get('classification'):
            metrics['model_classification'] = answer['classification']
            metrics['model_agrees'] = answer['classification'] == assessment['classification']
            metrics['model_missing_evidence'] = answer.get('missing_evidence', [])
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
