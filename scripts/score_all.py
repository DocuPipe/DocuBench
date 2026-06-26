"""score every engine's extraction results against the hand-labeled ground truth.

for each engine and each doc_id, loads:
  - results/<engine>/<doc_id>.json   (extracted data under key "data")
  - schemas/<doc_id>.json            (the json schema)
  - labels/<doc_id>.json             (ground truth)

runs scorer.score_standardization and prints a per-doc + aggregate table.
aggregate = simple mean of per-doc finals.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scorer import score_standardization  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE_ENGINE_ORDER = ["docupipe_high", "docupipe_standard", "extend"]


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def discover_engines() -> list[str]:
    result_dirs = [p.name for p in (ROOT / "results").iterdir() if p.is_dir()]
    ordered = [engine for engine in BASELINE_ENGINE_ORDER if engine in result_dirs]
    ordered.extend(sorted(engine for engine in result_dirs if engine not in BASELINE_ENGINE_ORDER))
    return ordered


def main():
    # the doc set = whatever labels we have
    doc_ids = sorted(p.stem for p in (ROOT / "labels").glob("*.json"))
    engines = discover_engines()

    # per_engine[engine][doc_id] = final score
    per_engine: dict = {engine: {} for engine in engines}
    for doc_id in doc_ids:
        schema = load_json(ROOT / "schemas" / f"{doc_id}.json")
        label = load_json(ROOT / "labels" / f"{doc_id}.json")
        for engine in engines:
            result_path = ROOT / "results" / engine / f"{doc_id}.json"
            if not result_path.exists():
                per_engine[engine][doc_id] = None
                continue
            result = load_json(result_path).get("data", {})
            out = score_standardization(result=result, schema=schema, label=label)
            per_engine[engine][doc_id] = out["final"]

    # per-doc table
    header = f"{'doc_id':<12}" + "".join(f"{e:>20}" for e in engines)
    print(header)
    print("-" * len(header))
    for doc_id in doc_ids:
        row = f"{doc_id:<12}"
        for engine in engines:
            s = per_engine[engine][doc_id]
            row += f"{(f'{s:.4f}' if s is not None else 'n/a'):>20}"
        print(row)

    # aggregate = simple mean of per-doc finals
    print("-" * len(header))
    agg_row = f"{'AGGREGATE':<12}"
    aggregates = {}
    for engine in engines:
        scores = [s for s in per_engine[engine].values() if s is not None]
        agg = sum(scores) / len(scores) if scores else 0.0
        aggregates[engine] = agg
        agg_row += f"{agg:>20.4f}"
    print(agg_row)
    return aggregates


if __name__ == "__main__":
    main()
