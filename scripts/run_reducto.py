"""run a single document through the Reducto (reducto.ai) extraction API.

uploads the source file (fresh per run), runs /extract_async with our JSON Schema passed via
instructions.schema, polls to completion, and maps the result back into the original field shape.
unlike Extend, Reducto accepts nested arrays-of-objects, so the schema handling is a light clean
(strip non-standard keys) rather than a structural rewrite.

two modes, matching the two committed result sets:
  - Deep Extract (agentic super_agent mode)  -> results/reducto/
  - standard extract                          -> results/reducto_standard/
the mode is inferred from the output directory name (…/reducto_standard/ -> standard), and can be
forced with REDUCTO_DEEP_EXTRACT=0/1.

Reducto rejects a couple of file shapes that it can still read once trivially re-encoded; the runner
applies the same transforms the benchmark used so the content is reachable:
  - xml/json files upload as .txt (the raw file type is rejected, the identical bytes as .txt extract)
  - oversized raw images are re-encoded to a smaller jpeg (full resolution, lower quality) under the size cap

set REDUCTO_API_KEY for your own Reducto workspace.

    python scripts/run_reducto.py <document_path> <schemas/doc_id.json> <output.json>
"""
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_gpt import guidelines_meta, load_guidelines

REDUCTO_API_BASE = "https://platform.reducto.ai"
REDUCTO_PER_CREDIT_USD = 0.015
SUBMIT_TIMEOUT_SEC = 120
POLL_INTERVAL_SEC = 5
# deep extract latency is highly variable on large docs (seen 9.5min vs >20min on the same 18p doc);
# override via REDUCTO_POLL_TIMEOUT_SEC for known-slow docs
POLL_TIMEOUT_SEC = int(os.environ.get("REDUCTO_POLL_TIMEOUT_SEC", "1200"))
DROP_SCHEMA_KEYS = {"$schema", "examples", "default", "title"}

SYSTEM_PROMPT = "Be precise and thorough. Extract exactly what appears in the document; do not infer or fabricate values."

# reducto's doc pipeline rejects raw structured-text files (.xml/.json) with "could not be processed",
# but extracts the identical bytes fine when uploaded as .txt. remap those so the content is reachable.
TEXT_REMAP_SUFFIXES = {".xml", ".json"}
# reducto also rejects oversized raw images (size-based cap); re-encoding to a smaller jpeg — keeping full
# resolution, dropping quality — gets under the cap and scored best (vs downscale or pdf-wrap)
IMAGE_SUFFIXES = {".jpeg", ".jpg", ".png", ".webp", ".tiff", ".tif", ".bmp"}
REDUCTO_MAX_IMAGE_MB = 1.5


def clean_node(spec: Any) -> Any:
    """recursively strip non-standard/leaky keys but keep structure, types, enums, descriptions.
    reducto accepts standard json schema incl. nested arrays-of-objects, so no structural rewrite.
    """
    if isinstance(spec, dict):
        return {k: clean_node(v) for k, v in spec.items() if not k.startswith("x_") and k not in DROP_SCHEMA_KEYS}
    if isinstance(spec, list):
        return [clean_node(v) for v in spec]
    return spec


def headers() -> dict[str, str]:
    key = os.environ.get("REDUCTO_API_KEY")
    if not key:
        raise RuntimeError("REDUCTO_API_KEY not set")
    return {"Authorization": f"Bearer {key}"}


def compress_image_bytes(raw: bytes) -> bytes:
    """re-encode an oversized image to jpeg under REDUCTO_MAX_IMAGE_MB, keeping resolution (drop quality first,
    downscale to 2000px only if quality alone can't get it small enough).
    """
    target = REDUCTO_MAX_IMAGE_MB * 1024 * 1024 * 0.7
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    for quality in (60, 45, 30, 20):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= target:
            return buf.getvalue()
    img.thumbnail((2000, 2000))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return buf.getvalue()


def prepare_upload(file_path: Path) -> tuple[bytes, str, Optional[dict], str]:
    """returns (bytes, upload_name, query_params, handling) applying reducto-specific file remaps."""
    suffix = file_path.suffix.lower()
    if suffix in TEXT_REMAP_SUFFIXES:
        return file_path.read_bytes(), f"{file_path.stem}.txt", {"extension": "txt"}, "text-remap"
    raw = file_path.read_bytes()
    if suffix in IMAGE_SUFFIXES and len(raw) > REDUCTO_MAX_IMAGE_MB * 1024 * 1024:
        return compress_image_bytes(raw), f"{file_path.stem}.jpeg", None, "image-compress"
    return raw, file_path.name, None, "none"


def upload_file(file_path: Path, h: dict) -> tuple[str, str]:
    """POST /upload -> returns (document reference, handling). prefers the file_id ref."""
    data, upload_name, params, handling = prepare_upload(file_path)
    resp = requests.post(
        f"{REDUCTO_API_BASE}/upload", headers=h, files={"file": (upload_name, data)}, params=params, timeout=180)
    if resp.status_code >= 300:
        raise RuntimeError(f"/upload failed {resp.status_code}: {resp.text[:400]}")
    body = resp.json()
    ref = body.get("file_id") or body.get("presigned_url")
    if not ref:
        raise RuntimeError(f"/upload returned no file ref: {json.dumps(body)[:300]}")
    return ref, handling


def poll_job(job_id: str, h: dict) -> Optional[dict]:
    """GET /job/{job_id} until Completed/Failed. returns the completed job's extract-response payload."""
    start = time.time()
    while True:
        if time.time() - start > POLL_TIMEOUT_SEC:
            print(f"    job {job_id} timed out after {POLL_TIMEOUT_SEC}s")
            return None
        time.sleep(POLL_INTERVAL_SEC)
        r = requests.get(f"{REDUCTO_API_BASE}/job/{job_id}", headers=h, timeout=60)
        if r.status_code >= 300:
            print(f"    GET /job/{job_id} failed {r.status_code}: {r.text[:200]}")
            return None
        body = r.json()
        status = str(body.get("status", "")).lower()
        if status in ("completed", "complete"):
            return body.get("result") or body
        if status in ("failed", "error", "cancelled", "canceled"):
            print(f"    job {job_id} {status}: {json.dumps(body)[:300]}")
            return None


def run_extract(document_url: str, schema: dict, h: dict, guidelines: str, deep_extract: bool) -> Optional[dict]:
    """submit async extract and poll to completion. deep_extract=True uses the agentic super_agent mode."""
    system_prompt = f"{SYSTEM_PROMPT}\n\n{guidelines.strip()}" if guidelines.strip() else SYSTEM_PROMPT
    payload = {
        "input": document_url,
        "instructions": {"schema": schema, "system_prompt": system_prompt},
        "settings": {"deep_extract": deep_extract}}
    resp = requests.post(
        f"{REDUCTO_API_BASE}/extract_async", headers={**h, "Content-Type": "application/json"}, json=payload, timeout=SUBMIT_TIMEOUT_SEC)
    if resp.status_code >= 300:
        print(f"    POST /extract_async failed {resp.status_code}: {resp.text[:500]}")
        return None
    job_id = resp.json().get("job_id")
    if not job_id:
        print(f"    /extract_async returned no job_id: {json.dumps(resp.json())[:300]}")
        return None
    return poll_job(job_id=job_id, h=h)


def extract_data(rec: dict) -> Any:
    """pull the extracted object out of reducto's response; unwrap a single-element result list."""
    result = rec.get("result", rec)
    if isinstance(result, dict) and "result" in result:
        result = result["result"]
    if isinstance(result, list):
        return result[0] if len(result) == 1 else result
    return result


def extract_cost(rec: dict) -> tuple[float, Optional[float]]:
    """best-effort credits/cost from the response usage block; (0.0, None) if absent."""
    usage = rec.get("usage") or {}
    credits = usage.get("credits") or usage.get("num_credits") or usage.get("total_credits")
    if credits is None:
        return 0.0, None
    return credits * REDUCTO_PER_CREDIT_USD, credits


def run(doc_id: str, file_path: Path, json_schema: dict, deep_extract: bool) -> Optional[dict]:
    """full per-doc flow: clean schema, upload file, extract, map result back into the field shape."""
    h = headers()
    schema = clean_node(json_schema)
    guidelines = load_guidelines(doc_id)
    try:
        document_url, handling = upload_file(file_path, h)
    except Exception as e:
        print(f"    reducto upload FAILED on {doc_id}: {e}")
        return None
    t0 = time.time()
    try:
        rec = run_extract(document_url=document_url, schema=schema, h=h, guidelines=guidelines, deep_extract=deep_extract)
    except Exception as e:
        print(f"    reducto extract FAILED on {doc_id}: {e}")
        return None
    tsec = time.time() - t0
    if rec is None:
        return None
    data = extract_data(rec)
    if not isinstance(data, dict):
        print(f"    reducto {doc_id}: unexpected result shape (not a dict): {json.dumps(data)[:200]}")
    cost, credits = extract_cost(rec)
    return {
        "data": data,
        "cost": cost,
        "time_sec": tsec,
        "meta": {
            "model": "reducto-deep-extract" if deep_extract else "reducto-standard",
            "document_url": document_url,
            "credits": credits,
            "file_handling": handling,
            "deep_extract": deep_extract,
            **guidelines_meta(guidelines),
        }}


def mode_from_output(output_path: Path) -> bool:
    """deep extract unless the output dir is reducto_standard, or REDUCTO_DEEP_EXTRACT overrides."""
    override = os.environ.get("REDUCTO_DEEP_EXTRACT")
    if override is not None:
        return override.strip() not in ("0", "false", "no", "")
    return output_path.parent.name != "reducto_standard"


def main():
    if len(sys.argv) != 4:
        print("usage: python scripts/run_reducto.py <document_path> <schemas/doc_id.json> <output.json>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    schema_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    json_schema = json.load(open(schema_path, encoding="utf-8"))
    doc_id = schema_path.stem
    result = run(doc_id=doc_id, file_path=file_path, json_schema=json_schema, deep_extract=mode_from_output(output_path))
    if result is None:
        print("extraction failed")
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
