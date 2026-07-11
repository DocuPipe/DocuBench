"""run a single document through the Extend (extend.ai) extraction API.

transforms a JSON Schema into Extend's accepted subset (recursively — these schemas are
nested arrays-of-objects), uploads the source file with a fresh file_id per run (the first
/extract on a file_id caches the parse output — the parse-cache trap), creates an extractor,
runs /extract, polls, and maps the output back into the original field shape.

set EXTEND_API_KEY for your own Extend workspace.

    python scripts/run_extend.py <document_path> <schemas/doc_id.json> <output.json>
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_gpt import guidelines_meta, load_guidelines

EXTEND_API_BASE = "https://api.extend.ai"
EXTEND_API_VERSION = "2026-02-09"
EXTEND_PER_CREDIT_USD = 0.0125
EXTEND_BASE_PROCESSOR = "extraction_performance"
EXTEND_BASE_VERSION = "4.1.1"
EXTEND_PARSE_ENGINE = "parse_performance"
POLL_INTERVAL_SEC = 3
POLL_TIMEOUT_SEC = 600

PRIMITIVE_TYPES = {"string", "number", "integer", "boolean"}
RESERVED_PROPERTY_NAMES = {"id"}
RENAME_SUFFIX = "__renamed"


# --- schema transform: our json schema -> extend's accepted subset ---

def transform_node(spec: dict) -> dict:
    """recursively transform one schema node into Extend's subset:
      - strip x_* keys and $schema; primitives -> nullable union; enums include null
      - format:"date" -> extend:type:"date"; objects recurse + additionalProperties:false; arrays recurse into items
    """
    # extend's schema subset rejects standard json-schema keywords like "examples"/"default"/"title"
    drop = {"$schema", "examples", "default", "title"}
    out: dict[str, Any] = {k: v for k, v in spec.items() if not k.startswith("x_") and k not in drop}
    if out.pop("format", None) == "date":
        out["extend:type"] = "date"
    type_val = out.get("type")
    type_list = [type_val] if isinstance(type_val, str) else list(type_val or [])

    if "object" in type_list:
        out["type"] = "object"
        if "properties" in out:
            out["properties"] = {n: transform_node(p) for n, p in out["properties"].items()}
        out["additionalProperties"] = False
        return out
    if "array" in type_list:
        out["type"] = "array"
        if "items" in out:
            out["items"] = transform_node(out["items"])
        return out
    if isinstance(type_val, str) and type_val in PRIMITIVE_TYPES:
        out["type"] = [type_val, "null"]
    elif isinstance(type_val, list) and "null" not in type_val:
        out["type"] = type_val + ["null"]
    if "enum" in out and None not in out["enum"]:
        out["enum"] = list(out["enum"]) + [None]
    return out


def transform_schema(json_schema: dict):
    """transform a full JSON schema to an Extend-compatible payload.
    returns (extend_schema, field_renames) mapping extend-side name -> original (for reserved keys).
    """
    base = {k: v for k, v in json_schema.items() if not k.startswith("x_") and k != "$schema"}
    field_renames: dict[str, str] = {}
    if base.get("type") == "object" and "properties" in base:
        new_props: dict = {}
        for name, spec in base["properties"].items():
            target = f"{name}{RENAME_SUFFIX}" if name in RESERVED_PROPERTY_NAMES else name
            if target != name:
                field_renames[target] = name
            new_props[target] = transform_node(spec)
        base["properties"] = new_props
    base["additionalProperties"] = False
    return base, field_renames


# --- api ---

def headers() -> dict:
    key = os.environ.get("EXTEND_API_KEY")
    if not key:
        raise RuntimeError("EXTEND_API_KEY not set")
    return {"Authorization": f"Bearer {key}", "x-extend-api-version": EXTEND_API_VERSION}


CONTENT_TYPES = {".pdf": "application/pdf", ".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".png": "image/png",
                 ".webp": "image/webp", ".tiff": "image/tiff", ".tif": "image/tiff", ".txt": "text/plain",
                 ".csv": "text/csv", ".xml": "text/xml", ".html": "text/html",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 ".doc": "application/msword",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xls": "application/vnd.ms-excel"}


def upload_file(file_path: Path, h: dict) -> str:
    ctype = CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    with open(file_path, "rb") as f:
        resp = requests.post(f"{EXTEND_API_BASE}/files/upload", headers=h, files={"file": (file_path.name, f, ctype)}, timeout=180)
    if resp.status_code >= 300:
        raise RuntimeError(f"/files/upload failed {resp.status_code}: {resp.text[:400]}")
    return resp.json()["id"]


def create_extractor(name: str, schema: dict, h: dict, guidelines: Optional[str] = None) -> str:
    cfg = {
        "baseProcessor": EXTEND_BASE_PROCESSOR,
        "baseVersion": EXTEND_BASE_VERSION,
        "schema": schema,
        "parseConfig": {"engine": EXTEND_PARSE_ENGINE}}
    if guidelines is not None and guidelines.strip():
        cfg["extractionRules"] = guidelines.strip()
    resp = requests.post(
        f"{EXTEND_API_BASE}/extractors",
        headers={**h, "Content-Type": "application/json"},
        json={"name": name, "config": cfg},
        timeout=60)
    if resp.status_code >= 300:
        raise RuntimeError(f"/extractors failed {resp.status_code}: {resp.text[:600]}")
    return resp.json()["id"]


def run_extract(file_id: str, extractor_id: str, h: dict) -> Optional[dict]:
    resp = requests.post(f"{EXTEND_API_BASE}/extract", headers={**h, "Content-Type": "application/json"},
                         json={"extractor": {"id": extractor_id}, "file": {"id": file_id}}, timeout=120)
    if resp.status_code >= 300:
        print(f"    POST /extract failed {resp.status_code}: {resp.text[:300]}")
        return None
    initial = resp.json()
    run_id = initial.get("id") or initial.get("extractRun", {}).get("id")
    if not run_id:
        print(f"    /extract returned no run id: {json.dumps(initial)[:300]}")
        return None
    start = time.time()
    while True:
        if time.time() - start > POLL_TIMEOUT_SEC:
            print(f"    extract run {run_id} timed out")
            return None
        time.sleep(POLL_INTERVAL_SEC)
        r = requests.get(f"{EXTEND_API_BASE}/extract_runs/{run_id}", headers=h, timeout=30)
        if r.status_code >= 300:
            print(f"    GET /extract_runs/{run_id} failed {r.status_code}")
            return None
        run = r.json().get("extractRun") or r.json()
        status = run.get("status")
        if status == "PROCESSED":
            return run
        if status == "FAILED":
            print(f"    extract run {run_id} FAILED: {run.get('failureReason')} {run.get('failureMessage')}")
            return None


def run(doc_id: str, file_path: Path, json_schema: dict) -> Optional[dict]:
    """full per-doc flow: transform schema, upload file (fresh file_id), create extractor, extract, map back.
    returns {"data", "cost", "time_sec", "meta"} or None.
    """
    h = headers()
    extend_schema, field_renames = transform_schema(json_schema)
    guidelines = load_guidelines(doc_id)
    try:
        file_id = upload_file(file_path, h)  # fresh file_id per run (parse-cache trap)
        # unique name per run: extend rejects duplicate processor names, and a fresh extractor guarantees the current schema
        extractor_id = create_extractor(
            name=f"public__{doc_id}__{int(time.time() * 1000)}",
            schema=extend_schema,
            h=h,
            guidelines=guidelines)
    except Exception as e:
        print(f"    extend setup FAILED on {doc_id}: {e}")
        return None
    rec = run_extract(file_id=file_id, extractor_id=extractor_id, h=h)
    if rec is None:
        return None
    data = (rec.get("output") or {}).get("value", {}) or {}
    if field_renames and isinstance(data, dict):
        data = {field_renames.get(k, k): v for k, v in data.items()}
    usage = rec.get("usage") or {}
    credits = usage.get("totalCredits") or usage.get("credits") or 0
    cost = credits * EXTEND_PER_CREDIT_USD
    try:
        t0 = datetime.fromisoformat((rec.get("createdAt") or "").replace("Z", "+00:00"))
        t1 = datetime.fromisoformat((rec.get("updatedAt") or "").replace("Z", "+00:00"))
        tsec = max(0.0, (t1 - t0).total_seconds())
    except (ValueError, TypeError):
        tsec = 0.0
    return {
        "data": data,
        "cost": cost,
        "time_sec": tsec,
        "meta": {
            "file_id": file_id,
            "extractor_id": extractor_id,
            "run_id": rec.get("id"),
            "credits": credits,
            **guidelines_meta(guidelines),
        }}


def main():
    if len(sys.argv) != 4:
        print("usage: python scripts/run_extend.py <document_path> <schemas/doc_id.json> <output.json>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    schema_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    json_schema = json.load(open(schema_path, encoding="utf-8"))
    doc_id = schema_path.stem
    result = run(doc_id=doc_id, file_path=file_path, json_schema=json_schema)
    if result is None:
        print("extraction failed")
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
