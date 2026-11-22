"""Neo4j connections. Writes happen only in the loader; everything else reads."""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any, Iterator

from neo4j import READ_ACCESS, GraphDatabase, Driver
from neo4j.time import Date, DateTime

from .config import Settings, get_settings


def writer_driver(settings: Settings | None = None) -> Driver:
    s = settings or get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password), notifications_min_severity="OFF")


def reader_driver(settings: Settings | None = None) -> Driver:
    s = settings or get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_reader_user, s.neo4j_reader_password), notifications_min_severity="OFF")


class ReadOnlyViolation(RuntimeError):
    pass


@contextmanager
def read_session(driver: Driver, database: str | None = None) -> Iterator[Any]:
    with driver.session(database=database or get_settings().neo4j_database, default_access_mode=READ_ACCESS) as session:
        yield session


def run_read(driver: Driver, cypher: str, params: dict[str, Any], database: str | None = None) -> list[dict]:
    """Run one query in a read transaction after checking its plan is read-only.

    ``EXPLAIN`` returns the query type without executing it: "r" means read-only. Anything
    else ("rw", "w", "s") is refused before it can run.
    """
    with read_session(driver, database) as session:
        summary = session.run("EXPLAIN " + cypher, params).consume()
        if summary.query_type != "r":
            raise ReadOnlyViolation(f"query type {summary.query_type!r} is not read-only")

        def work(tx):
            return [to_python(r.data()) for r in tx.run(cypher, params)]

        return session.execute_read(work)


def to_python(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: to_python(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_python(v) for v in value]
    if isinstance(value, Date):
        return value.to_native().isoformat()
    if isinstance(value, DateTime):
        return value.to_native().isoformat()
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value
