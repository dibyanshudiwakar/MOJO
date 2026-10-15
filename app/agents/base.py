"""The execution contract every agent worker implements.

``config/agent-catalogue.v1.json`` is the governed declaration of what an agent
may reach; this module is how an implementation declares what it *actually*
reaches, so the two can be compared. An implementation that requests a tool its
approved contract does not grant, or returns an output the contract does not
declare, is contract drift in the worker rather than in the catalogue -- and is
refused by ``AgentRegistry.dispatch`` for the same reason
``Repository._seed_agent_catalogue`` refuses a changed contract hash.

Agents take plain values and a ``Repository`` so each one stays runnable and
unit-testable on its own, with no HTTP layer and no registry. The governance
that must not be bypassable -- role checks, tool allow-lists, dependency
ordering, evidence registration, audit records -- lives in the registry, not
here, so an agent cannot grant itself more than its contract allows by being
invoked directly.
"""
import json
from dataclasses import dataclass, field
from app.repository import digest


class AgentError(RuntimeError):
    """An agent could not complete. Recorded as a FAILED run, not a crash."""


_REQUIRED = object()


@dataclass(frozen=True)
class EvidenceRef:
    """One artefact an agent produced, in the form an approval can bind to.

    ``content`` must be the exact serialisation the artefact's own
    ``content_hash`` was computed over. That equality is what lets a single
    approval satisfy both ``FactoryService._valid_approvals`` (which re-checks
    the evidence row's hash against ``artifact_bindings``) and
    ``MappingAndPlanningService._approved`` (which looks for the *artefact's*
    hash among those same bindings). If the two hashes diverge, a gate approval
    silently stops authorising the artefact it was granted for.
    """
    kind: str
    version: str
    uri: str
    content: str
    metadata: dict = field(default_factory=dict)

    @property
    def content_hash(self):
        return digest(self.content)


@dataclass(frozen=True)
class Clarification:
    """A question that must be answered before the lifecycle may advance.

    Recording one blocks every transition except STOPPED/FAILED/ROLLED_BACK
    (``state_machine.validate_transition``), so only agents whose contract
    grants ``clarification.create`` may return these.
    """
    stage: str
    question: str
    owner: str
    impact: str
    evidence_ref: str = ''


@dataclass
class AgentResult:
    """What an agent produced, before the registry records any of it."""
    status: str = 'COMPLETED'
    outputs: dict = field(default_factory=dict)
    evidence: tuple = ()
    clarifications: tuple = ()
    next_state: str = ''
    commands: tuple = ()
    error: str = ''
    metrics: dict = field(default_factory=dict)
    reasoning: dict = field(default_factory=dict)

    @property
    def failed(self):
        return self.status == 'FAILED' or bool(self.error)


class AgentContext:
    """Everything an agent is allowed to know about its invocation.

    Deliberately narrow: an agent receives the job id rather than the job row,
    and parameters rather than a request body, so it cannot act on a
    caller-supplied identity, role or project scope. Those were verified before
    the context was built and must not be re-derivable from it.
    """

    def __init__(self, repo, job_id, identity='system', roles=(), params=None,
                 correlation_id='', iteration=1, provider=None, channel='API',
                 default_model_alias='', effective_model_alias='', binding_source='DEFAULT',
                 logger=None, agent_id=''):
        self.repo = repo
        self.job_id = job_id
        self.identity = identity
        self.roles = tuple(roles)
        self.params = dict(params or {})
        self.correlation_id = correlation_id
        self.iteration = iteration
        # An optional provider execution adapter. Absent by default: the model
        # gateway records a governed request but never calls a provider, so
        # reasoning falls back to the deterministic path unless one is supplied.
        self.provider = provider
        self.channel = channel
        # The seeded default LLM alias for this agent (e.g. "reasoning-large",
        # "coding"). Empty string means no alias was assigned.
        self.default_model_alias = default_model_alias
        # The resolved alias after considering any active AGENT_OVERRIDE binding.
        # ReasoningGateway reads this to select the correct model registry row.
        self.effective_model_alias = effective_model_alias
        # 'DEFAULT' or 'AGENT_OVERRIDE' — recorded in model_provenance.
        self.binding_source = binding_source
        # Optional StepLogger for structured pipeline step logs.
        self.logger = logger
        # The catalogue agent_id, used by ReasoningGateway to load the
        # agent's skill from agent-skills/.
        self.agent_id = agent_id

    def param(self, name, default=_REQUIRED):
        value = self.params.get(name, default)
        if value is _REQUIRED:
            raise ValueError('missing_param:' + name)
        return value

    def job(self):
        row = self.repo.one('select * from jobs where id=?', (self.job_id,))
        if not row:
            raise ValueError('job_not_found')
        return row

    @property
    def params_hash(self):
        return digest(json.dumps(self.params, sort_keys=True, default=str))


class BaseAgent:
    """Base class for the 14 catalogue agents.

    Subclasses set ``AGENT_ID`` to the catalogue's stable id, ``TOOLS`` to the
    subset of the approved allow-list they request, and ``PROMPT`` to the
    governed prompt name when they reason through the model gateway.
    """
    AGENT_ID = ''
    VERSION = '1.0.0'
    TOOLS = ()
    PROMPT = None
    #: Contract ``produced_outputs`` keys this implementation fills. Checked
    #: against the catalogue so an implementation cannot quietly return
    #: something its contract never declared.
    OUTPUTS = ()

    def __init__(self, repo):
        self.repo = repo

    def execute(self, context):
        raise NotImplementedError('agent_execute_not_implemented:' + self.AGENT_ID)

    def context(self, job_id, **kwargs):
        """Build a context bound to this agent's repository."""
        return AgentContext(self.repo, job_id, **kwargs)

    def run(self, job_id, **kwargs):
        """Execute directly, without the registry.

        For isolated development and tests. It records the agent's artefacts
        (its services do that) but no ``agent_runs`` row, no evidence, no
        clarifications and no audit entry -- everything the registry adds. Use
        ``AgentRegistry.dispatch`` for a governed run.
        """
        return self.execute(self.context(job_id, **kwargs))
