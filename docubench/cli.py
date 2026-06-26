"""Command-line utilities for validating and scoring DocuBench."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from scorer import score_standardization


DEFAULT_ENGINE_DISPLAY_NAMES = {
    "claude": "Claude",
    "docupipe_high": "DocuPipe high",
    "docupipe_standard": "DocuPipe standard",
    "extend": "Extend",
    "gemini": "Gemini",
    "gpt": "GPT",
}


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def benchmark_ids(root: Path) -> list[str]:
    return sorted(p.stem for p in (root / "labels").glob("*.json"))


def discover_engines(root: Path) -> list[str]:
    results_dir = root / "results"
    if not results_dir.exists():
        return []
    return sorted(p.name for p in results_dir.iterdir() if p.is_dir())


def document_id_map(root: Path) -> dict[str, Path]:
    docs: dict[str, Path] = {}
    for path in (root / "documents").iterdir():
        if path.is_file():
            docs[path.stem] = path
    return docs


def validate_benchmark(root: Path) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []

    required_dirs = ["documents", "labels", "schemas", "results"]
    for dirname in required_dirs:
        if not (root / dirname).is_dir():
            errors.append(f"missing required directory: {dirname}/")

    sources_path = root / "sources.json"
    if not sources_path.is_file():
        errors.append("missing sources.json")
        sources = []
    else:
        try:
            sources = load_json(sources_path)
        except json.JSONDecodeError as exc:
            errors.append(f"sources.json is invalid JSON: {exc}")
            sources = []

    if errors:
        return errors, warnings, {}

    doc_paths = document_id_map(root)
    doc_ids = set(doc_paths)
    label_ids = {p.stem for p in (root / "labels").glob("*.json")}
    schema_ids = {p.stem for p in (root / "schemas").glob("*.json")}
    source_ids = {row.get("doc_id") for row in sources if isinstance(row, dict)}

    expected_ids = label_ids | schema_ids | source_ids
    for collection_name, ids in [
        ("documents", doc_ids),
        ("labels", label_ids),
        ("schemas", schema_ids),
        ("sources", source_ids),
    ]:
        missing = sorted(expected_ids - ids)
        extra = sorted(ids - expected_ids)
        if missing:
            errors.append(f"{collection_name} missing ids: {', '.join(missing)}")
        if extra:
            errors.append(f"{collection_name} has unexpected ids: {', '.join(extra)}")

    for directory in ["labels", "schemas"]:
        for path in sorted((root / directory).glob("*.json")):
            try:
                load_json(path)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.relative_to(root)} is invalid JSON: {exc}")

    engines = discover_engines(root)
    for engine in engines:
        engine_dir = root / "results" / engine
        result_ids = {p.stem for p in engine_dir.glob("*.json")}
        missing = sorted(label_ids - result_ids)
        extra = sorted(result_ids - label_ids)
        if missing:
            warnings.append(f"results/{engine} missing ids: {', '.join(missing)}")
        if extra:
            warnings.append(f"results/{engine} has extra ids: {', '.join(extra)}")
        for path in sorted(engine_dir.glob("*.json")):
            try:
                payload = load_json(path)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.relative_to(root)} is invalid JSON: {exc}")
                continue
            if not isinstance(payload, dict):
                errors.append(f"{path.relative_to(root)} must contain a JSON object")
            elif "data" not in payload:
                warnings.append(f"{path.relative_to(root)} has no top-level data key")

    summary = {
        "documents": len(doc_ids),
        "labels": len(label_ids),
        "schemas": len(schema_ids),
        "sources": len(source_ids),
        "engines": engines,
    }
    return errors, warnings, summary


def score_engines(root: Path, engines: list[str] | None = None) -> dict[str, Any]:
    doc_ids = benchmark_ids(root)
    selected_engines = engines or discover_engines(root)
    per_engine: dict[str, dict[str, float | None]] = {engine: {} for engine in selected_engines}

    for doc_id in doc_ids:
        schema = load_json(root / "schemas" / f"{doc_id}.json")
        label = load_json(root / "labels" / f"{doc_id}.json")
        for engine in selected_engines:
            result_path = root / "results" / engine / f"{doc_id}.json"
            if not result_path.exists():
                per_engine[engine][doc_id] = None
                continue
            result_payload = load_json(result_path)
            result = result_payload.get("data", {}) if isinstance(result_payload, dict) else {}
            score = score_standardization(result=result, schema=schema, label=label)
            per_engine[engine][doc_id] = score["final"]

    per_doc = []
    for doc_id in doc_ids:
        row: dict[str, Any] = {"doc_id": doc_id}
        for engine in selected_engines:
            row[engine] = per_engine[engine][doc_id]
        per_doc.append(row)

    aggregates = {}
    for engine, scores_by_doc in per_engine.items():
        scores = [score for score in scores_by_doc.values() if score is not None]
        aggregates[engine] = sum(scores) / len(scores) if scores else None

    return {
        "benchmark": {
            "name": "DocuBench",
            "version": "0.1.0",
            "doc_count": len(doc_ids),
            "metric": "macro_average_field_accuracy",
        },
        "engine_display_names": {
            engine: DEFAULT_ENGINE_DISPLAY_NAMES.get(engine, engine)
            for engine in selected_engines
        },
        "aggregates": aggregates,
        "breakdowns": build_breakdowns(root, per_doc, selected_engines),
        "per_doc": per_doc,
    }


def build_breakdowns(root: Path, per_doc: list[dict[str, Any]], engines: list[str]) -> dict[str, Any]:
    sources_path = root / "sources.json"
    if not sources_path.exists():
        return {}
    sources = load_json(sources_path)
    metadata = {row["doc_id"]: row for row in sources if isinstance(row, dict) and "doc_id" in row}
    by_doc = {row["doc_id"]: row for row in per_doc}
    breakdowns: dict[str, Any] = {}

    for dimension in ["ftype", "lang"]:
        groups: dict[str, list[str]] = {}
        for doc_id, row in metadata.items():
            value = str(row.get(dimension) or "unknown")
            groups.setdefault(value, []).append(doc_id)
        dimension_rows = []
        for value in sorted(groups):
            doc_ids = sorted(doc_id for doc_id in groups[value] if doc_id in by_doc)
            out: dict[str, Any] = {"value": value, "doc_count": len(doc_ids)}
            for engine in engines:
                scores = [by_doc[doc_id][engine] for doc_id in doc_ids if by_doc[doc_id][engine] is not None]
                out[engine] = sum(scores) / len(scores) if scores else None
            dimension_rows.append(out)
        breakdowns[dimension] = dimension_rows
    return breakdowns


def print_score_table(scores: dict[str, Any]) -> None:
    engines = list(scores["aggregates"].keys())
    header = f"{'doc_id':<12}" + "".join(f"{engine:>20}" for engine in engines)
    print(header)
    print("-" * len(header))
    for row in scores["per_doc"]:
        line = f"{row['doc_id']:<12}"
        for engine in engines:
            score = row[engine]
            line += f"{(f'{score:.4f}' if score is not None else 'n/a'):>20}"
        print(line)
    print("-" * len(header))
    aggregate_line = f"{'AGGREGATE':<12}"
    for engine in engines:
        score = scores["aggregates"][engine]
        aggregate_line += f"{(f'{score:.4f}' if score is not None else 'n/a'):>20}"
    print(aggregate_line)


def write_summary_csv(path: Path, scores: dict[str, Any]) -> None:
    engines = list(scores["aggregates"].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["doc_id", *engines], lineterminator="\n")
        writer.writeheader()
        for row in scores["per_doc"]:
            writer.writerow({key: row.get(key) for key in ["doc_id", *engines]})
        writer.writerow({"doc_id": "AGGREGATE", **scores["aggregates"]})


def cmd_validate(args: argparse.Namespace) -> int:
    errors, warnings, summary = validate_benchmark(args.root)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        "validated "
        f"{summary['documents']} documents, "
        f"{summary['labels']} labels, "
        f"{summary['schemas']} schemas, "
        f"{summary['sources']} source records, "
        f"{len(summary['engines'])} result sets"
    )
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    scores = score_engines(args.root, args.engine)
    if args.json:
        print(json.dumps(scores, ensure_ascii=False, indent=2))
    else:
        print_score_table(scores)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    scores = score_engines(args.root, args.engine)
    write_json(args.summary_json, scores)
    write_summary_csv(args.summary_csv, scores)
    print(f"wrote {args.summary_json}")
    print(f"wrote {args.summary_csv}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docubench")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate benchmark files")
    validate.set_defaults(func=cmd_validate)

    score = subparsers.add_parser("score", help="score committed result sets")
    score.add_argument("--engine", action="append", help="result directory to score; repeatable")
    score.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    score.set_defaults(func=cmd_score)

    report = subparsers.add_parser("report", help="write summary JSON and CSV reports")
    report.add_argument("--engine", action="append", help="result directory to score; repeatable")
    report.add_argument("--summary-json", type=Path, default=Path("results/summary.json"))
    report.add_argument("--summary-csv", type=Path, default=Path("results/summary.csv"))
    report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    if hasattr(args, "summary_json") and not args.summary_json.is_absolute():
        args.summary_json = args.root / args.summary_json
    if hasattr(args, "summary_csv") and not args.summary_csv.is_absolute():
        args.summary_csv = args.root / args.summary_csv
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
