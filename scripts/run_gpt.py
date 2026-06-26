"""run a single document through OpenAI GPT with the source file and JSON schema.

Uploads the document, sends it to the Responses API with Structured Outputs, and writes
the same result envelope used by the benchmark:

    python scripts/run_gpt.py <document_path> <schemas/doc_id.json> <output.json>

Set OPENAI_API_KEY. Override OPENAI_MODEL to change the model; the default is gpt-5.5.

LLM/API/schema failures are intentionally written as result files with status="failed"
and data={}, so score_all.py counts every labeled field as an error instead of treating
the run as missing.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import requests


OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_TIMEOUT_SEC = 600
SCHEMA_MODE = "openai_strict_nullable_v1"

DROP_SCHEMA_KEYS = {"$schema", "examples", "default", "title"}
PRIMITIVE_TYPES = {"string", "number", "integer", "boolean"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
TIFF_EXTENSIONS = {".tif", ".tiff"}


class ExtractionFailure(Exception):
    """A per-document extraction failure that should be tracked as data={}."""

    def __init__(self, kind: str, message: str, meta: dict[str, Any] | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.meta = meta or {}


def api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return key


def openai_model() -> str:
    return os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].strip()
            name, value = stripped.split("=", 1)
            name = name.strip()
            value = value.strip().strip("'\"")
            if name and name not in os.environ:
                os.environ[name] = value


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key()}"}


def load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def nullable_type(type_value: str | list[str], nullable: bool) -> str | list[str]:
    types = [type_value] if isinstance(type_value, str) else list(type_value)
    if nullable and "null" not in types:
        types.append("null")
    return types[0] if len(types) == 1 else types


def sanitize_schema_literal(value: Any) -> Any:
    """Remove characters OpenAI strict schema rejects from metadata/enum literals."""
    if isinstance(value, str):
        return value.replace('"', "")
    if isinstance(value, list):
        return [sanitize_schema_literal(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_schema_literal(item) for key, item in value.items()}
    return value


def normalize_schema_node(spec: Any, *, nullable: bool = True) -> dict[str, Any]:
    """Convert the benchmark schema to the strict JSON schema subset OpenAI accepts.

    Structured Outputs requires every object property to be listed in "required" and
    rejects unknown object fields. Since benchmark labels use null for many fields even
    when the source schema says "string"/"number", this normalizer preserves field shape
    while making object properties nullable.
    """
    if not isinstance(spec, dict):
        return {"type": nullable_type("string", nullable)}

    out: dict[str, Any] = {}
    for key, value in spec.items():
        if key in DROP_SCHEMA_KEYS or key.startswith("x_"):
            continue
        if key == "format":
            continue
        if key in {"properties", "items", "required", "additionalProperties"}:
            continue
        out[key] = sanitize_schema_literal(value)

    type_value = spec.get("type", "object" if "properties" in spec else "string")
    type_list = [type_value] if isinstance(type_value, str) else list(type_value or [])

    if "object" in type_list:
        properties = spec.get("properties") or {}
        normalized_props = {
            name: normalize_schema_node(child, nullable=True)
            for name, child in properties.items()
        }
        out["type"] = nullable_type("object", nullable)
        out["properties"] = normalized_props
        out["required"] = list(normalized_props.keys())
        out["additionalProperties"] = False
        return out

    if "array" in type_list:
        out["type"] = nullable_type("array", nullable)
        out["items"] = normalize_schema_node(spec.get("items", {"type": "string"}), nullable=True)
        return out

    primitive = next((t for t in type_list if t in PRIMITIVE_TYPES), "string")
    out["type"] = nullable_type(primitive, nullable)
    if "enum" in out and nullable and None not in out["enum"]:
        out["enum"] = list(out["enum"]) + [None]
    return out


def normalize_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return normalize_schema_node(schema, nullable=False)


def schema_name(doc_id: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"docubench_{doc_id}")
    return name[:64] or "docubench_schema"


def check_response(resp: requests.Response, action: str) -> dict[str, Any]:
    if resp.status_code < 300:
        return resp.json()
    try:
        body = resp.json()
    except ValueError:
        body = {"error": {"message": resp.text[:800]}}
    err = body.get("error") if isinstance(body, dict) else None
    message = err.get("message") if isinstance(err, dict) else str(body)[:800]
    raise ExtractionFailure("api_error", f"{action} failed {resp.status_code}: {message}", {"http_status": resp.status_code})


def content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def upload_file(path: Path) -> str:
    with open(path, "rb") as f:
        resp = requests.post(
            f"{OPENAI_API_BASE}/files",
            headers=headers(),
            data={"purpose": "user_data"},
            files={"file": (path.name, f, content_type(path))},
            timeout=180,
        )
    return check_response(resp, "file upload")["id"]


def image_part_from_bytes(data: bytes, media_type: str = "image/png") -> dict[str, Any]:
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{media_type};base64,{encoded}"}


def tiff_png_parts(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from PIL import Image, ImageSequence
    except ImportError as exc:
        raise ExtractionFailure("dependency_missing", "Pillow is required to convert TIFF inputs to PNG") from exc

    parts: list[dict[str, Any]] = []
    with Image.open(path) as image:
        for index, frame in enumerate(ImageSequence.Iterator(image), start=1):
            png = BytesIO()
            page = frame.copy()
            if page.mode not in {"RGB", "RGBA", "L"}:
                page = page.convert("RGB")
            page.save(png, format="PNG")
            parts.append({"type": "input_text", "text": f"TIFF page {index}, converted to PNG:"})
            parts.append(image_part_from_bytes(png.getvalue()))

    if not parts:
        raise ExtractionFailure("invalid_tiff", "TIFF contained no frames")
    return parts, {"input_mode": "tiff_png_sequence", "tiff_pages": len(parts) // 2}


def input_parts_for_file(path: Path) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    if path.suffix.lower() in TIFF_EXTENSIONS:
        parts, meta = tiff_png_parts(path)
        return parts, None, meta

    if path.suffix.lower() in IMAGE_EXTENSIONS:
        with open(path, "rb") as f:
            return [image_part_from_bytes(f.read(), content_type(path))], None, {"input_mode": "image"}

    file_id = upload_file(path)
    return [{"type": "input_file", "file_id": file_id}], file_id, {"input_mode": "file"}


PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extraction_prompt.txt"
DEFAULT_PROMPT_TEMPLATE = (
    "Extract the document into the supplied JSON schema. "
    "Use only information present in the document. "
    "Return null for fields that are not printed or cannot be determined. "
    "Preserve table rows as arrays and preserve the document language for values. "
    "Document id: {doc_id}."
)


def load_prompt_template() -> str:
    """Load the canonical extraction prompt committed in prompts/extraction_prompt.txt.

    The committed file is the single source of truth for the prompt that produced the
    baseline result sets; fall back to the inline default only if it is missing.
    """
    try:
        return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_PROMPT_TEMPLATE


def build_prompt(doc_id: str) -> str:
    return load_prompt_template().format(doc_id=doc_id)


def response_payload(doc_id: str, document_parts: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": openai_model(),
        "store": False,
        "input": [
            {
                "role": "user",
                "content": [
                    *document_parts,
                    {"type": "input_text", "text": build_prompt(doc_id)},
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name(doc_id),
                "strict": True,
                "schema": schema,
            }
        },
    }
    max_output_tokens = os.environ.get("OPENAI_MAX_OUTPUT_TOKENS")
    if max_output_tokens:
        payload["max_output_tokens"] = int(max_output_tokens)
    reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT")
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def create_response(payload: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(
        f"{OPENAI_API_BASE}/responses",
        headers={**headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=DEFAULT_TIMEOUT_SEC,
    )
    return check_response(resp, "response creation")


def extract_text(response: dict[str, Any]) -> str:
    if response.get("output_text"):
        return response["output_text"]

    chunks: list[str] = []
    for item in response.get("output") or []:
        for content in item.get("content") or []:
            ctype = content.get("type")
            if ctype in {"output_text", "text"} and content.get("text") is not None:
                chunks.append(content["text"])
            if ctype == "refusal":
                raise ExtractionFailure("refusal", content.get("refusal") or "model refused the request")
    return "".join(chunks).strip()


def first_type(schema: dict[str, Any]) -> str:
    type_value = schema.get("type")
    if isinstance(type_value, list):
        return next((t for t in type_value if t != "null"), "null")
    return type_value or "string"


def allows_null(schema: dict[str, Any]) -> bool:
    type_value = schema.get("type")
    return type_value == "null" or (isinstance(type_value, list) and "null" in type_value)


def validate_value(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    if value is None and allows_null(schema):
        return []

    expected = first_type(schema)
    errors: list[str] = []

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not in enum")

    if expected == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for name in required:
            if name not in value:
                errors.append(f"{path}.{name}: missing required field")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(f"{path}.{name}: unexpected field")
        for name, child_schema in properties.items():
            if name in value:
                errors.extend(validate_value(value[name], child_schema, f"{path}.{name}"))
        return errors

    if expected == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        item_schema = schema.get("items") or {}
        for index, item in enumerate(value):
            errors.extend(validate_value(item, item_schema, f"{path}[{index}]"))
        return errors

    if expected == "string" and not isinstance(value, str):
        errors.append(f"{path}: expected string")
    elif expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        errors.append(f"{path}: expected number")
    elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        errors.append(f"{path}: expected integer")
    elif expected == "boolean" and not isinstance(value, bool):
        errors.append(f"{path}: expected boolean")
    return errors


def parse_response_data(response: dict[str, Any], output_schema: dict[str, Any]) -> dict[str, Any]:
    status = response.get("status")
    if status and status != "completed":
        raise ExtractionFailure(
            "incomplete_response",
            f"response status was {status}",
            {"incomplete_details": response.get("incomplete_details")},
        )

    text = extract_text(response)
    if not text:
        raise ExtractionFailure("empty_response", "response contained no output text")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionFailure("invalid_json", f"response was not valid JSON: {exc}") from exc

    errors = validate_value(data, output_schema)
    if errors:
        raise ExtractionFailure("schema_mismatch", "; ".join(errors[:20]), {"validation_errors": errors[:200]})
    return data


def estimate_cost(usage: dict[str, Any]) -> float | None:
    """Optional cost estimate using env-provided per-million token prices."""
    try:
        input_rate = float(os.environ["OPENAI_INPUT_USD_PER_1M"])
        output_rate = float(os.environ["OPENAI_OUTPUT_USD_PER_1M"])
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
    file_id: str | None = None,
    response: dict[str, Any] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    usage = (response or {}).get("usage") or {}
    meta = {
        "provider": "openai",
        "model": openai_model(),
        "doc_id": doc_id,
        "file_id": file_id,
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
    file_id: str | None = None
    input_meta: dict[str, Any] = {}
    response: dict[str, Any] | None = None
    output_schema = normalize_output_schema(json_schema)

    try:
        document_parts, file_id, input_meta = input_parts_for_file(file_path)
        response = create_response(response_payload(doc_id, document_parts, output_schema))
        data = parse_response_data(response, output_schema)
    except ExtractionFailure as exc:
        return failure_result(
            exc.kind,
            exc.message,
            started_at=started_at,
            doc_id=doc_id,
            file_id=file_id,
            response=response,
            extra_meta={**input_meta, **exc.meta},
        )
    except requests.RequestException as exc:
        return failure_result(
            "request_error",
            str(exc),
            started_at=started_at,
            doc_id=doc_id,
            file_id=file_id,
            response=response,
            extra_meta=input_meta,
        )
    except Exception as exc:
        return failure_result(
            exc.__class__.__name__,
            str(exc),
            started_at=started_at,
            doc_id=doc_id,
            file_id=file_id,
            response=response,
            extra_meta=input_meta,
        )

    usage = response.get("usage") or {}
    return {
        "status": "ok",
        "cost": estimate_cost(usage),
        "time_sec": time.time() - started_at,
        "data": data,
        "meta": {
            "provider": "openai",
            "model": openai_model(),
            "doc_id": doc_id,
            "file_id": file_id,
            "response_id": response.get("id"),
            "usage": usage,
            "schema_mode": SCHEMA_MODE,
            **input_meta,
        },
    }


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python3 scripts/run_gpt.py <document_path> <schemas/doc_id.json> <output.json>")
        return 1

    file_path = Path(sys.argv[1])
    schema_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    repo_root = Path(__file__).resolve().parent.parent
    load_env_file(repo_root / ".env")
    load_env_file(Path.cwd() / ".env")

    json_schema = load_json(schema_path)
    doc_id = schema_path.stem
    result = run(doc_id=doc_id, file_path=file_path, json_schema=json_schema)
    write_json(output_path, result)
    if result.get("status") == "ok":
        print(f"wrote {output_path}")
    else:
        error = result.get("error") or {}
        print(f"wrote failed result {output_path}: {error.get('type')}: {error.get('message')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
