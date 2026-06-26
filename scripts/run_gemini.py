"""run a single document through Gemini with the source document and schema.

Writes the benchmark result envelope:

    python3 scripts/run_gemini.py <document_path> <schemas/doc_id.json> <output.json>

Set GOOGLE_API_KEY. Override GEMINI_MODEL to change the model; the default is
gemini-3.5-flash.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_claude import TEXT_EXTENSIONS, docx_text, read_text, xlsx_text
from run_gpt import (
    IMAGE_EXTENSIONS,
    TIFF_EXTENSIONS,
    ExtractionFailure,
    build_prompt,
    content_type,
    load_env_file,
    load_json,
    normalize_output_schema,
    sanitize_schema_literal,
    tiff_png_parts,
    validate_value,
    write_json,
)


GEMINI_API_BASE = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
SCHEMA_MODE = "gemini_response_schema_nullable_v1"
GEMINI_TYPES = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
}


def api_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY or GEMINI_API_KEY not set")
    return key


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def first_type(schema: dict[str, Any]) -> tuple[str, bool]:
    value = schema.get("type", "string")
    if isinstance(value, list):
        nullable = "null" in value
        return next((item for item in value if item != "null"), "string"), nullable
    return value, False


def gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema_type, nullable = first_type(schema)
    out: dict[str, Any] = {"type": GEMINI_TYPES.get(schema_type, "STRING")}
    if nullable:
        out["nullable"] = True
    if "description" in schema:
        out["description"] = sanitize_schema_literal(schema["description"])
    if "enum" in schema:
        enum_values = [value for value in schema["enum"] if value is not None]
        if enum_values:
            out["enum"] = [sanitize_schema_literal(value) for value in enum_values]
    if schema_type == "object":
        properties = schema.get("properties") or {}
        out["properties"] = {name: gemini_schema(child) for name, child in properties.items()}
        if properties:
            out["required"] = list(properties.keys())
            out["propertyOrdering"] = list(properties.keys())
    elif schema_type == "array":
        out["items"] = gemini_schema(schema.get("items", {"type": "string"}))
    return out


def text_for_file(path: Path) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return read_text(path), {"input_mode": "text"}
    if suffix == ".docx":
        return docx_text(path), {"input_mode": "docx_text"}
    if suffix == ".xlsx":
        return xlsx_text(path), {"input_mode": "xlsx_text"}
    raise ExtractionFailure("unsupported_input", f"Gemini runner cannot send {suffix} as input")


def gemini_document_parts(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".pdf", *IMAGE_EXTENSIONS}:
        return [
            {
                "inline_data": {
                    "mime_type": content_type(path),
                    "data": b64(path.read_bytes()),
                }
            }
        ], {"input_mode": "inline_data"}

    if suffix in TIFF_EXTENSIONS:
        openai_parts, meta = tiff_png_parts(path)
        parts: list[dict[str, Any]] = []
        for item in openai_parts:
            if item["type"] == "input_text":
                parts.append({"text": item["text"]})
            else:
                encoded = item["image_url"].split(",", 1)[1]
                parts.append({"inline_data": {"mime_type": "image/png", "data": encoded}})
        return parts, meta

    text, meta = text_for_file(path)
    return [{"text": f"Document text extracted from {path.name}:\n\n{text}"}], meta


def create_response(doc_id: str, document_parts: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    *document_parts,
                    {"text": build_prompt(doc_id)},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema(schema),
        },
    }
    max_output_tokens = os.environ.get("GEMINI_MAX_OUTPUT_TOKENS")
    if max_output_tokens:
        payload["generationConfig"]["maxOutputTokens"] = int(max_output_tokens)
    url = f"{GEMINI_API_BASE}/models/{gemini_model()}:generateContent"
    resp = requests.post(url, params={"key": api_key()}, json=payload, timeout=900)
    if resp.status_code >= 300:
        try:
            body = resp.json()
        except ValueError:
            body = {"error": {"message": resp.text[:800]}}
        err = body.get("error") if isinstance(body, dict) else None
        message = err.get("message") if isinstance(err, dict) else str(body)[:800]
        raise ExtractionFailure("api_error", f"generateContent failed {resp.status_code}: {message}", {"http_status": resp.status_code})
    return resp.json()


def extract_output_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        feedback = response.get("promptFeedback") or {}
        raise ExtractionFailure("empty_response", f"response contained no candidates: {feedback}")
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    if finish_reason and finish_reason not in {"STOP", "FINISH_REASON_UNSPECIFIED"}:
        raise ExtractionFailure("incomplete_response", f"finishReason was {finish_reason}")
    parts = ((candidate.get("content") or {}).get("parts") or [])
    return "".join(part.get("text", "") for part in parts).strip()


def parse_response_data(response: dict[str, Any], output_schema: dict[str, Any]) -> dict[str, Any]:
    text = extract_output_text(response)
    if not text:
        raise ExtractionFailure("empty_response", "response contained no output text")
    text = re.sub(r"^```(?:json)?\\s*|\\s*```$", "", text.strip(), flags=re.IGNORECASE | re.DOTALL)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionFailure("invalid_json", f"response was not valid JSON: {exc}") from exc
    errors = validate_value(data, output_schema)
    if errors:
        raise ExtractionFailure("schema_mismatch", "; ".join(errors[:20]), {"validation_errors": errors[:200]})
    return data


def estimate_cost(usage: dict[str, Any]) -> float | None:
    try:
        input_rate = float(os.environ["GEMINI_INPUT_USD_PER_1M"])
        output_rate = float(os.environ["GEMINI_OUTPUT_USD_PER_1M"])
    except (KeyError, ValueError):
        return None
    input_tokens = usage.get("promptTokenCount", 0) or 0
    output_tokens = usage.get("candidatesTokenCount", 0) or 0
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def failure_result(kind: str, message: str, *, started_at: float, doc_id: str, response: dict[str, Any] | None = None, extra_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    usage = (response or {}).get("usageMetadata") or {}
    meta = {
        "provider": "google",
        "model": gemini_model(),
        "doc_id": doc_id,
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
    response: dict[str, Any] | None = None
    input_meta: dict[str, Any] = {}
    output_schema = normalize_output_schema(json_schema)
    try:
        document_parts, input_meta = gemini_document_parts(file_path)
        response = create_response(doc_id, document_parts, output_schema)
        data = parse_response_data(response, output_schema)
    except ExtractionFailure as exc:
        return failure_result(exc.kind, exc.message, started_at=started_at, doc_id=doc_id, response=response, extra_meta={**input_meta, **exc.meta})
    except requests.RequestException as exc:
        return failure_result("request_error", str(exc), started_at=started_at, doc_id=doc_id, response=response, extra_meta=input_meta)
    except Exception as exc:
        return failure_result(exc.__class__.__name__, str(exc), started_at=started_at, doc_id=doc_id, response=response, extra_meta=input_meta)

    usage = response.get("usageMetadata") or {}
    return {
        "status": "ok",
        "cost": estimate_cost(usage),
        "time_sec": time.time() - started_at,
        "data": data,
        "meta": {
            "provider": "google",
            "model": gemini_model(),
            "doc_id": doc_id,
            "usage": usage,
            "schema_mode": SCHEMA_MODE,
            **input_meta,
        },
    }


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python3 scripts/run_gemini.py <document_path> <schemas/doc_id.json> <output.json>")
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
