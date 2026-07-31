"""run a single document through the Pulse AI (runpulse.com) extraction API.

two-step flow: POST /extract uploads the file and returns an extraction_id, then POST /schema applies
our JSON Schema to that extraction and returns schema_output.values. Pulse accepts standard JSON
Schema (nested arrays-of-objects included) directly, so the schema handling is a light clean rather
than a structural rewrite — same as Reducto, unlike Extend.

two config axes:
  - model  (on /extract): "default" or "pulse-ultra-2" (their VLM with built-in refinement)
  - effort (on /schema):  extended reasoning on/off
the mode is inferred from the output directory name (…/pulse_standard/ -> default + no effort,
anything else -> pulse-ultra-2 + effort), and either axis can be forced with PULSE_MODEL / PULSE_EFFORT.

Pulse rejects a file shape it can still read once trivially re-encoded, and has one outright bug;
the runner applies the same transforms the benchmark used so the content is reachable:
  - xml/json/txt files upload as .html (Pulse accepts HTML but not TXT or XML)
  - tiff/bmp/gif images are re-encoded to PNG (Pulse rejects those container formats)
  - pulse-ultra-2 500s on spreadsheets ("process_excel_to_markdown() got an unexpected keyword
    argument 'refine'"), so an extract failure on a non-default model retries once on "default"

set PULSE_API_KEY for your own Pulse workspace.

    python scripts/run_pulse.py <document_path> <schemas/doc_id.json> <output.json>
"""
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests
from PIL import Image, ImageSequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_gpt import guidelines_meta, load_guidelines

PULSE_API_BASE = "https://api.runpulse.com"
PULSE_PER_CREDIT_USD = 0.015
EXTRACT_TIMEOUT_SEC = int(os.environ.get("PULSE_EXTRACT_TIMEOUT_SEC", "900"))
SCHEMA_TIMEOUT_SEC = int(os.environ.get("PULSE_SCHEMA_TIMEOUT_SEC", "900"))
POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = int(os.environ.get("PULSE_POLL_TIMEOUT_SEC", "1800"))
DROP_SCHEMA_KEYS = {"$schema", "examples", "default", "title"}
# pulse-ultra-2 500s on spreadsheets; this is the model we retry on
FALLBACK_MODEL = "default"
BEST_MODEL = "pulse-ultra-2"

SCHEMA_PROMPT = "Be precise and thorough. Extract exactly what appears in the document; do not infer or fabricate values."

# pulse rejects anything outside this list with FILE_001; the two remaps below make the content
# reachable, documented as a file-handling gap rather than a scored zero (same convention as reducto)
SUPPORTED_SUFFIXES = {
    ".pdf", ".jpg", ".jpeg", ".png", ".docx", ".pptx", ".xlsx", ".xlsm", ".xls", ".xlsb", ".html", ".csv", ".webp"}
HTML_REMAP_SUFFIXES = {".xml", ".json", ".txt"}
IMAGE_CONVERT_SUFFIXES = {".tiff", ".tif", ".bmp", ".gif"}


def enum_is_safe(values: Any) -> bool:
    """pulse strict mode rejects enum literals containing quotes, backslashes or slashes (REQ_005).
    hit by 'אלפי ש"ח' and by 'חשבונית מס / קבלה'.
    """
    if not isinstance(values, list):
        return True
    return not any(isinstance(v, str) and any(c in v for c in ('"', "\\", "/")) for v in values)


def sanitize_description(text: str) -> str:
    """same REQ_005 rule applies to descriptions; these are guidance rather than matched data, so
    strip the offending characters instead of dropping the field's description entirely.
    """
    return text.replace('"', "'").replace("\\", "")


def clean_node(spec: Any) -> Any:
    """recursively strip non-standard/leaky keys but keep structure, types, enums, descriptions.
    drops enum constraints pulse's strict mode rejects — the field is still extracted, just unconstrained.
    """
    if isinstance(spec, dict):
        out = {k: clean_node(v) for k, v in spec.items() if not k.startswith("x_") and k not in DROP_SCHEMA_KEYS}
        if "enum" in out and not enum_is_safe(out["enum"]):
            out.pop("enum")
        if isinstance(out.get("description"), str):
            out["description"] = sanitize_description(out["description"])
        return out
    if isinstance(spec, list):
        return [clean_node(v) for v in spec]
    return spec


def headers() -> dict[str, str]:
    key = os.environ.get("PULSE_API_KEY")
    if not key:
        raise RuntimeError("PULSE_API_KEY not set")
    return {"x-api-key": key}


def to_supported_image(raw: bytes) -> tuple[bytes, str]:
    """re-encode an image pulse cannot ingest (tiff/bmp/gif). multi-frame files become a multi-page pdf
    rather than a png, which would silently keep only the first frame and understate the vendor.
    """
    img = Image.open(io.BytesIO(raw))
    frames = [f.convert("RGB") for f in ImageSequence.Iterator(img)]
    buf = io.BytesIO()
    if len(frames) == 1:
        page = frames[0] if img.mode not in ("RGB", "L") else img
        page.save(buf, format="PNG")
        return buf.getvalue(), "png"
    frames[0].save(buf, format="PDF", save_all=True, append_images=frames[1:])
    return buf.getvalue(), "pdf"


def prepare_upload(file_path: Path) -> tuple[bytes, str, str]:
    """returns (bytes, upload_name, handling) applying pulse-specific file remaps.
    """
    suffix = file_path.suffix.lower()
    raw = file_path.read_bytes()
    if suffix in HTML_REMAP_SUFFIXES:
        return raw, f"{file_path.stem}.html", "html-remap"
    if suffix in IMAGE_CONVERT_SUFFIXES:
        data, ext = to_supported_image(raw)
        return data, f"{file_path.stem}.{ext}", f"image-convert-{ext}"
    return raw, file_path.name, "none"


def poll_job(job_id: str, h: dict) -> Optional[dict]:
    """GET /job/{job_id} until the async job completes. returns the result payload or None.
    """
    start = time.time()
    while True:
        if time.time() - start > POLL_TIMEOUT_SEC:
            print(f"    pulse job {job_id} timed out after {POLL_TIMEOUT_SEC}s")
            return None
        time.sleep(POLL_INTERVAL_SEC)
        r = requests.get(f"{PULSE_API_BASE}/job/{job_id}", headers=h, timeout=60)
        if r.status_code >= 300:
            print(f"    GET /job/{job_id} failed {r.status_code}: {r.text[:200]}")
            return None
        body = r.json()
        status = str(body.get("status", "")).lower()
        if status in ("completed", "complete", "success", "succeeded"):
            return body.get("result") or body
        if status in ("failed", "error", "cancelled", "canceled"):
            print(f"    pulse job {job_id} {status}: {json.dumps(body)[:300]}")
            return None


def fetch_large_result(body: dict, h: dict) -> Optional[dict]:
    """pulse returns {is_url: true, url: ...} instead of inline json for payloads over ~5mb (long docs,
    spreadsheet extractions). the link is single-use and expires in an hour, so fetch it immediately.
    """
    if not (isinstance(body, dict) and body.get("is_url") and body.get("url")):
        return body
    r = requests.get(body["url"], headers=h, timeout=300)
    if r.status_code >= 300:
        print(f"    large-result fetch failed {r.status_code}: {r.text[:200]}")
        return None
    return r.json()


def resolve_async(body: Optional[dict], h: dict) -> Optional[dict]:
    """if the response is an async job acknowledgement, poll it; then resolve any large-result link.
    """
    if isinstance(body, dict) and body.get("job_id") and body.get("status") in ("pending", "processing"):
        body = poll_job(job_id=body["job_id"], h=h)
    if body is None:
        return None
    return fetch_large_result(body, h)


def run_extract(file_path: Path, h: dict, model: str, use_async: bool) -> tuple[Optional[dict], str]:
    """POST /extract with the file. returns (response body, file handling).
    """
    data, upload_name, handling = prepare_upload(file_path)
    fields = {"model": model}
    if use_async:
        fields["async"] = "true"
    resp = requests.post(
        f"{PULSE_API_BASE}/extract", headers=h, files={"file": (upload_name, data)}, data=fields, timeout=EXTRACT_TIMEOUT_SEC)
    if resp.status_code >= 300:
        print(f"    POST /extract failed {resp.status_code}: {resp.text[:400]}")
        return None, handling
    return resolve_async(resp.json(), h), handling


def run_schema(extraction_id: str, schema: dict, h: dict, effort: bool, guidelines: str, use_async: bool) -> Optional[dict]:
    """POST /schema applying our json schema to a saved extraction.
    """
    schema_prompt = f"{SCHEMA_PROMPT}\n\n{guidelines.strip()}" if guidelines.strip() else SCHEMA_PROMPT
    payload: dict[str, Any] = {
        "extraction_id": extraction_id,
        "schema_config": {"input_schema": schema, "schema_prompt": schema_prompt, "effort": effort}}
    if use_async:
        payload["async"] = True
    resp = requests.post(
        f"{PULSE_API_BASE}/schema", headers={**h, "Content-Type": "application/json"}, json=payload, timeout=SCHEMA_TIMEOUT_SEC)
    if resp.status_code >= 300:
        print(f"    POST /schema failed {resp.status_code}: {resp.text[:400]}")
        return None
    return resolve_async(resp.json(), h)


def extract_data(rec: dict) -> Any:
    """pull the extracted object out of pulse's schema response ({schema_output: {values, citations}}).
    """
    output = rec.get("schema_output") or {}
    if isinstance(output, dict) and "values" in output:
        return output["values"]
    return output or rec


def run(doc_id: str, file_path: Path, json_schema: dict, model: str, effort: bool, use_async: bool = False) -> Optional[dict]:
    """full per-doc flow: clean schema, extract the file, apply the schema, map back into the field shape.
    """
    h = headers()
    schema = clean_node(json_schema)
    guidelines = load_guidelines(doc_id)
    t0 = time.time()
    extraction, handling = None, "none"
    try:
        extraction, handling = run_extract(file_path=file_path, h=h, model=model, use_async=use_async)
    except Exception as e:
        print(f"    pulse extract FAILED on {doc_id}: {e}")

    # sync /extract read-times-out on large files; pulse's own docs say use async there
    used_async, resubmitted = use_async, False
    if not (extraction and extraction.get("extraction_id")) and not use_async:
        print(f"    pulse {doc_id}: sync extract failed, retrying async")
        resubmitted = True
        try:
            extraction, handling = run_extract(file_path=file_path, h=h, model=model, use_async=True)
            used_async = True
        except Exception as e:
            print(f"    pulse async retry FAILED on {doc_id}: {e}")

    # pulse-ultra-2 500s on spreadsheets; fall back so the content is still reachable
    used_model = model
    if (not extraction or not extraction.get("extraction_id")) and model != FALLBACK_MODEL:
        print(f"    pulse {doc_id}: {model} failed, retrying on {FALLBACK_MODEL}")
        try:
            extraction, handling = run_extract(file_path=file_path, h=h, model=FALLBACK_MODEL, use_async=used_async)
            used_model = FALLBACK_MODEL
            handling = f"{handling}+model-fallback"
        except Exception as e:
            print(f"    pulse fallback extract FAILED on {doc_id}: {e}")
            return None

    if not extraction or not extraction.get("extraction_id"):
        print(f"    pulse {doc_id}: /extract returned no extraction_id")
        return None

    rec = None
    try:
        rec = run_schema(
            extraction_id=extraction["extraction_id"], schema=schema, h=h,
            effort=effort, guidelines=guidelines, use_async=used_async)
    except Exception as e:
        print(f"    pulse schema FAILED on {doc_id}: {e}")

    # the schema step read-times-out on many-item documents the same way extract does on large ones
    if rec is None and not used_async:
        print(f"    pulse {doc_id}: sync schema failed, retrying async")
        resubmitted = True
        try:
            rec = run_schema(
                extraction_id=extraction["extraction_id"], schema=schema, h=h,
                effort=effort, guidelines=guidelines, use_async=True)
            used_async = True
        except Exception as e:
            print(f"    pulse async schema retry FAILED on {doc_id}: {e}")
            return None
    tsec = time.time() - t0
    if rec is None:
        return None

    data = extract_data(rec)
    if not isinstance(data, dict):
        print(f"    pulse {doc_id}: unexpected result shape (not a dict): {json.dumps(data)[:200]}")
    credits = (extraction.get("credits_used") or 0.0) + (rec.get("credits_used") or 0.0)
    return {
        "data": data,
        "cost": credits * PULSE_PER_CREDIT_USD,
        "time_sec": tsec,
        "meta": {
            "model": used_model,
            "effort": effort,
            "async": used_async,
            "resubmitted": resubmitted,
            "cost_reliable": not resubmitted,
            "extraction_id": extraction.get("extraction_id"),
            "schema_id": rec.get("schema_id"),
            "credits": credits,
            "page_count": extraction.get("page_count"),
            "file_handling": handling,
            **guidelines_meta(guidelines),
        }}


def config_from_output(output_path: Path) -> tuple[str, bool]:
    """best config unless the output dir is pulse_standard; PULSE_MODEL / PULSE_EFFORT override either axis.
    """
    standard = output_path.parent.name == "pulse_standard"
    model = os.environ.get("PULSE_MODEL") or (FALLBACK_MODEL if standard else BEST_MODEL)
    effort_env = os.environ.get("PULSE_EFFORT")
    effort = (effort_env.strip() not in ("0", "false", "no", "")) if effort_env is not None else (not standard)
    return model, effort


def main():
    if len(sys.argv) != 4:
        print("usage: python scripts/run_pulse.py <document_path> <schemas/doc_id.json> <output.json>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    schema_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    json_schema = json.load(open(schema_path, encoding="utf-8"))
    doc_id = schema_path.stem
    model, effort = config_from_output(output_path)
    result = run(doc_id=doc_id, file_path=file_path, json_schema=json_schema, model=model, effort=effort)
    if result is None:
        print("extraction failed")
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
