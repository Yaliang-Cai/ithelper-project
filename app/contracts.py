"""Strict internal proposals; none can mint trusted authority."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Intent=Literal['new_request','additional_info','correction','progress_report','question','cancel_request','handoff_request','unrelated','unclear']
Category=Literal['symptom','context','prior_attempt','reported_recovery','reported_failure','reported_approval','preference','request','correction']
Criterion=Literal['precise_access','access_and_user_verification','account_unlock','login_recovery','vpn_recovery','application_recovery']
Status=Literal['investigating','waiting_user','waiting_approval','waiting_external','verifying','resolved','handed_off','denied','cancelled']
QuestionIntent=Literal['affected_scope','error_detail','attempt_result','target_resource','reference_clarification','environment','general']


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class NewIssue(Strict):
    candidate_key:str;goal:str;quote:str;service_refs:list[str]
    criterion_id:Criterion|None=None;resource_id:str|None=None


class Observation(Strict):
    candidate_key:str;target_refs:list[str];category:Category;claim:str;quote:str;mapping_status:Literal['tentative','needs_clarification','superseded']


class Correction(Strict):
    candidate_key:str;observation_id:str;expected_version:int;claim:str;quote:str


class Relation(Strict):
    candidate_key:str;from_ref:str;to_ref:str;type:Literal['possible_shared_cause','depends_on','same_incident','duplicate_of'];support_refs:list[str]


class Unresolved(Strict):
    candidate_key:str;quote:str;candidate_issue_ids:list[str];clarification_question:str


class Interpretation(Strict):
    message_intents:list[Intent]
    new_issue_candidates:list[NewIssue]=Field(max_length=8)
    observations:list[Observation]=Field(max_length=20)
    corrections:list[Correction]=Field(max_length=20)
    relation_candidates:list[Relation]=Field(max_length=20)
    unresolved_references:list[Unresolved]=Field(max_length=20)


class IssueUpdate(Strict):
    issue_id:str;expected_version:int;requested_status:Status;criterion_id:Criterion;evidence_refs:list[str]


class Question(Strict):
    text:str;issue_refs:list[str];question_intent:QuestionIntent


class QuestionUpdate(Strict):
    question_id:str;status:Literal['answered','cancelled'];answer_message_id:str


class Hypothesis(Strict):
    hypothesis_id:str|None=None;expected_version:int|None=None
    issue_id:str;claim:str;status:Literal['tentative','supported','rejected'];support_refs:list[str];contradict_refs:list[str]


class Guidance(Strict):
    issue_id:str;remedy_id:str;normalized_parameters:dict;evidence_refs:list[str]


class Refinement(Strict):
    issue_id:str;expected_version:int;service_refs:list[str]|None=None;resource_id:str|None=None;criterion_id:Criterion|None=None


class RelationUpdate(Strict):
    relation_id:str;expected_version:int;status:Literal['candidate','rejected','superseded'];support_refs:list[str];contradict_refs:list[str]


class Turn(Strict):
    reply:str=Field(min_length=1,max_length=8000)
    issue_updates:list[IssueUpdate]=Field(max_length=20)
    questions:list[Question]=Field(max_length=8)
    question_updates:list[QuestionUpdate]=Field(max_length=8)
    hypotheses:list[Hypothesis]=Field(max_length=12)
    guidance:list[Guidance]=Field(max_length=2)
    refinements:list[Refinement]=Field(max_length=8)
    relation_updates:list[RelationUpdate]=Field(default_factory=list,max_length=12)


def submit_schema():
    return {'type':'function','function':{'name':'submit_turn','description':'Finalize this turn with validated state proposals and a concise reply. Guidance steps are rendered by backend from cited reviewed KB. Must be the only call in its response.', 'parameters':Turn.model_json_schema()}}


def refinement_schema():
    return {'type':'function','function':{'name':'refine_issue','description':'Internal guarded scope refinement: fill missing service/resource or strengthen acceptance before authorization/attempts. Cannot replace established target, weaken criteria, create issues or transplant approval. Use fresh returned version.', 'parameters':Refinement.model_json_schema()}}
