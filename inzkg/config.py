"""Settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "localdevpassword")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")
    # A separate read-only account for the chat assistant and query API. On Neo4j
    # Enterprise or Aura give this user the built-in "reader" role. On Community Edition
    # (no roles) the app still opens read-only sessions and checks every query plan.
    neo4j_reader_user: str = os.getenv("NEO4J_READER_USER") or os.getenv("NEO4J_USER", "neo4j")
    neo4j_reader_password: str = os.getenv("NEO4J_READER_PASSWORD") or os.getenv("NEO4J_PASSWORD", "localdevpassword")

    snapshot_path: Path = Path(os.getenv("INZKG_SNAPSHOT", str(ROOT / "data" / "snapshots" / "opsmanual-2026-11-22.json.gz")))
    rules_dir: Path = Path(os.getenv("INZKG_RULES_DIR", str(ROOT / "rules")))
    chat_log_dir: Path = Path(os.getenv("INZKG_CHAT_LOG_DIR", str(ROOT / "logs")))

    # Language model for the staff chat assistant.
    anthropic_model: str = os.getenv("INZKG_MODEL", "claude-opus-5-5")
    anthropic_effort: str = os.getenv("INZKG_EFFORT", "medium")
    max_tool_rounds: int = int(os.getenv("INZKG_MAX_TOOL_ROUNDS", "6"))


def get_settings() -> Settings:
    return Settings()
