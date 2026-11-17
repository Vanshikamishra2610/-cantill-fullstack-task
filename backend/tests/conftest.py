from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
KIT = BACKEND.parent
sys.path.insert(0, str(BACKEND))


@pytest.fixture(scope="session")
def kit() -> Path:
    return KIT


@pytest.fixture(scope="session")
def master_data(kit):
    from app.kit_loader import load_master_data

    return load_master_data(kit)


@pytest.fixture(scope="session")
def policy_v5():
    from app.engine.policy import Policy

    return Policy.model_validate(json.loads((BACKEND / "app" / "policies" / "policy_v5.0.json").read_text()))


@pytest.fixture(scope="session")
def sample_lines(kit):
    from app.engine.csv_io import parse_pr_csv

    return parse_pr_csv((kit / "pr_batches" / "sample_pr_batch.csv").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def sample_result(master_data, policy_v5, sample_lines):
    from app.engine.pipeline import DecisionEngine
    from app.engine.policy import PolicyBook

    return DecisionEngine(master_data, PolicyBook([policy_v5])).process(sample_lines)


@pytest.fixture(scope="session")
def rows(sample_result):
    return {(r["pr_no"], r["pr_line"]): r for r in sample_result.export_rows()}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """API client on a fresh SQLite database."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("LLM_ENABLED", "false")
    for mod in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        c.headers.update({"X-User": "Test Buyer"})
        yield c
    os.environ.pop("DATABASE_URL", None)
