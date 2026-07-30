"""Governance database: admins · approvals · catalog · audit · checkpointer seam."""

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
        with self.connect() as conn:
            dataset = conn.execute(
                """
                SELECT name, description, owner, sensitivity, table_schema, table_name
                FROM governance.datasets
                WHERE name = %s
                """,
                (name,),
            ).fetchone()
            if dataset is None:
                return None
            columns = conn.execute(
                """
                SELECT name, description, sensitivity
                FROM governance.columns
                WHERE dataset_name = %s
                ORDER BY name
                """,
                (name,),
            ).fetchall()
        result = dict(dataset)
        result["columns"] = list(columns)
        return result

    def get_metric(self, name: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT name, definition, expression, dataset_name
                FROM governance.metrics
                WHERE name = %s
                """,
                (name,),
            ).fetchone()
        return dict(row) if row else None

    def list_metric_names(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM governance.metrics ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]

    def load_catalog_from_yaml(self, semantic_layer_dir: str | Path) -> dict[str, Any]:
        """Load semantic_layer YAML into governance catalog tables.

        Runtime lookups use the tables; the YAML files are the authored source.
        Accepts original (columns/tables) and richer (fields/relations/measures/
        golden_queries) dataset YAML.
        """
        from data_request_agent.catalog_yaml import normalize_dataset_doc

        root = Path(semantic_layer_dir)
        datasets_dir = root / "datasets"
        metrics_path = root / "metrics.yaml"

        payload: dict[str, Any] = {
            "datasets": [],
            "metrics": [],
            "relationships": [],
            "golden_queries": [],
        }
        files_blob = ""

        with self.connect() as conn:
            # Full replace so removed YAML files do not linger in the tables.
            conn.execute("DELETE FROM governance.golden_queries")
            conn.execute("DELETE FROM governance.relationships")
            conn.execute("DELETE FROM governance.metrics")
            conn.execute("DELETE FROM governance.columns")
            conn.execute("DELETE FROM governance.datasets")

            for path in sorted(datasets_dir.glob("*.yaml")):
                raw = path.read_text()
                files_blob += raw
                data = yaml.safe_load(raw) or {}
                norm = normalize_dataset_doc(data)
                payload["datasets"].append(norm)
                conn.execute(
                    """
                    INSERT INTO governance.datasets (
                        name, description, owner, sensitivity, table_schema, table_name
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        norm["name"],
                        norm["description"],
                        norm.get("owner"),
                        norm.get("sensitivity", "none"),
                        norm["table_schema"],
                        norm["table_name"],
                    ),
                )
                for col in norm["columns"]:
                    conn.execute(
                        """
                        INSERT INTO governance.columns (
                            dataset_name, name, description, sensitivity,
                            data_type, role
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            norm["name"],
                            col["name"],
                            col.get("description", ""),
                            col.get("sensitivity", "none"),
                            col.get("data_type"),
                            col.get("role"),
                        ),
                    )
                for rel in norm["relationships"]:
                    payload["relationships"].append(rel)
                    conn.execute(
                        """
                        INSERT INTO governance.relationships (
                            from_dataset, from_column, to_dataset, to_column, description
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            rel["from_dataset"],
                            rel["from_column"],
                            rel["to_dataset"],
                            rel["to_column"],
                            rel.get("description", ""),
                        ),
                    )
                for measure in norm["measures"]:
                    payload["metrics"].append(measure)
                    conn.execute(
                        """
                        INSERT INTO governance.metrics (
                            name, definition, expression, dataset_name
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (name) DO UPDATE SET
                            definition = EXCLUDED.definition,
                            expression = EXCLUDED.expression,
                            dataset_name = EXCLUDED.dataset_name
                        """,
                        (
                            measure["name"],
                            measure["definition"],
                            measure["expression"],
                            measure["dataset"],
                        ),
                    )
                for gq in norm["golden_queries"]:
                    payload["golden_queries"].append(gq)
                    conn.execute(
                        """
                        INSERT INTO governance.golden_queries (
                            name, dataset_name, description, sql
                        )
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            gq["name"],
                            gq["dataset_name"],
                            gq["description"],
                            gq["sql"],
                        ),
                    )

            if metrics_path.exists():
                raw = metrics_path.read_text()
                files_blob += raw
                metrics_doc = yaml.safe_load(raw) or {}
                metrics = metrics_doc.get("metrics") or []
                for metric in metrics:
                    payload["metrics"].append(metric)
                    conn.execute(
                        """
                        INSERT INTO governance.metrics (
                            name, definition, expression, dataset_name
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (name) DO UPDATE SET
                            definition = EXCLUDED.definition,
                            expression = EXCLUDED.expression,
                            dataset_name = EXCLUDED.dataset_name
                        """,
                        (
                            metric["name"],
                            metric["definition"],
                            metric["expression"],
                            metric["dataset"],
                        ),
                    )

            checksum = hashlib.sha256(files_blob.encode()).hexdigest()
            conn.execute(
                """
                INSERT INTO governance.catalog_versions (source_path, checksum, payload)
                VALUES (%s, %s, %s)
                """,
                (str(root), checksum, Jsonb(payload)),
            )
            conn.commit()

        return {
            "checksum": checksum,
            "dataset_count": len(payload["datasets"]),
            "metric_count": len(payload["metrics"]),
            "relationship_count": len(payload["relationships"]),
            "golden_query_count": len(payload["golden_queries"]),
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
