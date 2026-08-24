"""Framework-independent contracts for the W4 bounded planning strategy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any


CONTEXT_SCHEMA_VERSION = "conflux-weave.context-bundle.v1"
PLAN_SCHEMA_VERSION = "conflux-weave.plan-proposal.v1"
VALIDATION_SCHEMA_VERSION = "conflux-weave.plan-validation.v1"
BOUNDED_STRATEGY_ID = "bounded-arxiv-planner-v1"
SEARCH_TOOL_ID = "search_arxiv"
MAX_PLAN_BYTES = 16_384


class PlanningContractError(ValueError):
    """Stable fail-closed error raised at the planning contract boundary."""

    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        self.code = code
        self.path = path
        super().__init__(message)


class PlanRejectedError(PlanningContractError):
    """Raised when a rejected plan is presented to the Tool Gateway."""


class ConstraintKind(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    HARD = "hard"


class ActionType(StrEnum):
    TOOL_CALL = "tool_call"
    FINISH = "finish"


@dataclass(frozen=True, slots=True)
class PlanBudget:
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    retrieval_rounds: int = 0

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "tool_calls", "retrieval_rounds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PlanningContractError(
                    "budget_invalid",
                    f"{name} must be a non-negative integer",
                    path=f"$.budget_remaining.{name}",
                )

    def covers(self, required: PlanBudget) -> bool:
        return all(
            getattr(self, name) >= getattr(required, name)
            for name in ("input_tokens", "output_tokens", "tool_calls", "retrieval_rounds")
        )


@dataclass(frozen=True, slots=True)
class PlanConstraint:
    constraint_id: str
    kind: ConstraintKind
    text: str

    def __post_init__(self) -> None:
        _require_identifier(self.constraint_id, "$.constraints[].constraint_id")
        _require_text(self.text, "$.constraints[].text")


@dataclass(frozen=True, slots=True)
class ContextBundle:
    schema_version: str
    task_summary: str
    research_question: str
    constraints: tuple[PlanConstraint, ...]
    allowed_tools: tuple[str, ...]
    allowed_actions: tuple[ActionType, ...]
    budget_remaining: PlanBudget
    max_search_actions: int
    prior_decision_refs: tuple[str, ...]
    selected_evidence_refs: tuple[str, ...]
    output_schema: str
    stop_conditions: tuple[str, ...]
    builder_version: str
    input_refs: tuple[str, ...]
    content_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != CONTEXT_SCHEMA_VERSION:
            raise PlanningContractError("context_schema_unsupported", "unsupported ContextBundle schema")
        _require_text(self.task_summary, "$.task_summary")
        _require_text(self.research_question, "$.research_question")
        _require_identifier(self.builder_version, "$.builder_version")
        _require_identifier(self.output_schema, "$.output_schema")
        if not 1 <= self.max_search_actions <= 2:
            raise PlanningContractError(
                "context_search_limit_invalid",
                "max_search_actions must be between 1 and 2",
                path="$.max_search_actions",
            )
        _require_unique(self.allowed_tools, "$.allowed_tools")
        _require_unique(tuple(item.value for item in self.allowed_actions), "$.allowed_actions")
        if SEARCH_TOOL_ID not in self.allowed_tools:
            raise PlanningContractError(
                "context_tool_missing", "search_arxiv must be allowed", path="$.allowed_tools"
            )
        if set(self.allowed_actions) != {ActionType.TOOL_CALL, ActionType.FINISH}:
            raise PlanningContractError(
                "context_actions_invalid",
                "allowed_actions must contain tool_call and finish",
                path="$.allowed_actions",
            )
        _require_unique(tuple(item.constraint_id for item in self.constraints), "$.constraints")
        _require_string_tuple(self.stop_conditions, "$.stop_conditions", require_non_empty=True)
        _require_string_tuple(self.prior_decision_refs, "$.prior_decision_refs")
        _require_string_tuple(self.selected_evidence_refs, "$.selected_evidence_refs")
        _require_string_tuple(self.input_refs, "$.input_refs", require_non_empty=True)
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise PlanningContractError(
                "context_hash_invalid", "content_sha256 must be lowercase SHA-256", path="$.content_sha256"
            )
        if self.content_sha256 != _sha256_json(self.payload_without_hash()):
            raise PlanningContractError(
                "context_hash_mismatch", "ContextBundle content hash does not match its payload"
            )

    def payload_without_hash(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_summary": self.task_summary,
            "research_question": self.research_question,
            "constraints": [
                {
                    "constraint_id": item.constraint_id,
                    "kind": item.kind.value,
                    "text": item.text,
                }
                for item in self.constraints
            ],
            "allowed_tools": list(self.allowed_tools),
            "allowed_actions": [item.value for item in self.allowed_actions],
            "budget_remaining": asdict(self.budget_remaining),
            "max_search_actions": self.max_search_actions,
            "prior_decision_refs": list(self.prior_decision_refs),
            "selected_evidence_refs": list(self.selected_evidence_refs),
            "output_schema": self.output_schema,
            "stop_conditions": list(self.stop_conditions),
            "builder_version": self.builder_version,
            "input_refs": list(self.input_refs),
        }


@dataclass(frozen=True, slots=True)
class ContextBuilder:
    builder_version: str = "bounded-context-builder-v1"

    def build(
        self,
        *,
        task_summary: str,
        research_question: str,
        budget_remaining: PlanBudget,
        input_refs: tuple[str, ...],
        inclusion_constraints: tuple[str, ...] = (),
        exclusion_constraints: tuple[str, ...] = (),
        hard_constraints: tuple[str, ...] = (),
        prior_decision_refs: tuple[str, ...] = (),
        selected_evidence_refs: tuple[str, ...] = (),
        output_schema: str = "conflux-weave.paper-discovery-plan-output.v1",
        stop_conditions: tuple[str, ...] = (
            "stop after at most two search actions",
            "finish when the source boundary cannot verify a hard constraint",
        ),
        max_search_actions: int = 2,
    ) -> ContextBundle:
        normalized_summary = _require_text(task_summary, "$.task_summary")
        normalized_question = _require_text(research_question, "$.research_question")
        constraints: list[PlanConstraint] = []
        for kind, values in (
            (ConstraintKind.INCLUDE, inclusion_constraints),
            (ConstraintKind.EXCLUDE, exclusion_constraints),
            (ConstraintKind.HARD, hard_constraints),
        ):
            _require_string_tuple(values, f"$.{kind.value}_constraints")
            for index, text in enumerate(values, start=1):
                constraints.append(PlanConstraint(f"{kind.value}-{index:02d}", kind, text.strip()))
        payload = {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "task_summary": normalized_summary,
            "research_question": normalized_question,
            "constraints": [
                {
                    "constraint_id": item.constraint_id,
                    "kind": item.kind.value,
                    "text": item.text,
                }
                for item in constraints
            ],
            "allowed_tools": [SEARCH_TOOL_ID],
            "allowed_actions": [ActionType.TOOL_CALL.value, ActionType.FINISH.value],
            "budget_remaining": asdict(budget_remaining),
            "max_search_actions": max_search_actions,
            "prior_decision_refs": list(prior_decision_refs),
            "selected_evidence_refs": list(selected_evidence_refs),
            "output_schema": output_schema,
            "stop_conditions": list(stop_conditions),
            "builder_version": self.builder_version,
            "input_refs": list(input_refs),
        }
        return ContextBundle(
            schema_version=CONTEXT_SCHEMA_VERSION,
            task_summary=payload["task_summary"],
            research_question=payload["research_question"],
            constraints=tuple(constraints),
            allowed_tools=(SEARCH_TOOL_ID,),
            allowed_actions=(ActionType.TOOL_CALL, ActionType.FINISH),
            budget_remaining=budget_remaining,
            max_search_actions=max_search_actions,
            prior_decision_refs=prior_decision_refs,
            selected_evidence_refs=selected_evidence_refs,
            output_schema=output_schema,
            stop_conditions=stop_conditions,
            builder_version=self.builder_version,
            input_refs=input_refs,
            content_sha256=_sha256_json(payload),
        )


@dataclass(frozen=True, slots=True)
class SearchArguments:
    query: str
    max_results: int

    def __post_init__(self) -> None:
        _require_text(self.query, "$.actions[].arguments.query")
        if isinstance(self.max_results, bool) or not isinstance(self.max_results, int):
            raise PlanningContractError(
                "max_results_invalid",
                "max_results must be an integer",
                path="$.actions[].arguments.max_results",
            )


@dataclass(frozen=True, slots=True)
class PlanAction:
    action_id: str
    action_type: ActionType
    tool_id: str | None
    arguments: SearchArguments | None
    expected_evidence: tuple[str, ...]
    constraint_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.action_id, "$.actions[].action_id")
        _require_string_tuple(self.expected_evidence, "$.actions[].expected_evidence")
        _require_string_tuple(self.constraint_refs, "$.actions[].constraint_refs")
        if self.action_type is ActionType.FINISH:
            if self.tool_id is not None or self.arguments is not None or self.expected_evidence:
                raise PlanningContractError(
                    "finish_shape_invalid",
                    "finish cannot contain a tool, arguments or expected evidence",
                )
        elif self.tool_id is None or self.arguments is None:
            raise PlanningContractError(
                "tool_action_shape_invalid", "tool_call requires a tool and arguments"
            )
        else:
            _require_text(self.tool_id, "$.actions[].tool_id")


@dataclass(frozen=True, slots=True)
class PlanProposal:
    schema_version: str
    strategy_id: str
    strategy_version: str
    context_sha256: str
    objective: str
    actions: tuple[PlanAction, ...]
    stop_reason: str
    proposal_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != PLAN_SCHEMA_VERSION:
            raise PlanningContractError("plan_schema_unsupported", "unsupported PlanProposal schema")
        _require_text(self.strategy_id, "$.strategy_id")
        _require_identifier(self.strategy_version, "$.strategy_version")
        _require_sha256(self.context_sha256, "$.context_sha256")
        _require_text(self.objective, "$.objective")
        _require_text(self.stop_reason, "$.stop_reason")
        if not isinstance(self.actions, tuple) or not 1 <= len(self.actions) <= 3:
            raise PlanningContractError(
                "plan_actions_invalid", "actions must contain between 1 and 3 items", path="$.actions"
            )
        _require_sha256(self.proposal_sha256, "$.proposal_sha256")
        if self.proposal_sha256 != _sha256_json(self.payload_without_hash()):
            raise PlanningContractError(
                "proposal_hash_mismatch", "PlanProposal content hash does not match its payload"
            )

    def payload_without_hash(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "context_sha256": self.context_sha256,
            "objective": self.objective,
            "actions": [_action_payload(item) for item in self.actions],
            "stop_reason": self.stop_reason,
        }


def parse_plan_proposal(value: str | Mapping[str, Any]) -> PlanProposal:
    raw = _parse_json_object(value)
    _require_exact_keys(
        raw,
        {
            "schema_version",
            "strategy_id",
            "strategy_version",
            "context_sha256",
            "objective",
            "actions",
            "stop_reason",
        },
        "$",
    )
    if raw["schema_version"] != PLAN_SCHEMA_VERSION:
        raise PlanningContractError(
            "plan_schema_unsupported", "unsupported PlanProposal schema", path="$.schema_version"
        )
    strategy_id = _require_text(raw["strategy_id"], "$.strategy_id")
    strategy_version = _require_identifier(raw["strategy_version"], "$.strategy_version")
    context_sha256 = _require_sha256(raw["context_sha256"], "$.context_sha256")
    objective = _require_text(raw["objective"], "$.objective")
    stop_reason = _require_text(raw["stop_reason"], "$.stop_reason")
    raw_actions = raw["actions"]
    if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 3:
        raise PlanningContractError(
            "plan_actions_invalid", "actions must contain between 1 and 3 items", path="$.actions"
        )
    actions = tuple(_parse_action(item, index) for index, item in enumerate(raw_actions))
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "context_sha256": context_sha256,
        "objective": objective,
        "actions": [_action_payload(item) for item in actions],
        "stop_reason": stop_reason,
    }
    return PlanProposal(
        schema_version=PLAN_SCHEMA_VERSION,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        context_sha256=context_sha256,
        objective=objective,
        actions=actions,
        stop_reason=stop_reason,
        proposal_sha256=_sha256_json(payload),
    )


@dataclass(frozen=True, slots=True)
class PlanViolation:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidatedPlan:
    proposal: PlanProposal
    context_sha256: str
    required_budget: PlanBudget
    search_action_ids: tuple[str, ...]
    validation_sha256: str


@dataclass(frozen=True, slots=True)
class PlanValidationResult:
    schema_version: str
    accepted: bool
    violations: tuple[PlanViolation, ...]
    validated_plan: ValidatedPlan | None

    def require_validated(self) -> ValidatedPlan:
        if not self.accepted or self.validated_plan is None:
            codes = ", ".join(item.code for item in self.violations) or "unknown"
            raise PlanRejectedError("plan_rejected", f"plan was rejected: {codes}")
        return self.validated_plan


@dataclass(frozen=True, slots=True)
class PlanValidator:
    max_query_chars: int = 512
    max_results_per_search: int = 15

    def validate(self, context: ContextBundle, proposal: PlanProposal) -> PlanValidationResult:
        violations: list[PlanViolation] = []

        def reject(code: str, path: str, message: str) -> None:
            violations.append(PlanViolation(code, path, message))

        if proposal.strategy_id != BOUNDED_STRATEGY_ID:
            reject("strategy_not_allowed", "$.strategy_id", "only the bounded arXiv strategy is allowed")
        if proposal.context_sha256 != context.content_sha256:
            reject("context_hash_mismatch", "$.context_sha256", "plan was built for a different ContextBundle")

        action_ids = [item.action_id for item in proposal.actions]
        if len(action_ids) != len(set(action_ids)):
            reject("action_id_duplicate", "$.actions", "action ids must be unique")

        finish_indexes: list[int] = []
        search_actions: list[PlanAction] = []
        normalized_queries: set[str] = set()
        known_constraints = {item.constraint_id for item in context.constraints}
        covered_constraints: set[str] = set()

        for index, action in enumerate(proposal.actions):
            path = f"$.actions[{index}]"
            if action.action_type not in context.allowed_actions:
                reject("action_type_not_allowed", f"{path}.action_type", "action type is not allowed")
                continue
            if action.action_type is ActionType.FINISH:
                finish_indexes.append(index)
                if action.constraint_refs:
                    reject("finish_constraint_refs_invalid", f"{path}.constraint_refs", "finish cannot claim constraint coverage")
                continue
            search_actions.append(action)
            if action.tool_id not in context.allowed_tools:
                reject("tool_not_allowed", f"{path}.tool_id", "tool is not in the ContextBundle whitelist")
            if action.arguments is None:
                reject("tool_arguments_missing", f"{path}.arguments", "tool_call requires arguments")
            else:
                normalized = " ".join(action.arguments.query.split()).casefold()
                if not normalized or len(action.arguments.query) > self.max_query_chars:
                    reject("search_query_invalid", f"{path}.arguments.query", "query is empty or too long")
                if any(ord(char) < 32 and char not in "\t\n\r" for char in action.arguments.query):
                    reject("search_query_control_character", f"{path}.arguments.query", "query contains a control character")
                if normalized in normalized_queries:
                    reject("search_query_duplicate", f"{path}.arguments.query", "duplicate searches are not allowed")
                normalized_queries.add(normalized)
                if not 1 <= action.arguments.max_results <= self.max_results_per_search:
                    reject("max_results_invalid", f"{path}.arguments.max_results", "max_results exceeds the frozen boundary")
            if not action.expected_evidence:
                reject("expected_evidence_missing", f"{path}.expected_evidence", "tool_call must state expected evidence")
            unknown_refs = set(action.constraint_refs) - known_constraints
            if unknown_refs:
                reject("constraint_ref_unknown", f"{path}.constraint_refs", "plan references an unknown constraint")
            covered_constraints.update(action.constraint_refs)

        if len(finish_indexes) != 1:
            reject("finish_count_invalid", "$.actions", "plan must contain exactly one finish action")
        elif finish_indexes[0] != len(proposal.actions) - 1:
            reject("finish_not_last", f"$.actions[{finish_indexes[0]}]", "finish must be the last action")
        if not search_actions:
            reject("search_action_missing", "$.actions", "plan must contain at least one search action")
        if len(search_actions) > context.max_search_actions:
            reject("search_limit_exceeded", "$.actions", "plan exceeds the ContextBundle search limit")
        missing_constraints = known_constraints - covered_constraints
        if missing_constraints:
            reject("constraint_not_acknowledged", "$.actions", "one or more frozen constraints are not referenced")

        required = PlanBudget(tool_calls=len(search_actions), retrieval_rounds=len(search_actions))
        if not context.budget_remaining.covers(required):
            reject("plan_budget_exceeded", "$.actions", "remaining budget cannot cover the proposed searches")

        if violations:
            return PlanValidationResult(VALIDATION_SCHEMA_VERSION, False, tuple(violations), None)
        validation_payload = {
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "context_sha256": context.content_sha256,
            "proposal_sha256": proposal.proposal_sha256,
            "required_budget": asdict(required),
            "search_action_ids": [item.action_id for item in search_actions],
        }
        validated = ValidatedPlan(
            proposal=proposal,
            context_sha256=context.content_sha256,
            required_budget=required,
            search_action_ids=tuple(item.action_id for item in search_actions),
            validation_sha256=_sha256_json(validation_payload),
        )
        return PlanValidationResult(VALIDATION_SCHEMA_VERSION, True, (), validated)


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_id: str
    action_id: str
    output_artifact_refs: tuple[str, ...]


ToolHandler = Callable[[str, SearchArguments], ToolResult]


class ToolGateway:
    """Dispatch only actions from a plan accepted against its exact ContextBundle."""

    def __init__(
        self,
        handlers: Mapping[str, ToolHandler],
        *,
        validator: PlanValidator | None = None,
    ) -> None:
        self._handlers = dict(handlers)
        self._validator = validator or PlanValidator()
        if set(self._handlers) != {SEARCH_TOOL_ID}:
            raise PlanningContractError(
                "gateway_registry_invalid", "W4.2 Tool Gateway must register only search_arxiv"
            )

    def dispatch(
        self,
        context: ContextBundle,
        proposal: PlanProposal,
        action_id: str,
    ) -> ToolResult:
        validated = self._validator.validate(context, proposal).require_validated()
        if validated.context_sha256 != context.content_sha256:
            raise PlanningContractError("gateway_context_mismatch", "validated context does not match")
        matches = [item for item in proposal.actions if item.action_id == action_id]
        if len(matches) != 1:
            raise PlanningContractError("gateway_action_missing", "action is not present in the validated plan")
        action = matches[0]
        if action.action_type is not ActionType.TOOL_CALL or action.arguments is None or action.tool_id is None:
            raise PlanningContractError("gateway_action_not_executable", "only tool_call actions can be dispatched")
        handler = self._handlers.get(action.tool_id)
        if handler is None:
            raise PlanningContractError("gateway_tool_unregistered", "validated tool is not registered")
        result = handler(action.action_id, action.arguments)
        if not isinstance(result, ToolResult):
            raise PlanningContractError("gateway_result_invalid", "tool must return ToolResult")
        if result.tool_id != action.tool_id or result.action_id != action.action_id:
            raise PlanningContractError("gateway_result_mismatch", "tool result does not match the authorized action")
        return result


def _parse_action(value: Any, index: int) -> PlanAction:
    path = f"$.actions[{index}]"
    if not isinstance(value, dict):
        raise PlanningContractError("plan_action_invalid", "action must be an object", path=path)
    _require_exact_keys(
        value,
        {"action_id", "action_type", "tool_id", "arguments", "expected_evidence", "constraint_refs"},
        path,
    )
    action_id = _require_identifier(value["action_id"], f"{path}.action_id")
    try:
        action_type = ActionType(value["action_type"])
    except (TypeError, ValueError) as exc:
        raise PlanningContractError(
            "action_type_invalid", "action_type must be tool_call or finish", path=f"{path}.action_type"
        ) from exc
    expected = _parse_string_list(value["expected_evidence"], f"{path}.expected_evidence")
    constraint_refs = _parse_string_list(value["constraint_refs"], f"{path}.constraint_refs")
    if action_type is ActionType.FINISH:
        if value["tool_id"] is not None or value["arguments"] != {} or expected:
            raise PlanningContractError(
                "finish_shape_invalid",
                "finish requires null tool_id, empty arguments and empty expected_evidence",
                path=path,
            )
        return PlanAction(action_id, action_type, None, None, (), constraint_refs)
    tool_id = _require_text(value["tool_id"], f"{path}.tool_id")
    raw_arguments = value["arguments"]
    if not isinstance(raw_arguments, dict):
        raise PlanningContractError("tool_arguments_invalid", "arguments must be an object", path=f"{path}.arguments")
    _require_exact_keys(raw_arguments, {"query", "max_results"}, f"{path}.arguments")
    query = _require_text(raw_arguments["query"], f"{path}.arguments.query")
    max_results = raw_arguments["max_results"]
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise PlanningContractError(
            "max_results_invalid", "max_results must be an integer", path=f"{path}.arguments.max_results"
        )
    return PlanAction(
        action_id,
        action_type,
        tool_id,
        SearchArguments(query, max_results),
        expected,
        constraint_refs,
    )


def _action_payload(action: PlanAction) -> dict[str, Any]:
    arguments: dict[str, Any]
    if action.arguments is None:
        arguments = {}
    else:
        arguments = {"query": action.arguments.query, "max_results": action.arguments.max_results}
    return {
        "action_id": action.action_id,
        "action_type": action.action_type.value,
        "tool_id": action.tool_id,
        "arguments": arguments,
        "expected_evidence": list(action.expected_evidence),
        "constraint_refs": list(action.constraint_refs),
    }


def _parse_json_object(value: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_PLAN_BYTES:
            raise PlanningContractError("plan_too_large", "PlanProposal exceeds the byte limit")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PlanningContractError("plan_json_invalid", "PlanProposal is not valid JSON") from exc
    elif isinstance(value, Mapping):
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_PLAN_BYTES:
                raise PlanningContractError("plan_too_large", "PlanProposal exceeds the byte limit")
            parsed = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            if isinstance(exc, PlanningContractError):
                raise
            raise PlanningContractError("plan_json_invalid", "PlanProposal must contain JSON values") from exc
    else:
        raise PlanningContractError("plan_root_invalid", "PlanProposal root must be an object")
    if not isinstance(parsed, dict):
        raise PlanningContractError("plan_root_invalid", "PlanProposal root must be an object")
    return parsed


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise PlanningContractError(
            "schema_keys_invalid",
            f"object keys differ; missing={missing}, extra={extra}",
            path=path,
        )


def _require_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningContractError("text_invalid", "value must be a non-empty string", path=path)
    return value.strip()


def _require_identifier(value: Any, path: str) -> str:
    text = _require_text(value, path)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", text):
        raise PlanningContractError("identifier_invalid", "value is not a valid identifier", path=path)
    return text


def _require_sha256(value: Any, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise PlanningContractError("sha256_invalid", "value must be lowercase SHA-256", path=path)
    return value


def _parse_string_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PlanningContractError("string_list_invalid", "value must be a list", path=path)
    result = tuple(_require_text(item, f"{path}[]") for item in value)
    _require_unique(result, path)
    return result


def _require_string_tuple(
    value: tuple[str, ...], path: str, *, require_non_empty: bool = False
) -> None:
    if not isinstance(value, tuple) or (require_non_empty and not value):
        raise PlanningContractError("string_tuple_invalid", "value must be a non-empty tuple", path=path)
    for item in value:
        _require_text(item, f"{path}[]")
    _require_unique(value, path)


def _require_unique(value: tuple[str, ...], path: str) -> None:
    if len(value) != len(set(value)):
        raise PlanningContractError("values_duplicate", "values must be unique", path=path)


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
