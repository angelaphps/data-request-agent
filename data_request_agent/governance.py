"""Governance database: admins · approvals · audit · checkpointer seam.

Semantic meanings are read from ``semantic_layer/`` YAML via ``catalog``,
not from governance catalog tables (legacy tables may still exist unused).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import psycopg
import yaml
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class Governance:
    """Persistence + policy surface for the agent."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def is_admin(self, slack_user_id: str) -> bool:
        """Return True if slack_user_id is an active row in governance.admins."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM governance.admins
                WHERE slack_user_id = %s AND active = TRUE
                """,
                (slack_user_id,),
            ).fetchone()
        return row is not None

    def list_admins(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT slack_user_id, display_name, role, active, created_at
                FROM governance.admins
                ORDER BY slack_user_id
                """
            ).fetchall()
        return list(rows)

    def upsert_admins(self, admins: list[dict[str, str]]) -> int:
        """Upsert admin rows from an in-repo YAML list. Returns count written."""
        with self.connect() as conn:
            for admin in admins:
                conn.execute(
                    """
                    INSERT INTO governance.admins (slack_user_id, display_name, role, active)
                    VALUES (%s, %s, %s, TRUE)
                    ON CONFLICT (slack_user_id) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        role = EXCLUDED.role,
                        active = TRUE
                    """,
                    (
                        admin["slack_user_id"],
                        admin.get("display_name", admin["slack_user_id"]),
                        admin.get("role", "approver"),
                    ),
                )
            conn.commit()
        return len(admins)

    def get_dataset(self, name: str) -> dict[str, Any] | None:
        """Return dataset from YAML catalog (not governance tables)."""
        from data_request_agent.catalog import get_semantic_catalog

        ds = get_semantic_catalog().get_dataset(name)
        if ds is None:
            return None
        return {
            "name": ds["name"],
            "description": ds.get("description"),
            "owner": ds.get("owner"),
            "sensitivity": ds.get("sensitivity", "none"),
            "table_schema": ds["table_schema"],
            "table_name": ds["table_name"],
            "columns": [
                {
                    "name": c["name"],
                    "description": c.get("description"),
                    "sensitivity": c.get("sensitivity", "none"),
                }
                for c in ds.get("columns") or []
            ],
        }

    def get_metric(self, name: str) -> dict[str, Any] | None:
        """Return metric from YAML catalog (not governance tables)."""
        from data_request_agent.catalog import get_semantic_catalog

        m = get_semantic_catalog().get_metric(name)
        if m is None:
            return None
        return {
            "name": m["name"],
            "definition": m.get("definition") or "",
            "expression": m.get("expression") or "",
            "dataset_name": m.get("dataset_name") or m.get("dataset") or "",
        }

    def list_metric_names(self) -> list[str]:
        from data_request_agent.catalog import get_semantic_catalog

        return get_semantic_catalog().metric_names()

    def load_catalog_from_yaml(self, semantic_layer_dir: str | Path) -> dict[str, Any]:
        """Validate YAML catalog only — does not write governance catalog tables.

        Runtime reads ``semantic_layer/`` directly. Kept for older scripts/tests.
        """
        from data_request_agent.catalog import load_semantic_catalog

        cat = load_semantic_catalog(semantic_layer_dir)
        return {
            "dataset_count": len(cat.datasets),
            "metric_count": len(cat.metrics),
            "relationship_count": len(cat.relationships),
            "golden_query_count": len(cat.golden_queries),
            "source": "semantic_layer_yaml",
        }

    def audit(
        self,
        event: str,
        payload: dict[str, Any] | None = None,
        *,
        actor_slack_id: str | None = None,
    ) -> int:
        """Append an audit event. Returns the new row id."""
        with self.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO governance.audit_log (event, actor_slack_id, payload)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (event, actor_slack_id, Jsonb(payload or {})),
            ).fetchone()
            conn.commit()
        return int(row["id"])

    def recent_audit(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, event, actor_slack_id, payload, created_at
                FROM governance.audit_log
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return list(rows)

    def create_approval(
        self,
        *,
        approval_id: str,
        request_id: str,
        requester_slack_id: str,
        channel_id: str,
        thread_ts: str,
        plan: dict[str, Any],
        touches_personal_data: bool,
        expires_at: Any | None = None,
        status: str = "pending",
        decided_by: list[str] | None = None,
    ) -> str:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO governance.approvals (
                    id, request_id, requester_slack_id, channel_id, thread_ts,
                    plan, status, touches_personal_data, expires_at, decided_by,
                    decided_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    CASE WHEN %s IN ('approved', 'rejected') THEN NOW() ELSE NULL END
                )
                """,
                (
                    approval_id,
                    request_id,
                    requester_slack_id,
                    channel_id,
                    thread_ts,
                    Jsonb(plan),
                    status,
                    touches_personal_data,
                    expires_at,
                    decided_by or [],
                    status,
                ),
            )
            conn.commit()
        return approval_id

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, request_id, requester_slack_id, channel_id, thread_ts,
                       plan, status, touches_personal_data, created_at, expires_at,
                       decided_at, decided_by
                FROM governance.approvals
                WHERE id = %s
                """,
                (approval_id,),
            ).fetchone()
        return dict(row) if row else None

    def record_approval_decision(
        self,
        approval_id: str,
        *,
        decided_by: str,
        status: str,
        plan: dict[str, Any] | None = None,
        touches_personal_data: bool | None = None,
    ) -> None:
        with self.connect() as conn:
            if plan is not None and touches_personal_data is not None:
                conn.execute(
                    """
                    UPDATE governance.approvals
                    SET status = %s,
                        decided_at = NOW(),
                        decided_by = array_append(decided_by, %s),
                        plan = %s,
                        touches_personal_data = %s
                    WHERE id = %s
                    """,
                    (
                        status,
                        decided_by,
                        Jsonb(plan),
                        touches_personal_data,
                        approval_id,
                    ),
                )
            elif plan is not None:
                conn.execute(
                    """
                    UPDATE governance.approvals
                    SET status = %s,
                        decided_at = NOW(),
                        decided_by = array_append(decided_by, %s),
                        plan = %s
                    WHERE id = %s
                    """,
                    (status, decided_by, Jsonb(plan), approval_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE governance.approvals
                    SET status = %s,
                        decided_at = NOW(),
                        decided_by = array_append(decided_by, %s)
                    WHERE id = %s
                    """,
                    (status, decided_by, approval_id),
                )
            conn.execute(
                """
                INSERT INTO governance.approval_events (
                    approval_id, actor_slack_id, action, detail
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    approval_id,
                    decided_by,
                    status,
                    Jsonb(
                        {
                            "plan_redacted_personal": bool(
                                plan and plan.get("personal_columns_redacted")
                            )
                        }
                    ),
                ),
            )
            conn.commit()

    def invalidate_approval(self, approval_id: str) -> None:
        """Test/helper: mark approval rejected so re-check fails."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE governance.approvals
                SET status = 'rejected', decided_at = NOW()
                WHERE id = %s
                """,
                (approval_id,),
            )
            conn.commit()

    def expire_stale_approvals(self) -> list[str]:
        """Mark pending/approved rows past expires_at as expired. Returns ids."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                UPDATE governance.approvals
                SET status = 'expired', decided_at = COALESCE(decided_at, NOW())
                WHERE status IN ('pending', 'approved')
                  AND expires_at IS NOT NULL
                  AND expires_at < NOW()
                RETURNING id, requester_slack_id, channel_id, thread_ts
                """
            ).fetchall()
            expired = list(rows)
            for row in expired:
                conn.execute(
                    """
                    INSERT INTO governance.audit_log (event, actor_slack_id, payload)
                    VALUES ('approval_expired', %s, %s)
                    """,
                    (
                        row["requester_slack_id"],
                        Jsonb(
                            {
                                "approval_id": row["id"],
                                "channel_id": row["channel_id"],
                                "thread_ts": row["thread_ts"],
                            }
                        ),
                    ),
                )
            conn.commit()
        return [r["id"] for r in expired]

    def backdate_approval_expiry(self, approval_id: str, *, hours_ago: int = 25) -> None:
        """Test helper: set expires_at in the past (not an approval bypass)."""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE governance.approvals
                SET expires_at = NOW() - (%s || ' hours')::interval
                WHERE id = %s
                """,
                (str(hours_ago), approval_id),
            )
            conn.commit()

    def thread_key(self, channel_id: str, thread_ts: str) -> str:
        return f"{channel_id}:{thread_ts}"

    def save_thread_context(
        self,
        *,
        channel_id: str,
        thread_ts: str,
        requester_slack_id: str,
        context: dict[str, Any],
    ) -> str:
        """Upsert per-DM-thread analysis context (aggregates / summary only)."""
        key = self.thread_key(channel_id, thread_ts)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO governance.thread_context (
                    thread_key, requester_slack_id, channel_id, thread_ts,
                    context, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (thread_key) DO UPDATE SET
                    requester_slack_id = EXCLUDED.requester_slack_id,
                    context = EXCLUDED.context,
                    updated_at = NOW()
                """,
                (
                    key,
                    requester_slack_id,
                    channel_id,
                    thread_ts,
                    Jsonb(context),
                ),
            )
            conn.commit()
        return key

    def get_thread_context(self, thread_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT thread_key, requester_slack_id, channel_id, thread_ts,
                       context, updated_at
                FROM governance.thread_context
                WHERE thread_key = %s
                """,
                (thread_key,),
            ).fetchone()
        return dict(row) if row else None

    def clear_thread_context(self, thread_key: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM governance.thread_context WHERE thread_key = %s",
                (thread_key,),
            )
            conn.commit()

    def checkpointer(self) -> Any:
        """Return LangGraph PostgresSaver — prefer :meth:`open_checkpointer`."""
        from langgraph.checkpoint.postgres import PostgresSaver

        return PostgresSaver.from_conn_string(self.database_url)

    def open_checkpointer(self) -> "CheckpointerHandle":
        """Open a pooled PostgresSaver for the lifetime of the process."""
        return CheckpointerHandle(self.database_url)


class CheckpointerHandle:
    """Owns a psycopg ConnectionPool + PostgresSaver. Call close() on shutdown."""

    def __init__(self, database_url: str) -> None:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(
            conninfo=database_url,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )
        self.checkpointer = PostgresSaver(self.pool)
        self.checkpointer.setup()

    def close(self) -> None:
        self.pool.close()

    def __enter__(self) -> "CheckpointerHandle":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def load_admins_yaml(path: str | Path) -> list[dict[str, str]]:
    doc = yaml.safe_load(Path(path).read_text()) or {}
    admins = doc.get("admins") or []
    return list(admins)
