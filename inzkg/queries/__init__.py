"""Run approved queries. This is the only way the app reads the graph."""

from __future__ import annotations

from typing import Any

from neo4j import Driver

from ..db import run_read
from .catalogue import QUERIES, VISA_TYPES, Query, extra_params


class UnknownQuery(KeyError):
    pass


def get_query(query_id: str) -> Query:
    try:
        return QUERIES[query_id]
    except KeyError:
        raise UnknownQuery(query_id) from None


def execute(driver: Driver, query_id: str, args: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    """Validate ``args`` for ``query_id`` and run it read-only. Returns (params used, rows)."""
    query = get_query(query_id)
    params = query.validate(args)
    params.update(extra_params(query, params))
    rows = run_read(driver, query.cypher, params)
    return params, rows[: query.limit]


__all__ = ["QUERIES", "VISA_TYPES", "Query", "execute", "get_query", "UnknownQuery"]
