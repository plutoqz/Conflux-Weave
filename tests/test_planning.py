import json

import pytest

from conflux_weave.planning import (
    BOUNDED_STRATEGY_ID,
    PLAN_SCHEMA_VERSION,
    ContextBuilder,
    PlanBudget,
    PlanRejectedError,
    PlanningContractError,
    PlanValidator,
    ToolGateway,
    ToolResult,
    parse_plan_proposal,
)


def build_context(*, tool_calls=2, retrieval_rounds=2):
    return ContextBuilder().build(
        task_summary="Find GIS Agent papers",
        research_question="Find papers where an Agent performs GIS spatial analysis.",
        inclusion_constraints=("Agent performs GIS spatial analysis",),
        exclusion_constraints=("remote-sensing-only image understanding",),
        hard_constraints=("at most two arXiv searches",),
        budget_remaining=PlanBudget(tool_calls=tool_calls, retrieval_rounds=retrieval_rounds),
        input_refs=("case-sha256-abc",),
    )


def plan_payload(context, *, queries=("all:agent AND all:GIS",), tool_id="search_arxiv"):
    constraint_refs = [item.constraint_id for item in context.constraints]
    actions = [
        {
            "action_id": f"search-{index}",
            "action_type": "tool_call",
            "tool_id": tool_id,
            "arguments": {"query": query, "max_results": 15},
            "expected_evidence": ["arXiv metadata and abstract candidates"],
            "constraint_refs": constraint_refs,
        }
        for index, query in enumerate(queries, start=1)
    ]
    actions.append(
        {
            "action_id": "finish-1",
            "action_type": "finish",
            "tool_id": None,
            "arguments": {},
            "expected_evidence": [],
            "constraint_refs": [],
        }
    )
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "strategy_id": BOUNDED_STRATEGY_ID,
        "strategy_version": "bounded-arxiv-prompt-v1",
        "context_sha256": context.content_sha256,
        "objective": "Find directly relevant GIS Agent candidates without relaxing exclusions.",
        "actions": actions,
        "stop_reason": "Stop after the bounded searches and expose unverifiable constraints.",
    }


def violation_codes(result):
    return {item.code for item in result.violations}


def test_context_builder_is_deterministic_and_binds_budget_and_inputs():
    first = build_context()
    second = build_context()
    changed = build_context(tool_calls=1)

    assert first == second
    assert first.content_sha256 == second.content_sha256
    assert first.content_sha256 != changed.content_sha256
    assert [item.constraint_id for item in first.constraints] == [
        "include-01",
        "exclude-01",
        "hard-01",
    ]
    assert first.allowed_tools == ("search_arxiv",)


def test_context_rejects_invalid_search_limit_and_empty_input_refs():
    with pytest.raises(PlanningContractError) as limit:
        ContextBuilder().build(
            task_summary="task",
            research_question="question",
            budget_remaining=PlanBudget(tool_calls=2, retrieval_rounds=2),
            input_refs=("case",),
            max_search_actions=3,
        )
    assert limit.value.code == "context_search_limit_invalid"

    with pytest.raises(PlanningContractError) as refs:
        ContextBuilder().build(
            task_summary="task",
            research_question="question",
            budget_remaining=PlanBudget(),
            input_refs=(),
        )
    assert refs.value.code == "string_tuple_invalid"

    with pytest.raises(PlanningContractError) as text:
        ContextBuilder().build(
            task_summary=None,
            research_question="question",
            budget_remaining=PlanBudget(),
            input_refs=("case",),
        )
    assert text.value.code == "text_invalid"


def test_plan_parser_is_strict_and_canonical():
    context = build_context()
    payload = plan_payload(context)
    parsed_from_mapping = parse_plan_proposal(payload)
    parsed_from_json = parse_plan_proposal(json.dumps(payload, ensure_ascii=False))

    assert parsed_from_mapping == parsed_from_json
    assert parsed_from_mapping.context_sha256 == context.content_sha256
    assert parsed_from_mapping.proposal_sha256 == parsed_from_json.proposal_sha256

    payload["unexpected"] = True
    with pytest.raises(PlanningContractError) as captured:
        parse_plan_proposal(payload)
    assert captured.value.code == "schema_keys_invalid"


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        (lambda payload: payload["actions"][0]["arguments"].update(extra=True), "schema_keys_invalid"),
        (lambda payload: payload["actions"][0].update(action_type="loop"), "action_type_invalid"),
        (lambda payload: payload["actions"][0]["arguments"].update(max_results=True), "max_results_invalid"),
        (lambda payload: payload["actions"][-1].update(tool_id="search_arxiv"), "finish_shape_invalid"),
    ],
)
def test_plan_parser_rejects_malformed_action_shapes(mutation, error_code):
    payload = plan_payload(build_context())
    mutation(payload)

    with pytest.raises(PlanningContractError) as captured:
        parse_plan_proposal(payload)

    assert captured.value.code == error_code


def test_plan_parser_rejects_more_than_two_search_slots_and_oversized_json():
    context = build_context()
    payload = plan_payload(context, queries=("query one", "query two", "query three"))

    with pytest.raises(PlanningContractError) as actions:
        parse_plan_proposal(payload)
    assert actions.value.code == "plan_actions_invalid"

    with pytest.raises(PlanningContractError) as size:
        parse_plan_proposal("{" + " " * 16_384 + "}")
    assert size.value.code == "plan_too_large"


def test_validator_accepts_two_bounded_searches_and_records_reservation():
    context = build_context()
    proposal = parse_plan_proposal(
        plan_payload(context, queries=("all:agent AND all:GIS", "all:agent AND all:geospatial"))
    )

    result = PlanValidator().validate(context, proposal)
    validated = result.require_validated()

    assert result.accepted is True
    assert validated.required_budget == PlanBudget(tool_calls=2, retrieval_rounds=2)
    assert validated.search_action_ids == ("search-1", "search-2")
    assert len(validated.validation_sha256) == 64


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda payload: payload.update(strategy_id="unbounded-agent-v1"), "strategy_not_allowed"),
        (lambda payload: payload.update(context_sha256="0" * 64), "context_hash_mismatch"),
        (lambda payload: payload["actions"][0].update(tool_id="shell"), "tool_not_allowed"),
        (lambda payload: payload["actions"][0].update(constraint_refs=[]), "constraint_not_acknowledged"),
        (lambda payload: payload["actions"][0].update(constraint_refs=["unknown-01"]), "constraint_ref_unknown"),
        (lambda payload: payload["actions"].pop(), "finish_count_invalid"),
        (lambda payload: payload["actions"].reverse(), "finish_not_last"),
        (lambda payload: payload["actions"][0]["arguments"].update(max_results=16), "max_results_invalid"),
    ],
)
def test_validator_rejects_policy_schema_and_boundary_violations(mutate, expected_code):
    context = build_context()
    payload = plan_payload(context)
    mutate(payload)
    proposal = parse_plan_proposal(payload)

    result = PlanValidator().validate(context, proposal)

    assert result.accepted is False
    assert result.validated_plan is None
    assert expected_code in violation_codes(result)


def test_validator_rejects_duplicate_and_over_budget_searches():
    context = build_context(tool_calls=1, retrieval_rounds=1)
    duplicate = parse_plan_proposal(
        plan_payload(context, queries=("all:agent AND all:GIS", "  ALL:AGENT   and ALL:gis  "))
    )

    result = PlanValidator().validate(context, duplicate)

    assert {"search_query_duplicate", "plan_budget_exceeded"} <= violation_codes(result)


def test_tool_gateway_dispatches_only_an_action_from_an_accepted_plan():
    context = build_context()
    proposal = parse_plan_proposal(plan_payload(context))
    calls = []

    def search(action_id, arguments):
        calls.append((action_id, arguments.query, arguments.max_results))
        return ToolResult("search_arxiv", action_id, ("artifact-sha256-result",))

    result = ToolGateway({"search_arxiv": search}).dispatch(context, proposal, "search-1")

    assert result.output_artifact_refs == ("artifact-sha256-result",)
    assert calls == [("search-1", "all:agent AND all:GIS", 15)]


def test_tool_gateway_rejected_plan_starts_zero_tool_calls():
    context = build_context()
    proposal = parse_plan_proposal(plan_payload(context, tool_id="shell"))
    calls = 0

    def search(action_id, arguments):
        nonlocal calls
        calls += 1
        return ToolResult("search_arxiv", action_id, ())

    gateway = ToolGateway({"search_arxiv": search})
    with pytest.raises(PlanRejectedError) as captured:
        gateway.dispatch(context, proposal, "search-1")

    assert captured.value.code == "plan_rejected"
    assert calls == 0


def test_tool_gateway_cannot_dispatch_finish_or_unregistered_actions():
    context = build_context()
    proposal = parse_plan_proposal(plan_payload(context))
    gateway = ToolGateway(
        {
            "search_arxiv": lambda action_id, arguments: ToolResult(
                "search_arxiv", action_id, ()
            )
        }
    )

    with pytest.raises(PlanningContractError) as finish:
        gateway.dispatch(context, proposal, "finish-1")
    assert finish.value.code == "gateway_action_not_executable"

    with pytest.raises(PlanningContractError) as missing:
        gateway.dispatch(context, proposal, "missing")
    assert missing.value.code == "gateway_action_missing"


def test_gateway_registry_is_exactly_the_frozen_whitelist():
    with pytest.raises(PlanningContractError) as captured:
        ToolGateway({"search_arxiv": lambda action_id, arguments: None, "shell": lambda a, b: None})
    assert captured.value.code == "gateway_registry_invalid"


def test_gateway_rejects_a_handler_result_not_bound_to_the_action():
    context = build_context()
    proposal = parse_plan_proposal(plan_payload(context))
    gateway = ToolGateway(
        {"search_arxiv": lambda action_id, arguments: ToolResult("search_arxiv", "other", ())}
    )

    with pytest.raises(PlanningContractError) as captured:
        gateway.dispatch(context, proposal, "search-1")
    assert captured.value.code == "gateway_result_mismatch"
