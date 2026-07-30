"""TabularStore seam: Postgres (business DB) now, BigQuery stub."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row


class TabularStore(ABC):
    @abstractmethod
    def execute(
        self,
        sql: str,
        *,
        params: Sequence[Any] | None = None,
        row_cap: int | None = None,
        timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def headings(self, sql: str) -> list[str]:
        """Return column headings without reading data rows (LIMIT 0)."""
        ...

    @abstractmethod
    def explain(self, sql: str) -> str:
        ...

    @abstractmethod
    def estimate_rows(self, sql: str) -> int | None:
        ...


class PostgresStore(TabularStore):
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def execute(
        self,
        sql: str,
        *,
        params: Sequence[Any] | None = None,
        row_cap: int | None = None,
        timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        capped = sql.rstrip().rstrip(";")
        if row_cap is not None:
            capped = f"SELECT * FROM ({capped}) AS _q LIMIT {int(row_cap)}"
        with self._connect() as conn:
            if timeout_ms is not None:
                conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(int(timeout_ms)),),
                )
            rows = conn.execute(capped, params).fetchall()
        return [dict(r) for r in rows]

    def headings(self, sql: str) -> list[str]:
        capped = sql.rstrip().rstrip(";")
        probe = f"SELECT * FROM ({capped}) AS _q LIMIT 0"
        with self._connect() as conn:
            cur = conn.execute(probe)
            if cur.description is None:
                return []
            return [col.name for col in cur.description]

    def explain(self, sql: str) -> str:
        capped = sql.rstrip().rstrip(";")
        with self._connect() as conn:
            rows = conn.execute(f"EXPLAIN {capped}").fetchall()
        # EXPLAIN returns a single text column; name varies
        lines = []
        for row in rows:
            lines.append(next(iter(row.values())))
        return "\n".join(str(x) for x in lines)

    def estimate_rows(self, sql: str) -> int | None:
        plan = self.explain(sql)
        match = re.search(r"rows=(\d+)", plan)
        if match:
            return int(match.group(1))
        return None


class BigQueryStore(TabularStore):
    """Stub — not wired for the demo."""

    def execute(
        self,
        sql: str,
        *,
        params: Sequence[Any] | None = None,
        row_cap: int | None = None,
        timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("BigQuery store is a stub")

    def headings(self, sql: str) -> list[str]:
        raise NotImplementedError("BigQuery store is a stub")

    def explain(self, sql: str) -> str:
        raise NotImplementedError("BigQuery store is a stub")

    def estimate_rows(self, sql: str) -> int | None:
        raise NotImplementedError("BigQuery store is a stub")
