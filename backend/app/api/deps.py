from __future__ import annotations

from urllib.parse import unquote

from fastapi import Header


def actor(x_user: str | None = Header(default=None)) -> str:
    """Who is acting. The UI sends the selected user's name in X-User (no auth in this prototype)."""
    return unquote(x_user).strip() if x_user and x_user.strip() else "anonymous"
