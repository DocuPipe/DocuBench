"""run a single document through Anthropic Claude with the source document and schema.

Writes the benchmark result envelope:

    python3 scripts/run_claude.py <document_path> <schemas/doc_id.json> <output.json>

Set ANTHROPIC_API_KEY. Override ANTHROPIC_MODEL to change the model; the default is
claude-sonnet-4-6.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_gpt import (
    IMAGE_EXTENSIONS,
    TIFF_EXTENSIONS,
    ExtractionFailure,
    content_type,
    load_env_file,
    load_json,
    load_prompt_template,
    normalize_output_schema,
    tiff_png_parts,
    validate_value,
    write_json,
)


ANTHROPIC_API_BASE = os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1")
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "20000"))
SCHEMA_MODE = "anthropic_output_config_nullable_v1"
TEXT_EXTENSIONS = {".txt", ".csv", ".xml", ".html", ".htm", ".md", ".json", ".tsv", ".yaml", ".yml"}


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
    with zipfile.ZipFile(path) as zf:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ElementTree.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root:
                shared_strings.append("".join(node.text or "" for node in si.iter() if node.tag.endswith("}t")))

        lines: list[str] = []
        for name in sorted(n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
            root = ElementTree.fromstring(zf.read(name))
            lines.append(f"# {name}")
            for row in root.iter():
                if not row.tag.endswith("}row"):
                    continue
                values: list[str] = []
                for cell in row:
                    if not cell.tag.endswith("}c"):
                        continue
                    cell_type = cell.attrib.get("t")
                    value_node = next((child for child in cell if child.tag.endswith("}v")), None)
                    if value_node is None or value_node.text is None:
                        values.append("")
                    elif cell_type == "s":
                        idx = int(value_node.text)
                        values.append(shared_strings[idx] if idx < len(shared_strings) else value_node.text)
                    else:
                        values.append(value_node.text)
                if any(v.strip() for v in values):
                    lines.append("\t".join(values))
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


def build_prompt(doc_id: str, schema: dict[str, Any] | None = None) -> str:
    # canonical prompt (prompts/extraction_prompt.txt) plus one Claude-specific sentence,
    # since Claude returns conversational text unless told to emit only the JSON object.
    prompt = load_prompt_template().format(doc_id=doc_id) + " Return only the JSON object, with no prose or markdown."
    if schema is not None:
        prompt += "\n\nJSON schema:\n" + json.dumps(schema, ensure_ascii=False)
    return prompt


def create_message(doc_id: str, document_parts: list[dict[str, Any]], schema: dict[str, Any], *, strict_output: bool = True) -> dict[str, Any]:
    payload = {
        "model": anthropic_model(),
        "max_tokens": DEFAULT_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    *document_parts,
                    {"type": "text", "text": build_prompt(doc_id, None if strict_output else schema)},
                ],
            }
        ],
    }
    if strict_output:
        payload["output_config"] = {
            "format": {
                "type": "json_schema",
                "schema": schema,
            }
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


def should_retry_with_prompt_schema(exc: ExtractionFailure) -> bool:
    if exc.kind != "api_error":
        return False
    message = exc.message.lower()
    return any(
        marker in message
        for marker in [
            "compiled grammar",
            "output_config",
            "invalid schema",
            "schema contains",
            "schemas contains",
            "union",
        ]
    )


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


def parse_response_data(message: dict[str, Any], output_schema: dict[str, Any]) -> dict[str, Any]:
    if message.get("stop_reason") in {"max_tokens", "model_context_window_exceeded"}:
        raise ExtractionFailure("incomplete_response", f"stop_reason was {message.get('stop_reason')}")
    text = extract_output_text(message)
    if not text:
        raise ExtractionFailure("empty_response", "response contained no output text")
    text = normalize_json_text(text)
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
        input_rate = float(os.environ["ANTHROPIC_INPUT_USD_PER_1M"])
        output_rate = float(os.environ["ANTHROPIC_OUTPUT_USD_PER_1M"])
    except (KeyError, ValueError):
        return None
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def failure_result(kind: str, message: str, *, started_at: float, doc_id: str, response: dict[str, Any] | None = None, extra_meta: dict[str, Any] | None = None) -> dict[str, Any]:
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
    output_schema = normalize_output_schema(json_schema)
    try:
        document_parts, input_meta = claude_document_parts(file_path)
        try:
            response = create_message(doc_id, document_parts, output_schema)
        except ExtractionFailure as exc:
            if not should_retry_with_prompt_schema(exc):
                raise
            input_meta["schema_fallback"] = "prompt_json_schema"
            response = create_message(doc_id, document_parts, output_schema, strict_output=False)
        data = parse_response_data(response, output_schema)
    except ExtractionFailure as exc:
        return failure_result(exc.kind, exc.message, started_at=started_at, doc_id=doc_id, response=response, extra_meta={**input_meta, **exc.meta})
    except requests.RequestException as exc:
        return failure_result("request_error", str(exc), started_at=started_at, doc_id=doc_id, response=response, extra_meta=input_meta)
    except Exception as exc:
        return failure_result(exc.__class__.__name__, str(exc), started_at=started_at, doc_id=doc_id, response=response, extra_meta=input_meta)

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
