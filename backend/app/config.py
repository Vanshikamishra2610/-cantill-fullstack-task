"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

# Folder holding the task kit (master_data/, contracts/, pr_batches/).
KIT_DIR = Path(os.getenv("KIT_DIR", REPO_DIR))
POLICY_SEED = BACKEND_DIR / "app" / "policies" / "policy_v5.0.json"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'corvo.db'}")
if DATABASE_URL.startswith("postgres://"):  # Render / Heroku style URLs
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", REPO_DIR / "frontend" / "dist"))

# LLM settings (optional) are read at call time in services/llm.py, so they can
# be changed without a rebuild. Without any key the app still works: chat uses
# the rule-based parser and policy changes go through the structured editor.
