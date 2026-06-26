import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_data = load_module("build_data", "space/build_data.py")


def test_build_data_structure():
    data = build_data.build()
    summary = json.loads((ROOT / "results" / "summary.json").read_text())

    engine_keys = [e["key"] for e in data["engines"]]
    assert engine_keys == list(summary["aggregates"].keys())
    assert len(data["documents"]) == summary["benchmark"]["doc_count"]
    assert data["breakdowns"]["capability"], "expected at least one capability row"

    for doc in data["documents"]:
        assert set(doc["scores"].keys()) == set(engine_keys)
        assert doc["name"] and doc["ftype"] and doc["lang"]


def test_committed_leaderboard_json_is_in_sync():
    # the Space ships a committed copy; it must match a fresh build so it never goes stale
    committed = json.loads((ROOT / "space" / "leaderboard.json").read_text())
    assert committed == build_data.build(), "run `python3 space/build_data.py` to refresh space/leaderboard.json"


def test_app_tables():
    pytest.importorskip("pandas")
    pytest.importorskip("gradio")
    app = load_module("space_app", "space/app.py")

    leaderboard = app.leaderboard_df()
    assert len(leaderboard) == len(app.ENGINES)
    assert list(leaderboard["Rank"]) == sorted(leaderboard["Rank"])

    all_docs = app.documents_df()
    assert len(all_docs) == len(app.DATA["documents"])
    rtl_docs = app.documents_df(capability="rtl")
    assert 0 < len(rtl_docs) < len(all_docs)
