"""SQL inspection with sqlglot — single read-only SELECT on known catalog objects."""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class CatalogObject:
    schema: str
    table: str
    columns: frozenset[str]
    column_sensitivity: dict[str, str]


@dataclass
class InspectResult:
    ok: bool
    notes: list[str]
    tables: list[tuple[str, str]]
    columns: list[str]
    touches_personal: bool


def inspect_sql(
    sql: str,
    *,
    catalog: dict[tuple[str, str], CatalogObject],
) -> InspectResult:
    """Return ok=False if SQL is not a single allowlisted read-only SELECT."""
    notes: list[str] = []
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as exc:
        return InspectResult(
            ok=False,
            notes=[f"parse error: {exc}"],
            tables=[],
            columns=[],
            touches_personal=False,
        )

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return InspectResult(
            ok=False,
            notes=[f"expected exactly one statement, got {len(statements)}"],
            tables=[],
            columns=[],
            touches_personal=False,
        )

    tree = statements[0]
    if not isinstance(tree, exp.Select):
        # WITH ... SELECT is wrapped; unwrap
        if isinstance(tree, exp.With) and isinstance(tree.this, exp.Select):
            tree = tree.this
        else:
            return InspectResult(
                ok=False,
                notes=[f"only SELECT is allowed, got {type(tree).__name__}"],
                tables=[],
                columns=[],
                touches_personal=False,
            )

    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Create,
        exp.Alter,
        exp.Command,
        exp.Grant,
        exp.TruncateTable,
    )
    for node in tree.walk():
        if isinstance(node, forbidden):
            return InspectResult(
                ok=False,
                notes=[f"forbidden node: {type(node).__name__}"],
                tables=[],
                columns=[],
                touches_personal=False,
            )

    tables: list[tuple[str, str]] = []
    for table in tree.find_all(exp.Table):
        schema = (table.db or "public").lower()
        name = table.name.lower()
        tables.append((schema, name))
        if (schema, name) not in catalog:
            notes.append(f"unknown table: {schema}.{name}")

    if notes:
        return InspectResult(
            ok=False, notes=notes, tables=tables, columns=[], touches_personal=False
        )

    selected_cols: list[str] = []
    touches_personal = False
    star_used = False
    for proj in tree.expressions:
        if isinstance(proj, exp.Star) or (
            isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
        ):
            star_used = True
            continue
        col = proj.find(exp.Column)
        if col is not None:
            selected_cols.append(col.name)

    if star_used:
        # Expand * to all columns of referenced tables for sensitivity / guard.
        for schema, name in tables:
            obj = catalog[(schema, name)]
            selected_cols.extend(sorted(obj.columns))
            notes.append(f"SELECT * expanded for {schema}.{name}")

    for schema, name in tables:
        obj = catalog[(schema, name)]
        for col_name in selected_cols:
            if col_name.lower() not in {c.lower() for c in obj.columns}:
                # Column may belong to another joined table; check any table
                continue
        for col_name in selected_cols:
            sens = None
            for key, cobj in catalog.items():
                if key in tables or key == (schema, name):
                    for c, s in cobj.column_sensitivity.items():
                        if c.lower() == col_name.lower():
                            sens = s
            if sens == "personal":
                touches_personal = True

    # Validate each selected column exists on at least one referenced table
    for col_name in selected_cols:
        found = False
        for schema, name in tables:
            if col_name.lower() in {c.lower() for c in catalog[(schema, name)].columns}:
                found = True
                sens = catalog[(schema, name)].column_sensitivity.get(col_name) or catalog[
                    (schema, name)
                ].column_sensitivity.get(col_name.lower())
                # try case-insensitive sensitivity lookup
                if sens is None:
                    for c, s in catalog[(schema, name)].column_sensitivity.items():
                        if c.lower() == col_name.lower():
                            sens = s
                            break
                if sens == "personal":
                    touches_personal = True
                break
        if not found and selected_cols:
            # Allow expressions without bare columns
            if any(
                isinstance(proj, (exp.Alias, exp.Count, exp.Sum, exp.Avg, exp.Max, exp.Min))
                for proj in tree.expressions
            ):
                continue
            notes.append(f"unknown column: {col_name}")

    # Soften: if only aggregates, columns list may be empty — ok
    unknown_cols = [n for n in notes if n.startswith("unknown column")]
    if unknown_cols and not star_used:
        # Re-check more carefully — drop false positives for qualified names
        notes = [n for n in notes if not n.startswith("unknown column")]
        for col_name in selected_cols:
            found = any(
                col_name.lower() in {c.lower() for c in catalog[t].columns} for t in tables
            )
            if not found:
                notes.append(f"unknown column: {col_name}")

    ok = not any(n.startswith("unknown") for n in notes)
    if not ok:
        return InspectResult(
            ok=False,
            notes=notes,
            tables=tables,
            columns=selected_cols,
            touches_personal=touches_personal,
        )

    return InspectResult(
        ok=True,
        notes=notes or ["inspection passed"],
        tables=tables,
        columns=selected_cols,
        touches_personal=touches_personal,
    )


def load_catalog_objects(gov) -> dict[tuple[str, str], CatalogObject]:
    """Build allowlist from governance catalog tables."""
    out: dict[tuple[str, str], CatalogObject] = {}
    with gov.connect() as conn:
        datasets = conn.execute(
            """
            SELECT name, table_schema, table_name
            FROM governance.datasets
            """
        ).fetchall()
        for ds in datasets:
            cols = conn.execute(
                """
                SELECT name, sensitivity
                FROM governance.columns
                WHERE dataset_name = %s
                """,
                (ds["name"],),
            ).fetchall()
            schema = ds["table_schema"].lower()
            table = ds["table_name"].lower()
            out[(schema, table)] = CatalogObject(
                schema=schema,
                table=table,
                columns=frozenset(c["name"] for c in cols),
                column_sensitivity={c["name"]: c["sensitivity"] for c in cols},
            )
    return out
