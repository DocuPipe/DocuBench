"""run an extraction engine across the full benchmark with idempotent skips.

Currently supports GPT, Claude, and Gemini:

    python3 scripts/run_all.py
    python3 scripts/run_all.py --engine gpt --doc-id PSU5pciM
    python3 scripts/run_all.py --engine claude
    python3 scripts/run_all.py --engine gemini

Existing successful result files are skipped and printed. Failed or malformed result
files are rerun unless --force is used, which reruns everything.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ENGINE_RUNNERS = {
    "claude": "scripts/run_claude.py",
    "gemini": "scripts/run_gemini.py",
    "gpt": "scripts/run_gpt.py",
}


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
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
            if name:
                env[name] = value
    return env


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def benchmark_ids(root: Path) -> list[str]:
    return sorted(path.stem for path in (root / "labels").glob("*.json"))


def document_id_map(root: Path) -> dict[str, Path]:
    return {path.stem: path for path in (root / "documents").iterdir() if path.is_file()}


def result_state(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return "malformed"
    if not isinstance(payload, dict):
        return "malformed"
    if payload.get("status") == "ok":
        return "success"
    if payload.get("status") == "failed" or payload.get("error"):
        return "tracked_failure" if "data" in payload else "malformed"
    return "success" if "data" in payload else "malformed"


def is_successful_result(path: Path) -> bool:
    return result_state(path) == "success"


def selected_doc_ids(all_doc_ids: list[str], requested: list[str] | None, limit: int | None) -> list[str]:
    if requested:
        requested_set = set(requested)
        missing = sorted(requested_set - set(all_doc_ids))
        if missing:
            raise SystemExit(f"unknown doc_id(s): {', '.join(missing)}")
        doc_ids = [doc_id for doc_id in all_doc_ids if doc_id in requested_set]
    else:
        doc_ids = all_doc_ids
    return doc_ids[:limit] if limit is not None else doc_ids


def run_one(root: Path, runner: Path, engine: str, doc_id: str, document_path: Path) -> int:
    schema_path = root / "schemas" / f"{doc_id}.json"
    output_path = root / "results" / engine / f"{doc_id}.json"
    cmd = [sys.executable, str(runner), str(document_path), str(schema_path), str(output_path)]
    env = os.environ.copy()
    env.update(load_env_file(root / ".env"))
    completed = subprocess.run(cmd, cwd=root, env=env, check=False)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_all.py")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--engine", default="gpt", choices=sorted(ENGINE_RUNNERS))
    parser.add_argument("--doc-id", action="append", help="run only this document id; repeatable")
    parser.add_argument("--limit", type=int, help="run at most this many selected documents")
    parser.add_argument("--force", action="store_true", help="rerun even if a successful result exists")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    code_root = Path(__file__).resolve().parent.parent
    runner = code_root / ENGINE_RUNNERS[args.engine]
    if not runner.exists():
        raise SystemExit(f"missing runner: {runner}")

    docs = document_id_map(root)
    doc_ids = selected_doc_ids(benchmark_ids(root), args.doc_id, args.limit)
    skipped = 0
    succeeded = 0
    tracked_failed = 0
    failed = 0

    for doc_id in doc_ids:
        output_path = root / "results" / args.engine / f"{doc_id}.json"
        if not args.force and is_successful_result(output_path):
            print(f"skip {doc_id}: existing successful result at {output_path.relative_to(root)}")
            skipped += 1
            continue

        document_path = docs.get(doc_id)
        if document_path is None:
            print(f"fail {doc_id}: missing source document")
            failed += 1
            continue

        print(f"run {doc_id}: {document_path.relative_to(root)} -> {output_path.relative_to(root)}")
        rc = run_one(root, runner, args.engine, doc_id, document_path)
        state = result_state(output_path)
        if rc == 0 and state == "success":
            succeeded += 1
        elif rc == 0 and state == "tracked_failure":
            tracked_failed += 1
        else:
            print(f"fail {doc_id}: runner exit={rc}, result_state={state}")
            failed += 1

    print(
        f"done engine={args.engine} "
        f"run={succeeded} skipped={skipped} tracked_failed={tracked_failed} failed={failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
