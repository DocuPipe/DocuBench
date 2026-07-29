"""run a single document through the Unstructured (unstructured.io) extraction API.

Unstructured is an ETL/RAG pipeline rather than a single extraction endpoint, so the committed result
set comes from a two-node on-demand job: a Partitioner reads the file, then a structured_data_extractor
node applies our JSON Schema to the partitioned elements. No source/destination connectors are needed --
the file is posted inline as multipart to the Transform API's /jobs/ endpoint.

Two settings define the committed run (results/unstructured/):
  - partition: Auto routing (is_dynamic=true), VLM model claude-opus-4-6
  - extract:   claude-opus-4-6 via bedrock, output_mode extracted_data_only

Unstructured's Extract node is bring-your-own-model: provider and model are required settings, so the
score depends on the model chosen. We used the strongest model they host on each node. Note Opus 4.6 is
addressed as `anthropic` on the partitioner but only as `bedrock` on the extractor -- same model, and
both run on Unstructured's own keys (no AWS credentials required from the caller).

The schema is converted to the OpenAI Structured Outputs subset they require: every property listed in
`required`, additionalProperties false on every object, optionality expressed as a nullable type union,
and the whole schema passed as a JSON *string*.

Set UNSTRUCTURED_API_KEY for your own Unstructured Transform account (their free tier covers this
benchmark many times over). The API URL is shown next to the key in the Transform dashboard; override
with UNSTRUCTURED_API_URL if yours differs.

    python scripts/run_unstructured.py <document_path> <schemas/doc_id.json> <output.json>
"""
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_gpt import guidelines_meta, load_guidelines

# on-demand local-file jobs live on Unstructured Transform. the Pipelines host
# (platform.unstructuredapp.io) rejects a Transform api key with 401.
UNSTRUCTURED_API_BASE = os.environ.get("UNSTRUCTURED_API_URL", "https://platform-api.transform.unstructured.io/api/v1")
UNSTRUCTURED_PER_PAGE_USD = 0.03
SUBMIT_TIMEOUT_SEC = 300
POLL_INTERVAL_SEC = 10
# dense multi-page scans are genuinely slow here (a 10p dictionary took ~1100s)
POLL_TIMEOUT_SEC = int(os.environ.get("UNSTRUCTURED_POLL_TIMEOUT_SEC", "7200"))
# the platform rejects local-file jobs launched less than a second apart
SUBMIT_SPACING_SEC = 1.5
MAX_FILE_MB = 50
# consecutive 5xx / connection errors tolerated while polling before a job is called dead
MAX_TRANSIENT_POLL_ERRORS = 30

DROP_SCHEMA_KEYS = {"$schema", "examples", "default", "title", "format"}
PRIMITIVE_TYPES = {"string", "number", "integer", "boolean"}

# NOTE: is_dynamic=true is Auto routing. to force the VLM strategy instead it must be set to false --
# Unstructured's own Extract quickstart passes true with subtype "vlm", which silently falls back to Auto.
AUTO_PARTITION = {"name": "Partitioner", "type": "partition", "subtype": "vlm",
                  "settings": {"is_dynamic": True, "allow_fast": True, "provider": "anthropic", "model": "claude-opus-4-6"}}
EXTRACT_CONFIG = {"provider": "bedrock", "model": "us.anthropic.claude-opus-4-6-v1"}

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".xml": "text/xml",
    ".html": "text/html",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel"}


# --- schema transform: our json schema -> openai structured outputs subset ---

def transform_node(spec: Any) -> Any:
    """recursively rewrite one schema node into the structured-outputs subset.

    objects get additionalProperties:false plus every property listed in `required`; optionality is
    expressed as a nullable type union instead, since structured outputs forbids optional keys.
    """
    if not isinstance(spec, dict):
        return spec
    out: dict[str, Any] = {k: transform_node(v) for k, v in spec.items() if not k.startswith("x_") and k not in DROP_SCHEMA_KEYS}
    type_val = out.get("type")
    type_list = [type_val] if isinstance(type_val, str) else list(type_val or [])

    if "object" in type_list:
        out["type"] = "object"
        props = out.get("properties")
        if isinstance(props, dict):
            out["properties"] = {n: transform_node(p) for n, p in props.items()}
            out["required"] = list(props.keys())
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
    if "enum" in out and isinstance(out["enum"], list) and None not in out["enum"]:
        out["enum"] = list(out["enum"]) + [None]
    return out


# --- api ---

def headers() -> dict[str, str]:
    key = os.environ.get("UNSTRUCTURED_API_KEY")
    if not key:
        raise RuntimeError("UNSTRUCTURED_API_KEY not set")
    return {"unstructured-api-key": key, "accept": "application/json"}


def content_type_for(file_path: Path) -> str:
    explicit = CONTENT_TYPES.get(file_path.suffix.lower())
    if explicit:
        return explicit
    guessed, _ = mimetypes.guess_type(file_path.name)
    return guessed or "application/octet-stream"


def build_job_nodes(schema: dict, guidelines: str) -> list[dict]:
    """two-node DAG: partition the file, then extract our schema from the partitioned elements.
    """
    schema_to_extract: dict[str, Any] = {"json_schema": json.dumps(schema, ensure_ascii=False)}
    if guidelines:
        schema_to_extract["extraction_guidance"] = guidelines
    extractor = {
        "name": "Extractor",
        "type": "structured_data_extractor",
        "subtype": "llm",
        "settings": {"schema_to_extract": schema_to_extract, "output_mode": "extracted_data_only", **EXTRACT_CONFIG}}
    return [AUTO_PARTITION, extractor]


def create_job(file_path: Path, job_nodes: list[dict], h: dict) -> Optional[str]:
    """POST /jobs/ with the file inline as multipart. returns the job id or None.
    """
    request_data = json.dumps({"job_nodes": job_nodes}, ensure_ascii=False)
    with open(file_path, "rb") as f:
        files = {"input_files": (file_path.name, f, content_type_for(file_path))}
        resp = requests.post(f"{UNSTRUCTURED_API_BASE}/jobs/", headers=h, data={"request_data": request_data},
                             files=files, timeout=SUBMIT_TIMEOUT_SEC)
    if resp.status_code >= 300:
        print(f"    POST /jobs/ failed {resp.status_code}: {resp.text[:500]}")
        return None
    body = resp.json()
    job_id = body.get("id") or (body.get("job_information") or {}).get("id")
    if not job_id:
        print(f"    /jobs/ returned no job id: {json.dumps(body)[:300]}")
        return None
    return job_id


def poll_job(job_id: str, h: dict) -> Optional[dict]:
    """GET /jobs/{id} until COMPLETED. returns the job payload (carrying output_node_files) or None.
    """
    start = time.time()
    transient = 0
    while True:
        if time.time() - start > POLL_TIMEOUT_SEC:
            print(f"    job {job_id} timed out after {POLL_TIMEOUT_SEC}s")
            return None
        time.sleep(POLL_INTERVAL_SEC)
        # a 5xx or dropped connection is their infrastructure blipping, not a failed extraction
        try:
            r = requests.get(f"{UNSTRUCTURED_API_BASE}/jobs/{job_id}", headers=h, timeout=60)
        except requests.RequestException as e:
            transient += 1
            if transient > MAX_TRANSIENT_POLL_ERRORS:
                print(f"    GET /jobs/{job_id} unreachable {transient}x, giving up: {e}")
                return None
            continue
        if r.status_code >= 500:
            transient += 1
            if transient > MAX_TRANSIENT_POLL_ERRORS:
                print(f"    GET /jobs/{job_id} returned {r.status_code} {transient}x, giving up")
                return None
            continue
        if r.status_code >= 300:
            print(f"    GET /jobs/{job_id} failed {r.status_code}: {r.text[:200]}")
            return None
        transient = 0
        body = r.json()
        status = str(body.get("status", "")).upper()
        if status == "COMPLETED":
            return body
        if status in ("FAILED", "STOPPED", "CANCELLED", "CANCELED"):
            print(f"    job {job_id} {status}")
            return None


def download_output(job_id: str, file_id: str, h: dict, node_id: Optional[str] = None) -> Optional[Any]:
    params = {"file_id": file_id}
    if node_id:
        params["node_id"] = node_id
    r = requests.get(f"{UNSTRUCTURED_API_BASE}/jobs/{job_id}/download", headers=h, params=params, timeout=180)
    if r.status_code >= 300:
        print(f"    download {file_id} failed {r.status_code}: {r.text[:200]}")
        return None
    try:
        return r.json()
    except ValueError:
        print(f"    download {file_id} returned non-json: {r.text[:200]}")
        return None


def extract_data(payload: Any) -> Any:
    """pull the extracted object out of an extracted_data_only download.
    """
    if isinstance(payload, list):
        if len(payload) == 1:
            return extract_data(payload[0])
        for entry in payload:
            if isinstance(entry, dict) and (entry.get("type") == "DocumentData" or "extracted_data" in (entry.get("metadata") or {})):
                return extract_data(entry)
        return payload
    if isinstance(payload, dict):
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and "extracted_data" in metadata:
            return metadata["extracted_data"]
        if "extracted_data" in payload:
            return payload["extracted_data"]
    return payload


def job_page_count(job: dict) -> Optional[int]:
    """best-effort page count for the cost basis; the api does not always report one.
    """
    for key in ("total_pages", "pages_processed", "page_count"):
        value = job.get(key)
        if isinstance(value, int):
            return value
    return None


def run(doc_id: str, file_path: Path, json_schema: dict) -> Optional[dict]:
    """full per-doc flow: transform schema, submit a one-file job, poll, download, map back.
    """
    h = headers()
    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        print(f"    {doc_id}: file is {size_mb:.1f}MB, over the {MAX_FILE_MB}MB local-file cap")
        return None
    guidelines = load_guidelines(doc_id)
    job_nodes = build_job_nodes(schema=transform_node(json_schema), guidelines=guidelines)
    time.sleep(SUBMIT_SPACING_SEC)
    t0 = time.time()
    job_id = create_job(file_path=file_path, job_nodes=job_nodes, h=h)
    if job_id is None:
        return None
    job = poll_job(job_id=job_id, h=h)
    tsec = time.time() - t0
    if job is None:
        return None

    # both nodes emit an output entry under the SAME file_id, distinguished only by node_id
    entries = [f for f in (job.get("output_node_files") or []) if isinstance(f, dict) and f.get("file_id")]
    extractor = next((f for f in entries if f.get("node_type") == "structured_data_extractor"), entries[-1] if entries else None)
    if extractor is None:
        print(f"    {doc_id}: job {job_id} completed with no output files")
        return None
    payload = download_output(job_id=job_id, file_id=str(extractor["file_id"]), h=h, node_id=extractor.get("node_id"))
    if payload is None:
        return None
    data = extract_data(payload)
    # a non-dict payload means we got raw partition elements back, i.e. the extractor produced nothing
    if not isinstance(data, dict):
        print(f"    {doc_id}: extractor returned no data, got partition elements")
        return None
    pages = job_page_count(job)
    return {
        "data": data,
        "cost": pages * UNSTRUCTURED_PER_PAGE_USD if pages is not None else 0.0,
        "time_sec": tsec,
        "meta": {
            "model": EXTRACT_CONFIG["model"],
            "partition": AUTO_PARTITION["settings"],
            "job_id": job_id,
            "pages": pages,
            **guidelines_meta(guidelines),
        }}


def main():
    if len(sys.argv) != 4:
        print("usage: python scripts/run_unstructured.py <document_path> <schemas/doc_id.json> <output.json>")
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
