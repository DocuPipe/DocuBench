"""run a single document through Anthropic Claude with the source document and schema.

Writes the benchmark result envelope:

    python3 scripts/run_claude.py <document_path> <schemas/doc_id.json> <output.json>

Set ANTHROPIC_API_KEY. Override ANTHROPIC_MODEL to change the model; the default is
claude-sonnet-4-6.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import openpyxl

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_gpt import (
    IMAGE_EXTENSIONS,
    PRIMITIVE_TYPES,
    TIFF_EXTENSIONS,
    ExtractionFailure,
    content_type,
    guidelines_meta,
    load_env_file,
    load_guidelines,
    load_json,
    load_prompt_template,
    tiff_png_parts,
    validate_value,
    write_json,
)


ANTHROPIC_API_BASE = os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1")
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "20000"))
SCHEMA_MODE = "anthropic_output_config_required_nonnullable_v1"
TEXT_EXTENSIONS = {".txt", ".csv", ".xml", ".html", ".htm", ".md", ".json", ".tsv", ".yaml", ".yml"}
CLAUDE_DROP_SCHEMA_KEYS = {"$schema", "examples", "default", "title", "name"}


def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return key


def anthropic_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)


def headers() -> dict[str, str]:
    return {
        "x-api-key": api_key(),
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
    }


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: list[str] = []
    for paragraph in root.findall(".//w:p", ns):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        if text.strip():
            lines.append(text)
    return "\n".join(lines)


def xlsx_text(path: Path) -> str:
    """Render an xlsx the way a human sees it: resolved labels + formatted dates, tab-separated per sheet.

    Uses openpyxl (data_only) so shared/inline strings resolve and formula cells return their cached
    computed values with real dates. A raw-XML reader drops inline-string label columns and leaves dates
    as raw Excel serials, feeding the model a headerless number grid.
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"# sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if value is None else str(value) for value in row]
            if any(cell.strip() for cell in cells):
                lines.append("\t".join(cells))
    wb.close()
    return "\n".join(lines)


def extracted_text_for_file(path: Path) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return read_text(path), {"input_mode": "text"}
    if suffix == ".docx":
        return docx_text(path), {"input_mode": "docx_text"}
    if suffix == ".xlsx":
        return xlsx_text(path), {"input_mode": "xlsx_text"}
    raise ExtractionFailure("unsupported_input", f"Claude runner cannot send {suffix} as native input")


def claude_document_parts(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64(path.read_bytes()),
                },
            }
        ], {"input_mode": "pdf_document"}

    if suffix in IMAGE_EXTENSIONS:
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": content_type(path),
                    "data": b64(path.read_bytes()),
                },
            }
        ], {"input_mode": "image"}

    if suffix in TIFF_EXTENSIONS:
        openai_parts, meta = tiff_png_parts(path)
        parts: list[dict[str, Any]] = []
        page = 0
        for item in openai_parts:
            if item["type"] == "input_text":
                page += 1
                parts.append({"type": "text", "text": f"TIFF page {page}, converted to PNG:"})
            else:
                encoded = item["image_url"].split(",", 1)[1]
                parts.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": encoded},
                })
        return parts, meta

    text, meta = extracted_text_for_file(path)
    return [{"type": "text", "text": f"Document text extracted from {path.name}:\n\n{text}"}], meta


def primitive_type(spec: dict[str, Any]) -> str:
    type_value = spec.get("type", "object" if "properties" in spec else "string")
    type_list = [type_value] if isinstance(type_value, str) else list(type_value or [])
    return next((item for item in type_list if item in PRIMITIVE_TYPES), "string")


def schema_metadata(spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in spec.items():
        if key in CLAUDE_DROP_SCHEMA_KEYS or key.startswith("x_"):
            continue
        if key in {"properties", "items", "required", "additionalProperties", "format", "type"}:
            continue
        if key == "enum" and isinstance(value, list):
            enum_values = [item for item in value if item is not None]
            if enum_values:
                out[key] = enum_values
            continue
        out[key] = value
    return out


def claude_compatible_schema_node(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {"type": "string"}

    out = schema_metadata(spec)
    type_value = spec.get("type", "object" if "properties" in spec else "string")
    type_list = [type_value] if isinstance(type_value, str) else list(type_value or [])

    if "object" in type_list:
        properties = spec.get("properties") or {}
        normalized_props = {name: claude_compatible_schema_node(child) for name, child in properties.items()}
        out["type"] = "object"
        out["properties"] = normalized_props
        out["required"] = list(normalized_props.keys())
        out["additionalProperties"] = False
        return out

    if "array" in type_list:
        out["type"] = "array"
        out["items"] = claude_compatible_schema_node(spec.get("items", {"type": "string"}))
        return out

    out["type"] = primitive_type(spec)
    return out


def normalize_claude_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return claude_compatible_schema_node(schema)


def nullable_type(type_value: str | list[str], nullable: bool) -> str | list[str]:
    types = [type_value] if isinstance(type_value, str) else list(type_value)
    if nullable and "null" not in types:
        types.append("null")
    return types[0] if len(types) == 1 else types


def claude_validation_schema_node(spec: Any, *, nullable: bool = True) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {"type": nullable_type("string", nullable)}

    out = schema_metadata(spec)
    type_value = spec.get("type", "object" if "properties" in spec else "string")
    type_list = [type_value] if isinstance(type_value, str) else list(type_value or [])

    if "object" in type_list:
        properties = spec.get("properties") or {}
        normalized_props = {name: claude_validation_schema_node(child, nullable=True) for name, child in properties.items()}
        out["type"] = nullable_type("object", nullable)
        out["properties"] = normalized_props
        out["required"] = list(normalized_props.keys())
        out["additionalProperties"] = False
        return out

    if "array" in type_list:
        out["type"] = nullable_type("array", nullable)
        out["items"] = claude_validation_schema_node(spec.get("items", {"type": "string"}), nullable=True)
        return out

    out["type"] = nullable_type(primitive_type(spec), nullable)
    enum_values = spec.get("enum")
    if isinstance(enum_values, list):
        out["enum"] = list(enum_values)
        if nullable and None not in out["enum"]:
            out["enum"].append(None)
    return out


def normalize_claude_validation_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return claude_validation_schema_node(schema, nullable=False)


def build_prompt(doc_id: str, guidelines: str | None = None) -> str:
    # canonical prompt (prompts/extraction_prompt.txt) plus one Claude-specific sentence,
    # since Claude returns conversational text unless told to emit only the JSON object.
    prompt = (
        load_prompt_template().format(doc_id=doc_id)
        + " Return only the JSON object, with no prose or markdown."
    )
    if guidelines is None:
        guidelines = load_guidelines(doc_id)
    if guidelines:
        prompt += f"\n\nAdditional schema instructions:\n{guidelines}"
    return prompt


def create_message(
    doc_id: str,
    document_parts: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    guidelines: str | None = None) -> dict[str, Any]:
    payload = {
        "model": anthropic_model(),
        "max_tokens": DEFAULT_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    *document_parts,
                    {"type": "text", "text": build_prompt(doc_id=doc_id, guidelines=guidelines)},
                ],
            }
        ],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": schema,
            }
        },
    }
    resp = requests.post(f"{ANTHROPIC_API_BASE}/messages", headers=headers(), json=payload, timeout=900)
    if resp.status_code >= 300:
        try:
            body = resp.json()
        except ValueError:
            body = {"error": {"message": resp.text[:800]}}
        err = body.get("error") if isinstance(body, dict) else None
        message = err.get("message") if isinstance(err, dict) else str(body)[:800]
        raise ExtractionFailure("api_error", f"message creation failed {resp.status_code}: {message}", {"http_status": resp.status_code})
    return resp.json()


def extract_output_text(message: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in message.get("content") or []:
        if item.get("type") == "text" and item.get("text") is not None:
            chunks.append(item["text"])
        elif item.get("type") == "json" and "json" in item:
            return json.dumps(item["json"], ensure_ascii=False)
    return "".join(chunks).strip()


def normalize_json_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
    return text


def repair_hebrew_abbreviation_quotes(text: str) -> str:
    return re.sub(r"(?<!\\)(?<=[\u0590-\u05ff])\"(?=[\u0590-\u05ff])", lambda match: '\\"', text)


def parse_json_text(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        repaired = repair_hebrew_abbreviation_quotes(text)
        if repaired == text:
            raise exc
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            raise exc


def parse_response_data(message: dict[str, Any], output_schema: dict[str, Any]) -> dict[str, Any]:
    if message.get("stop_reason") in {"max_tokens", "model_context_window_exceeded"}:
        raise ExtractionFailure("incomplete_response", f"stop_reason was {message.get('stop_reason')}")
    text = extract_output_text(message)
    if not text:
        raise ExtractionFailure("empty_response", "response contained no output text")
    text = normalize_json_text(text)
    try:
        data = parse_json_text(text)
    except json.JSONDecodeError as exc:
        raise ExtractionFailure("invalid_json", f"response was not valid JSON: {exc}") from exc
    data = fill_missing_nullable_fields(data, output_schema)
    errors = validate_value(data, output_schema)
    if errors:
        raise ExtractionFailure("schema_mismatch", "; ".join(errors[:20]), {"validation_errors": errors[:200]})
    return data


def fill_missing_nullable_fields(value: Any, schema: dict[str, Any]) -> Any:
    type_value = schema.get("type")
    type_list = [type_value] if isinstance(type_value, str) else list(type_value or [])
    expected = next((item for item in type_list if item != "null"), "string")

    if expected == "object" and isinstance(value, dict):
        out = dict(value)
        for name, child_schema in (schema.get("properties") or {}).items():
            child_type = child_schema.get("type")
            child_types = [child_type] if isinstance(child_type, str) else list(child_type or [])
            if name in out:
                out[name] = fill_missing_nullable_fields(out[name], child_schema)
            elif "null" in child_types:
                out[name] = None
        return out

    if expected == "array" and isinstance(value, list):
        item_schema = schema.get("items") or {}
        return [fill_missing_nullable_fields(item, item_schema) for item in value]

    return value


def estimate_cost(usage: dict[str, Any]) -> float | None:
    try:
        input_rate = float(os.environ["ANTHROPIC_INPUT_USD_PER_1M"])
        output_rate = float(os.environ["ANTHROPIC_OUTPUT_USD_PER_1M"])
    except (KeyError, ValueError):
        return None
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def failure_result(
    kind: str,
    message: str,
    *,
    started_at: float,
    doc_id: str,
    response: dict[str, Any] | None = None,
    extra_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    usage = (response or {}).get("usage") or {}
    meta = {
        "provider": "anthropic",
        "model": anthropic_model(),
        "doc_id": doc_id,
        "response_id": (response or {}).get("id"),
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
    validation_schema = normalize_claude_validation_schema(json_schema)
    output_schema = normalize_claude_output_schema(json_schema)
    guidelines = load_guidelines(doc_id)
    try:
        document_parts, input_meta = claude_document_parts(file_path)
        response = create_message(doc_id=doc_id, document_parts=document_parts, schema=output_schema, guidelines=guidelines)
        data = parse_response_data(response, validation_schema)
    except ExtractionFailure as exc:
        return failure_result(
            exc.kind,
            exc.message,
            started_at=started_at,
            doc_id=doc_id,
            response=response,
            extra_meta={**input_meta, **guidelines_meta(guidelines), **exc.meta})
    except requests.RequestException as exc:
        return failure_result(
            "request_error",
            str(exc),
            started_at=started_at,
            doc_id=doc_id,
            response=response,
            extra_meta={**input_meta, **guidelines_meta(guidelines)})
    except Exception as exc:
        return failure_result(
            exc.__class__.__name__,
            str(exc),
            started_at=started_at,
            doc_id=doc_id,
            response=response,
            extra_meta={**input_meta, **guidelines_meta(guidelines)})

    usage = response.get("usage") or {}
    return {
        "status": "ok",
        "cost": estimate_cost(usage),
        "time_sec": time.time() - started_at,
        "data": data,
        "meta": {
            "provider": "anthropic",
            "model": anthropic_model(),
            "doc_id": doc_id,
            "response_id": response.get("id"),
            "usage": usage,
            "schema_mode": SCHEMA_MODE,
            **input_meta,
            **guidelines_meta(guidelines),
        },
    }


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python3 scripts/run_claude.py <document_path> <schemas/doc_id.json> <output.json>")
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
