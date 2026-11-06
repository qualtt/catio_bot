"""Shared paths and helpers for the Telegram-based data recovery tools.

The recovery flow (see README.md in this folder):
    login_userbot  -> export_telegram -> fetch_file_ids -> reconstruct -> build_db -> schedule_report
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = Path(os.environ.get("RECOVERY_ENV_PATH", REPO_ROOT / ".env"))
OUT_DIR = Path(os.environ.get("RECOVERY_DIR", Path.home() / "catio_recovery"))
PHOTOS_DIR = OUT_DIR / "photos"

MANIFEST_BOT = OUT_DIR / "manifest_bot.jsonl"
MANIFEST_CHANNEL = OUT_DIR / "manifest_channel.jsonl"
FILE_IDS_PATH = OUT_DIR / "file_ids.json"
RECONSTRUCTION_PATH = OUT_DIR / "reconstruction.json"
OVERRIDES_PATH = OUT_DIR / "overrides.json"
REPORT_PATH = OUT_DIR / "schedule.html"


def env() -> dict[str, str]:
    values = {k: (v or "").strip() for k, v in dotenv_values(ENV_PATH).items()}
    # Real environment variables win, same as pydantic-settings in bot.config.
    values.update({k: v for k, v in os.environ.items() if k in values or k == "DATABASE_URL"})
    return values


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open() as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def body(record: dict) -> str:
    """Text or caption of an exported message, as plain text."""
    return ((record.get("caption") or record.get("text") or {}).get("text")) or ""
