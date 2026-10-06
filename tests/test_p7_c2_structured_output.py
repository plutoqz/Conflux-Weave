"""Tests for structured JSON decoding, syntax repair, and schema validation."""

import pytest

from conflux_weave.structured_output import (
    StructuredOutputDecodeError,
    extract_json_candidate,
    format_schema_repair_prompt,
    repair_json_syntax,
    repair_truncated_json,
    safe_json_loads,
    strip_code_fences,
    strip_thinking_tags,
    validate_and_repair_schema,
)


def test_strip_thinking_tags():
    # Test closed thinking tag
    text = "<think>Let me analyze the problem first...</think>{\"answer\": 42}"
    assert strip_thinking_tags(text) == '{"answer": 42}'

    # Test reasoning tag
    text2 = "<reasoning>Step 1... Step 2...</reasoning>\n```json\n{\"ok\": true}\n```"
    assert '{"ok": true}' in strip_thinking_tags(text2)

    # Test unclosed start tag
    text3 = "<think>Analyzing...\n{\"queries\": [\"q1\"]}"
    assert strip_thinking_tags(text3) == '{"queries": ["q1"]}'


def test_strip_code_fences():
    text = "Here is the result:\n```json\n{\"result\": \"success\"}\n```\nHope it helps!"
    assert strip_code_fences(text) == '{"result": "success"}'

    text_no_lang = "```\n[1, 2, 3]\n```"
    assert strip_code_fences(text_no_lang) == "[1, 2, 3]"

    text_raw = "{\"direct\": true}"
    assert strip_code_fences(text_raw) == '{"direct": true}'


def test_repair_json_syntax():
    # Trailing commas in objects and arrays
    raw = '{"name": "test", "items": [1, 2, 3, ], "meta": {"a": 1, }, }'
    repaired = repair_json_syntax(raw)
    assert ',"items"' in repaired or ' "items"' in repaired
    data = safe_json_loads(raw)
    assert data["items"] == [1, 2, 3]
    assert data["meta"]["a"] == 1

    # Python literals
    python_raw = '{"active": True, "archived": False, "note": None}'
    data = safe_json_loads(python_raw)
    assert data["active"] is True
    assert data["archived"] is False
    assert data["note"] is None


def test_repair_truncated_json():
    # Truncated array
    truncated_arr = '{"items": ["apple", "banana", "ora'
    repaired = repair_truncated_json(truncated_arr)
    data = safe_json_loads(repaired)
    assert "items" in data
    assert len(data["items"]) == 3

    # Truncated nested object
    truncated_obj = '{"user": {"profile": {"age": 30'
    repaired_obj = repair_truncated_json(truncated_obj)
    data_obj = safe_json_loads(repaired_obj)
    assert data_obj["user"]["profile"]["age"] == 30


def test_safe_json_loads_complex_llm_responses():
    # Mixed: Thinking tag + conversational prefix + markdown fence + trailing comma
    llm_output = """<think>
User wants a list of research topics.
Let me generate 3 high quality topics.
</think>
Certainly! Here is the JSON you requested:

```json
{
  "topics": [
    "Quantum Computing",
    "Synthetic Biology",
    "Neural Architectures",
  ],
  "count": 3,
}
```
Let me know if you need any adjustments!"""

    data = safe_json_loads(llm_output)
    assert data["count"] == 3
    assert len(data["topics"]) == 3
    assert data["topics"][0] == "Quantum Computing"


def test_safe_json_loads_fallback():
    assert safe_json_loads("", default={"empty": True}) == {"empty": True}
    with pytest.raises(StructuredOutputDecodeError):
        safe_json_loads("Not a JSON at all and no brackets")


def test_validate_and_repair_schema():
    schema = {
        "type": "object",
        "required": ["title", "count", "tags"],
        "properties": {
            "title": {"type": "string"},
            "count": {"type": "integer"},
            "tags": {"type": "array"},
            "optional_desc": {"type": "string", "default": "N/A"},
        },
    }

    # Input with missing required field, string integer, and extra field
    data = {
        "title": "My Research",
        "count": "42",  # String instead of int
        # "tags" is missing
        "extra_field": "ignore me",
    }

    repaired, warnings = validate_and_repair_schema(data, schema, fill_defaults=True)
    assert repaired["title"] == "My Research"
    assert repaired["count"] == 42  # Coerced to int
    assert repaired["tags"] == []  # Fallback generated
    assert repaired["optional_desc"] == "N/A"  # Schema default
    assert len(warnings) >= 2


def test_format_schema_repair_prompt():
    prompt = format_schema_repair_prompt(
        raw_output="{\"bad\": 1}",
        error_message="Missing required field 'name'",
        expected_schema={"type": "object", "required": ["name"]},
    )
    assert "Your previous output failed strict JSON schema validation" in prompt
    assert "Missing required field 'name'" in prompt
    assert "Expected JSON Schema" in prompt
