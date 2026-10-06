"""Structured JSON output decoding, sanitization, and fault-tolerant repair engine.

Provides robust extraction of structured JSON payloads from heterogeneous LLM
responses (handling thinking tags, markdown fences, trailing commas, truncated
brackets, Python literals, and schema drift).
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence


class StructuredOutputDecodeError(json.JSONDecodeError, ValueError):
    """Raised when JSON extraction or syntax repair fails to produce valid JSON."""

    def __init__(self, message: str, raw_text: str | None = None, pos: int = 0) -> None:
        doc = raw_text if raw_text is not None else ""
        super().__init__(message, doc, pos)
        self.raw_text = raw_text


# Regex patterns for reasoning/thinking blocks commonly output by thinking models
THINKING_TAG_PATTERN = re.compile(
    r"<(think|thought|reasoning)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
UNCLOSED_THINKING_TAG_PATTERN = re.compile(
    r"^.*?</(think|thought|reasoning)>\s*",
    re.DOTALL | re.IGNORECASE,
)
OPENING_THINK_TAG_PREFIX = re.compile(
    r"^<(think|thought|reasoning)>\s*",
    re.IGNORECASE,
)

# Markdown code fences: ```json ... ``` or ``` ... ```
MARKDOWN_FENCE_PATTERN = re.compile(
    r"```(?:json|JSON)?\s*\n?(.*?)\n?```",
    re.DOTALL,
)


def strip_thinking_tags(text: str) -> str:
    """Strip reasoning/thinking tags (e.g. <think>...</think>) from text."""
    if not text:
        return ""
    cleaned = THINKING_TAG_PATTERN.sub("", text)
    # If there is a lone unclosed closing tag at the start
    cleaned = UNCLOSED_THINKING_TAG_PATTERN.sub("", cleaned)
    # If the text begins with <think> but was truncated before </think>
    if OPENING_THINK_TAG_PREFIX.match(cleaned):
        # Look for the first JSON start symbol
        first_bracket = min(
            (pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos != -1),
            default=-1,
        )
        if first_bracket != -1:
            cleaned = cleaned[first_bracket:]
    return cleaned.strip()


def strip_code_fences(text: str) -> str:
    """Extract contents from markdown code fences if present, or return text."""
    if not text:
        return ""
    matches = MARKDOWN_FENCE_PATTERN.findall(text)
    if matches:
        # Return the longest fenced code block (most likely the primary payload)
        return max(matches, key=len).strip()
    return text.strip()


def find_outermost_json_boundaries(text: str) -> tuple[int, int] | None:
    """Find the start and end indices of the outermost balanced JSON object/array.
    
    Correctly ignores brackets inside string literals and escaped characters.
    Returns (start_idx, end_idx) inclusive, or None if no balanced JSON structure exists.
    """
    first_char_idx = -1
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            first_char_idx = i
            break
    if first_char_idx == -1:
        return None

    expected_closing = "}" if text[first_char_idx] == "{" else "]"
    opening_char = text[first_char_idx]

    stack = [opening_char]
    in_string = False
    escape = False

    for i in range(first_char_idx + 1, len(text)):
        ch = text[i]

        if escape:
            escape = False
            continue

        if ch == "\\":
            if in_string:
                escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch in ("{", "["):
            stack.append(ch)
        elif ch in ("}", "]"):
            if not stack:
                continue
            top = stack[-1]
            if (top == "{" and ch == "}") or (top == "[" and ch == "]"):
                stack.pop()
                if not stack:
                    return (first_char_idx, i)
            else:
                # Mismatched bracket
                pass

    return None


def repair_truncated_json(text: str) -> str:
    """Attempt to repair truncated JSON by closing open strings, arrays, and objects."""
    first_char_idx = min(
        (pos for pos in (text.find("{"), text.find("[")) if pos != -1),
        default=-1,
    )
    if first_char_idx == -1:
        return text

    snippet = text[first_char_idx:]
    stack: list[str] = []
    in_string = False
    escape = False

    for ch in snippet:
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch in ("{", "["):
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()

    repaired = snippet
    # If left open inside a string, close the string
    if in_string:
        repaired += '"'

    # Close remaining open brackets in reverse order
    while stack:
        top = stack.pop()
        if top == "{":
            repaired += "}"
        elif top == "[":
            repaired += "]"

    return repaired


def repair_json_syntax(text: str) -> str:
    """Fix common minor JSON syntax errors like trailing commas and Python literals."""
    # 1. Replace trailing commas before closing braces/brackets
    # e.g., [1, 2, ] -> [1, 2] and {"a": 1, } -> {"a": 1}
    repaired = re.sub(r",\s*([\]}])", r"\1", text)

    # 2. Replace Python boolean/null literals with JSON equivalents when outside strings
    # We do a safe replacement of standalone literals
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)
    repaired = re.sub(r"\bNone\b", "null", repaired)

    # 3. Handle single-quoted object keys: e.g. {'name': ...} -> {"name": ...}
    repaired = re.sub(r"([{,]\s*)'([a-zA-Z0-9_\-]+)'\s*:", r'\1"\2":', repaired)

    return repaired


def extract_json_candidate(text: str) -> str:
    """High-level multi-stage extraction of JSON substring from freeform text."""
    if not text:
        return ""
    # Stage 1: Strip thinking tags
    candidate = strip_thinking_tags(text)
    # Stage 2: Check for markdown code fences
    fenced = strip_code_fences(candidate)
    if fenced != candidate:
        candidate = fenced
    # Stage 3: Look for balanced JSON boundaries
    boundaries = find_outermost_json_boundaries(candidate)
    if boundaries:
        start_idx, end_idx = boundaries
        return candidate[start_idx : end_idx + 1]

    # If no balanced boundaries, slice from the first bracket
    first_char_idx = min(
        (pos for pos in (candidate.find("{"), candidate.find("[")) if pos != -1),
        default=-1,
    )
    if first_char_idx != -1:
        return candidate[first_char_idx:]

    return candidate.strip()


def safe_json_loads(text: str, *, default: Any = None) -> Any:
    """Safely decode structured JSON from arbitrary text using multi-tier repair.

    Fallback stages:
    1. Direct json.loads;
    2. Extract JSON candidate substring, then json.loads;
    3. Syntax normalization (trailing commas, Python literals), then json.loads;
    4. Truncation repair (auto-closing strings and brackets), then json.loads;
    5. Fallback to default if provided, otherwise raise StructuredOutputDecodeError.
    """
    if not text or not text.strip():
        if default is not None:
            return default
        raise StructuredOutputDecodeError("Input text is empty", raw_text=text)

    # Tier 1: Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # Tier 2: Extract candidate substring
    candidate = extract_json_candidate(text)
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # Tier 3: Syntax repair
    repaired_syntax = repair_json_syntax(candidate)
    try:
        return json.loads(repaired_syntax)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # Tier 4: Truncation repair on top of syntax repair
    repaired_truncated = repair_truncated_json(repaired_syntax)
    try:
        return json.loads(repaired_truncated)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        if default is not None:
            return default
        raise StructuredOutputDecodeError(
            f"Failed to decode or repair structured JSON: {exc}",
            raw_text=text,
        ) from exc


def validate_and_repair_schema(
    data: Any,
    schema: Mapping[str, Any],
    *,
    fill_defaults: bool = True,
    strict_extra_keys: bool = False,
) -> tuple[Any, list[str]]:
    """Validate data against a JSON schema and perform best-effort repair.

    Returns:
        (repaired_data, list_of_warning_messages)
    """
    warnings: list[str] = []
    schema_type = schema.get("type", "object")

    if schema_type == "object":
        if not isinstance(data, dict):
            if fill_defaults:
                warnings.append(f"Expected object, got {type(data).__name__}; initialized empty dict")
                data = {}
            else:
                raise ValueError(f"Expected object, got {type(data).__name__}")

        result: dict[str, Any] = dict(data)
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Check required fields
        for field in required:
            if field not in result or result[field] is None:
                prop_spec = properties.get(field, {})
                prop_type = prop_spec.get("type")
                if "default" in prop_spec:
                    result[field] = prop_spec["default"]
                    warnings.append(f"Missing required field '{field}'; filled with schema default")
                elif fill_defaults:
                    # Provide type-based default
                    default_val: Any = ""
                    if prop_type == "array":
                        default_val = []
                    elif prop_type == "object":
                        default_val = {}
                    elif prop_type in ("integer", "number"):
                        default_val = 0
                    elif prop_type == "boolean":
                        default_val = False
                    result[field] = default_val
                    warnings.append(f"Missing required field '{field}'; generated fallback default")
                else:
                    raise ValueError(f"Missing required field '{field}'")

        # Check optional properties with default values
        if fill_defaults:
            for prop_name, prop_spec in properties.items():
                if prop_name not in result and "default" in prop_spec:
                    result[prop_name] = prop_spec["default"]
                    warnings.append(f"Filled optional field '{prop_name}' with default value")

        # Type normalization for known properties
        for prop_name, prop_spec in properties.items():
            if prop_name in result and result[prop_name] is not None:
                expected_type = prop_spec.get("type")
                val = result[prop_name]
                if expected_type == "integer" and not isinstance(val, int):
                    try:
                        result[prop_name] = int(val)
                        warnings.append(f"Coerced '{prop_name}' from {type(val).__name__} to integer")
                    except (ValueError, TypeError):
                        pass
                elif expected_type == "number" and not isinstance(val, (int, float)):
                    try:
                        result[prop_name] = float(val)
                        warnings.append(f"Coerced '{prop_name}' from {type(val).__name__} to float")
                    except (ValueError, TypeError):
                        pass
                elif expected_type == "string" and not isinstance(val, str):
                    result[prop_name] = str(val)
                    warnings.append(f"Coerced '{prop_name}' from {type(val).__name__} to string")
                elif expected_type == "array" and not isinstance(val, list):
                    if isinstance(val, (tuple, set)):
                        result[prop_name] = list(val)
                    else:
                        result[prop_name] = [val]
                    warnings.append(f"Wrapped '{prop_name}' into list")

        # Remove extra keys if strict mode enabled
        if strict_extra_keys and properties:
            extra_keys = [k for k in result if k not in properties]
            for k in extra_keys:
                del result[k]
                warnings.append(f"Filtered undeclared property '{k}'")

        return result, warnings

    elif schema_type == "array":
        if not isinstance(data, list):
            if isinstance(data, (tuple, set)):
                data = list(data)
                warnings.append("Converted collection to list")
            elif fill_defaults:
                data = [data] if data is not None else []
                warnings.append("Wrapped element into list")
            else:
                raise ValueError(f"Expected array, got {type(data).__name__}")
        return data, warnings

    return data, warnings


def format_schema_repair_prompt(
    raw_output: str,
    error_message: str,
    expected_schema: Mapping[str, Any],
) -> str:
    """Format an explicit prompt for LLM-based schema re-extraction and correction."""
    schema_str = json.dumps(expected_schema, indent=2, ensure_ascii=False)
    truncated_raw = raw_output[:3000] if len(raw_output) > 3000 else raw_output
    return (
        "Your previous output failed strict JSON schema validation.\n"
        f"Validation Error: {error_message}\n\n"
        f"Expected JSON Schema:\n{schema_str}\n\n"
        f"Previous Output (to be corrected):\n{truncated_raw}\n\n"
        "Instructions:\n"
        "1. Output ONLY a valid JSON object matching the exact schema above.\n"
        "2. Do not include markdown code blocks, conversational comments, or explanations.\n"
        "3. Ensure all brackets, braces, and quotation marks are properly closed.\n"
    )
