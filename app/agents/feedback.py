"""Feedback Agent.

Closes the loop that previously ended at a report: once the generated code has
been analysed, this agent applies those findings *into* the code and re-runs
the same quality gate to prove it. Wraps ``CodeFeedbackService``, which holds
the repairs and the bounded fix/re-validate loop.

The contract grants ``repository.write`` and ``build.execute`` because this
agent genuinely does both -- it edits generated sources and re-runs the gate
over them. It is the first agent allowed to modify what ``code-generation``
produced, which is why the loop is bounded and why every pass records the
gate's own verdict: the evidence for a fix is the check passing afterwards, not
this agent asserting it fixed something.

It does not hold ``clarification.create``. Behavioural variance between ACE and
the migrated service is not something a mechanical repair can settle, so those
findings are returned under ``unresolved`` for the remediation agent rather
than raised here as questions this agent cannot answer.
"""
import json

from app.agents.base import AgentResult, BaseAgent, EvidenceRef
from app.agents.artefact import produce
from app.agents.reasoning import NOT_CONSULTED
from app.services import workspace
from app.services.feedback import CodeFeedbackService


class FeedbackAgent(BaseAgent):
    AGENT_ID = 'feedback'
    TOOLS = ('repository.write', 'build.execute', 'evidence.write')
    OUTPUTS = ('applied_fixes', 'quality_status', 'unresolved')
    PROMPT = 'agent/feedback/finding-triage'
    #: Deliberately none: the source migration skill covers producing the target
    #: service, not repairing it afterwards, so any skill here would be invented.
    #: Declared in agent-skills/skills-map.json under agents_without_skill.
    SKILL = None

    def execute(self, context):
        plan_id = context.param('plan_id')
        # Confined even though the value arrives from code-generation's own
        # output rather than a request body: this agent writes to the path, so
        # it is resolved against the workspace root like every other
        # caller-influenced path in the factory.
        project_root = str(workspace.resolve(context.param('project_root'), must_exist=True))
        service = CodeFeedbackService(self.repo)
        result = service.apply(
            context.job_id, plan_id, project_root,
            max_passes=int(context.param('max_passes', 3)),
            execute_build=bool(context.param('execute_build', False)),
            # Carries the provider and correlation id, which is what enables the
            # model-assisted stage. Without it the agent is deterministic-only.
            context=context)

        # Consulted about what is left, never about what was changed: the fixes
        # are deterministic and the gate has already ruled on them. The payload
        # carries check names and counts, not generated source.
        # The repair itself is already model-driven, inside CodeFeedbackService:
        # it proposes targeted edits, the gate re-runs, and an edit that does not
        # reduce the failing set is reverted. This call is about what is *left*
        # after that. The old fallback restated each unfixed check with
        # "no mechanical fix clears this check" as the reason -- which is what
        # the gate already said, attributed to a triage that never happened.
        triage, provenance = produce(
            context, self.PROMPT,
            {'unfixed_checks': result['unfixed_checks'],
             'fix_count': len(result['applied_fixes']),
             'passes': len(result['passes']),
             'unresolved_kinds': sorted({x['kind'] for x in result['unresolved']})},
        ) if (result['unfixed_checks'] or result['unresolved']) else ({'recommendations': []}, {
            # Nothing outstanding, so nothing to triage. The model is not asked
            # rather than asked and unavailable -- a clean gate is a result.
            'mode': NOT_CONSULTED, 'prompt_name': self.PROMPT,
            'reason': 'quality gate clean and nothing outstanding to triage',
            'artefact_source': 'NONE'})

        rounds = result.get('model_rounds') or []
        metrics = {
            'fix_count': len(result['applied_fixes']),
            'pass_count': len(result['passes']),
            'quality_status': result['quality_status'],
            'unfixed_checks': result['unfixed_checks'],
            'unresolved_count': len(result['unresolved']),
            # Whether the model stage ran, and what the gate did with its edits.
            'model_rounds': len(rounds),
            'model_outcomes': [x.get('outcome') for x in rounds],
            'model_edits_kept': sum(len(x.get('applied') or []) for x in rounds),
            'model_edits_refused': sum(len(x.get('refused') or []) for x in rounds),
        }
        metrics['recommendations'] = triage.get('recommendations') or []
        metrics['model_attempts'] = provenance.get('attempts', 0)

        payload = json.dumps({
            'project_root': result['project_root'],
            'quality_run_id': result['quality_run_id'],
            'quality_status': result['quality_status'],
            'applied_fixes': result['applied_fixes'],
            'passes': result['passes'],
            'unfixed_checks': result['unfixed_checks'],
            'unresolved': result['unresolved'],
            'model_rounds': rounds,
        }, sort_keys=True)

        # A gate still failing after the budget is spent is a real failure: the
        # generated code did not reach a shippable state, and saying COMPLETED
        # would hand the next step code the gate rejects.
        failed = bool(result['unfixed_checks'])
        return AgentResult(
            status='FAILED' if failed else 'COMPLETED',
            error=('quality_gate_unresolved:' + ','.join(result['unfixed_checks'])) if failed else '',
            outputs={'applied_fixes': result['applied_fixes'],
                     'quality_status': result['quality_status'],
                     'unresolved': result['unresolved']},
            evidence=(EvidenceRef('CODE_FEEDBACK', '1.0.0',
                                  'feedback://' + result['quality_run_id'], payload,
                                  {'plan_id': plan_id,
                                   'quality_run_id': result['quality_run_id'],
                                   'quality_status': result['quality_status'],
                                   'fix_count': len(result['applied_fixes'])}),),
            metrics=metrics, reasoning=provenance)


if __name__ == '__main__':
    from app.agents.cli import main
    raise SystemExit(main(FeedbackAgent))
