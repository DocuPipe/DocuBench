"""run a single document through Claude on Amazon Bedrock with a tool schema.

Writes the benchmark result envelope:

    python3 scripts/run_claude_bedrock.py <document_path> <schemas/doc_id.json> <output.json>

Uses AWS credentials from the standard boto3 chain. Override BEDROCK_CLAUDE_MODEL_ID
to change the model; the default is global.anthropic.claude-sonnet-5.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, cast

from anthropic import AnthropicBedrock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_claude import (
    ExtractionFailure,
    build_prompt,
    claude_document_parts,
    fill_missing_nullable_fields,
    normalize_claude_output_schema,
    normalize_claude_validation_schema,
)
from run_gpt import guidelines_meta, load_env_file, load_guidelines, load_json, validate_value, write_json


DEFAULT_BEDROCK_MODEL_ID = "global.anthropic.claude-sonnet-5"
DEFAULT_BEDROCK_REGION = "us-east-1"
DEFAULT_MAX_TOKENS = int(os.environ.get("BEDROCK_CLAUDE_MAX_TOKENS", "20000"))
SCHEMA_MODE = "bedrock_tool_required_nonnullable_v1"
TOOL_NAME = "submit_extraction"
INPUT_USD_PER_1M = float(os.environ.get("BEDROCK_CLAUDE_INPUT_USD_PER_1M", "3.0"))
OUTPUT_USD_PER_1M = float(os.environ.get("BEDROCK_CLAUDE_OUTPUT_USD_PER_1M", "15.0"))


def bedrock_region() -> str:
    return os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or DEFAULT_BEDROCK_REGION


def bedrock_model_id() -> str:
    return os.environ.get("BEDROCK_CLAUDE_MODEL_ID", DEFAULT_BEDROCK_MODEL_ID)


def bedrock_client() -> AnthropicBedrock:
    return AnthropicBedrock(aws_region=bedrock_region())


def extraction_tool(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Submit the extracted document data matching the requested schema.",
        "input_schema": schema,
    }


def create_message(
    doc_id: str,
    document_parts: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    guidelines: str | None = None) -> Any:
    prompt = build_prompt(doc_id=doc_id, guidelines=guidelines) + f" Call the `{TOOL_NAME}` tool exactly once."
    try:
        kwargs: dict[str, Any] = {
            "model": bedrock_model_id(),
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        *document_parts,
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "tools": [extraction_tool(schema)],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        return bedrock_client().messages.create(**kwargs)
    except Exception as exc:
        raise ExtractionFailure("api_error", f"bedrock message creation failed: {exc}") from exc


def serialize_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "dict"):
        return response.dict()
    return {"repr": repr(response)}


def usage_dict(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if hasattr(usage, "dict"):
        return usage.dict()
    return {}


def response_id(response: Any) -> str | None:
    return getattr(response, "id", None)


def extract_tool_input(response: Any) -> dict[str, Any]:
    for item in getattr(response, "content", []) or []:
        item_type = getattr(item, "type", None)
        item_name = getattr(item, "name", None)
        if item_type == "tool_use" and item_name == TOOL_NAME:
            tool_input = getattr(item, "input", None)
            if isinstance(tool_input, dict):
                return cast(dict[str, Any], tool_input)
            raise ExtractionFailure("schema_mismatch", "tool input was not a JSON object")
    raise ExtractionFailure("empty_response", f"response did not contain a {TOOL_NAME} tool call")


def estimate_cost(usage: dict[str, Any]) -> float | None:
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    return (input_tokens * INPUT_USD_PER_1M + output_tokens * OUTPUT_USD_PER_1M) / 1_000_000


def failure_result(
    kind: str,
    message: str,
    *,
    started_at: float,
    doc_id: str,
    response: Any | None = None,
    extra_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    usage = usage_dict(response)
    meta = {
        "provider": "anthropic_bedrock",
        "model": "claude-sonnet-5",
        "bedrock_model_id": bedrock_model_id(),
        "bedrock_region": bedrock_region(),
        "doc_id": doc_id,
        "response_id": response_id(response),
        "usage": usage,
        "schema_mode": SCHEMA_MODE,
    }
    if extra_meta:
        meta.update(extra_meta)
    return {
        "status": "failed",
        "error": {"type": kind, "message": message},
        "cost": estimate_cost(usage),
        "time_sec": time.time() - started_at,
        "data": {},
        "meta": meta,
    }


def run(doc_id: str, file_path: Path, json_schema: dict[str, Any]) -> dict[str, Any]:
    started_at = time.time()
    response: Any | None = None
    input_meta: dict[str, Any] = {}
    output_schema = normalize_claude_output_schema(json_schema)
    validation_schema = normalize_claude_validation_schema(json_schema)
    guidelines = load_guidelines(doc_id)
    try:
        document_parts, input_meta = claude_document_parts(file_path)
        response = create_message(doc_id=doc_id, document_parts=document_parts, schema=output_schema, guidelines=guidelines)
        data = fill_missing_nullable_fields(extract_tool_input(response), validation_schema)
        errors = validate_value(data, validation_schema)
        if errors:
            raise ExtractionFailure("schema_mismatch", "; ".join(errors[:20]), {"validation_errors": errors[:200]})
    except ExtractionFailure as exc:
        return failure_result(
            exc.kind,
            exc.message,
            started_at=started_at,
            doc_id=doc_id,
            response=response,
            extra_meta={**input_meta, **guidelines_meta(guidelines), **exc.meta},
        )
    except Exception as exc:
        return failure_result(
            exc.__class__.__name__,
            str(exc),
            started_at=started_at,
            doc_id=doc_id,
            response=response,
            extra_meta={**input_meta, **guidelines_meta(guidelines)},
        )

    usage = usage_dict(response)
    return {
        "status": "ok",
        "cost": estimate_cost(usage),
        "time_sec": time.time() - started_at,
        "data": data,
        "meta": {
            "provider": "anthropic_bedrock",
            "model": "claude-sonnet-5",
            "bedrock_model_id": bedrock_model_id(),
            "bedrock_region": bedrock_region(),
            "doc_id": doc_id,
            "response_id": response_id(response),
            "usage": usage,
            "schema_mode": SCHEMA_MODE,
            **input_meta,
            **guidelines_meta(guidelines),
        },
    }


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python3 scripts/run_claude_bedrock.py <document_path> <schemas/doc_id.json> <output.json>")
        return 1
    file_path = Path(sys.argv[1])
    schema_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    repo_root = Path(__file__).resolve().parent.parent
    load_env_file(repo_root / ".env")
    load_env_file(Path.cwd() / ".env")

    result = run(doc_id=schema_path.stem, file_path=file_path, json_schema=load_json(schema_path))
    write_json(output_path, result)
    if result.get("status") == "ok":
        print(f"wrote {output_path}")
    else:
        error = result.get("error") or {}
        print(f"wrote failed result {output_path}: {error.get('type')}: {error.get('message')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
