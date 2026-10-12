from dataclasses import dataclass, asdict
from typing import Any
@dataclass(frozen=True)
class Actor: identity:str; roles:tuple[str,...]
@dataclass(frozen=True)
class AgentContract: name:str; version:str; accepted_inputs:tuple[str,...]; produced_outputs:tuple[str,...]; required_role:str='AGENT_OPERATOR'
@dataclass
class ComparisonPolicy:
    ignored_paths:list[str]; masks:list[str]; numeric_tolerance:float=0.0; ordering_significant:bool=True
@dataclass
class ApprovalRequest:
    job_id:str; gate:str; decision:str; approver_role:str; approver_identity:str; evidence_ids:list[str]; comments:str=''; conditions:str=''
def data(x:Any): return asdict(x)
