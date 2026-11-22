import datetime as dt
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def snapshot():
    from inzkg.config import get_settings
    from inzkg.manual.parse import load_snapshot

    return load_snapshot(get_settings().snapshot_path)


@pytest.fixture(scope="session")
def rulebook(snapshot):
    from inzkg.config import get_settings
    from inzkg.rules import build_rulebook

    return build_rulebook(snapshot, get_settings().rules_dir)


@pytest.fixture(scope="session")
def driver():
    """A read-only driver for a Neo4j loaded with the rulebook. Skips if unreachable."""
    from inzkg.config import get_settings
    from inzkg.db import reader_driver, run_read

    try:
        d = reader_driver(get_settings())
        d.verify_connectivity()
        if not run_read(d, "MATCH (r:LoaderRun) RETURN count(r) AS n", {})[0]["n"]:
            pytest.skip("Neo4j is running but the rulebook is not loaded (run `inzkg load`)")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Neo4j not reachable: {exc}")
    yield d
    d.close()
