"""Normalize authored semantic-layer YAML into a consistent shape.

Supports both the original agent format (name/tables/columns) and a richer
relation-aware format (table/fields with related_table, measures, golden_queries).
"""

from __future__ import annotations

from typing import Any


def normalize_dataset_doc(data: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized dataset dict for governance seeding."""
    name = data.get("name") or data.get("table")
    if not name:
        raise ValueError("dataset YAML needs name or table")

    if data.get("tables"):
        table0 = (data.get("tables") or [{}])[0]
        table_schema = table0.get("schema", "public")
        table_name = table0.get("name", name)
    else:
        table_schema = data.get("schema", "public")
        table_name = data.get("table") or name

    raw_fields = data.get("fields") or data.get("columns") or []
    columns: list[dict[str, Any]] = []
    relationships: list[dict[str, str]] = []
    for col in raw_fields:
        columns.append(
            {
                "name": col["name"],
                "description": (col.get("description") or "").strip(),
                "sensitivity": col.get("sensitivity")
                or _default_sensitivity(col.get("role")),
                "data_type": col.get("type") or col.get("data_type"),
                "role": col.get("role"),
            }
        )
        related_table = col.get("related_table")
        related_field = col.get("related_field") or "user_id"
        if related_table or col.get("role") == "relationship":
            if not related_table:
                raise ValueError(
                    f"{name}.{col['name']}: relationship role needs related_table"
                )
            relationships.append(
                {
                    "from_dataset": name,
                    "from_column": col["name"],
                    "to_dataset": related_table,
                    "to_column": related_field,
                    "description": (
                        col.get("description")
                        or f"{name}.{col['name']} → {related_table}.{related_field}"
                    ),
                }
            )

    measures = []
    for m in data.get("measures") or []:
        measures.append(
            {
                "name": m["name"],
                "definition": (m.get("description") or m.get("definition") or "").strip(),
                "expression": m.get("formula") or m.get("expression") or "",
                "dataset": name,
            }
        )

    golden = []
    for g in data.get("golden_queries") or []:
        golden.append(
            {
                "name": g["name"],
                "dataset_name": name,
                "description": (g.get("description") or "").strip(),
                "sql": (g.get("sql") or "").strip().rstrip(";"),
            }
        )

    return {
        "name": name,
        "description": (data.get("description") or "").strip(),
        "owner": data.get("owner"),
        "sensitivity": data.get("sensitivity", "none"),
        "table_schema": table_schema,
        "table_name": table_name,
        "columns": columns,
        "relationships": relationships,
        "measures": measures,
        "golden_queries": golden,
    }


def _default_sensitivity(role: str | None) -> str:
    if role == "measure":
        return "internal"
    return "none"
