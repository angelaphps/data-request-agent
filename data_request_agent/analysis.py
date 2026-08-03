"""Stage 3 analysis — LM proposes steps from descriptions only; code runs them.

Invariant: no data rows in any language-model context. The model sees column
names, dtypes, and catalog descriptions. Aggregates for the narrative are
computed by the restricted runner on the already-guarded frame.
"""

from __future__ import annotations

import io
import logging
import os
from decimal import Decimal
from typing import Any, Callable, Literal

import pandas as pd
from pydantic import BaseModel, Field

from data_request_agent.governance import Governance

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM = """
You design a small analysis plan for an internal Slack analytics bot.

You receive ONLY column names, dtypes, and catalog descriptions — never data
rows or sample values. Do not invent columns.

Choose a chart and aggregations that answer the user's question using the
available columns. Prefer bar or line charts. Use chart_type=none when a
tiny summary table is enough.

groupby_column / x_column / y_column must be names from the schema list (or
null). aggregation applies to y_column when grouping.
""".strip()


class AnalysisPlan(BaseModel):
    """Schema-only proposal — executed by run_analysis on a guarded DataFrame."""

    title: str = ""
    chart_type: Literal["none", "bar", "line"] = "bar"
    groupby_column: str | None = None
    x_column: str | None = None
    y_column: str | None = None
    aggregation: Literal["sum", "mean", "count", "min", "max"] = "mean"
    sort_descending: bool = True
    notes: str = ""


class AnalysisResult(BaseModel):
    answer: str
    table_markdown: str
    table_rows: list[dict[str, Any]] = Field(default_factory=list)
    chart_png: bytes | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    plan: AnalysisPlan | None = None
    context_had_no_rows: bool = True


def schema_slice_from_governance(
    gov: Governance,
    *,
    column_names: list[str],
    dtypes: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Description-only schema for columns present in the guarded result."""
    wanted = {c.lower(): c for c in column_names}
    dtypes = dtypes or {}
    out: list[dict[str, str]] = []
    with gov.connect() as conn:
        rows = conn.execute(
            """
            SELECT c.name, c.description, c.sensitivity, c.data_type, d.name AS dataset
            FROM governance.columns c
            JOIN governance.datasets d ON d.name = c.dataset_name
            ORDER BY d.name, c.name
            """
        ).fetchall()
    seen: set[str] = set()
    for row in rows:
        key = row["name"].lower()
        if key not in wanted or key in seen:
            continue
        seen.add(key)
        original = wanted[key]
        out.append(
            {
                "name": original,
                "description": row["description"] or "",
                "sensitivity": row["sensitivity"] or "none",
                "dtype": dtypes.get(original) or dtypes.get(key) or row["data_type"] or "",
                "dataset": row["dataset"] or "",
            }
        )
    for name in column_names:
        if name.lower() not in seen:
            out.append(
                {
                    "name": name,
                    "description": "(no catalog description)",
                    "sensitivity": "none",
                    "dtype": dtypes.get(name) or "",
                    "dataset": "",
                }
            )
    return out


def build_analysis_prompt(
    *,
    ask: str,
    schema_slice: list[dict[str, str]],
) -> str:
    """Build the LM prompt. Must never include data rows."""
    lines = [
        "User question:",
        ask.strip() or "(unspecified analysis)",
        "",
        "Available columns (descriptions only — no data rows):",
    ]
    for col in schema_slice:
        lines.append(
            f"- {col['name']} (dtype={col.get('dtype') or '?'}, "
            f"sensitivity={col.get('sensitivity')}, dataset={col.get('dataset')}): "
            f"{col.get('description') or ''}"
        )
    lines.append("")
    lines.append("Propose one AnalysisPlan that answers the question.")
    return "\n".join(lines)


def assert_prompt_has_no_data_rows(
    prompt: str,
    frame: pd.DataFrame,
    *,
    schema_slice: list[dict[str, str]] | None = None,
) -> None:
    """Fail if cell values from the frame appear in the prompt (except catalog text)."""
    if frame.empty:
        return
    allowed = ""
    if schema_slice:
        allowed = " ".join(
            f"{c.get('name','')} {c.get('description','')}" for c in schema_slice
        )
    sample = frame.head(50)
    for col in sample.columns:
        for val in sample[col].tolist():
            if val is None or (isinstance(val, float) and pd.isna(val)):
                continue
            text = str(val).strip()
            if len(text) < 4:
                continue
            if text.lower() in {c.lower() for c in frame.columns}:
                continue
            if text in allowed:
                continue
            if text in prompt:
                raise AssertionError(
                    f"analysis prompt leaked data value {text!r} from column {col}"
                )


def make_llm_analysis_planner(
    *,
    model: str = "openai:gpt-4o-mini",
) -> Callable[..., AnalysisPlan]:
    def plan_analysis(
        ask: str,
        schema_slice: list[dict[str, str]],
        *,
        frame_for_leak_check: pd.DataFrame | None = None,
    ) -> AnalysisPlan:
        from pydantic_ai import Agent

        prompt = build_analysis_prompt(ask=ask, schema_slice=schema_slice)
        if frame_for_leak_check is not None:
            assert_prompt_has_no_data_rows(
                prompt, frame_for_leak_check, schema_slice=schema_slice
            )
        agent = Agent(model, output_type=AnalysisPlan, system_prompt=ANALYSIS_SYSTEM)
        result = agent.run_sync(prompt)
        return result.output

    return plan_analysis


def heuristic_analysis_plan(
    ask: str,
    schema_slice: list[dict[str, str]],
    **_: Any,
) -> AnalysisPlan:
    """Deterministic planner for tests (no API)."""
    names = [c["name"] for c in schema_slice]
    lower = {n.lower(): n for n in names}
    y = None
    for cand in (
        "avg_duration_minutes",
        "duration_minutes",
        "amount_usd",
        "total_revenue_usd",
        "dau",
        "user_count",
        "max_duration_minutes",
    ):
        if cand in lower:
            y = lower[cand]
            break
    if y is None:
        for c in schema_slice:
            if c.get("dtype", "").startswith(("int", "float")):
                y = c["name"]
                break
    x = None
    for cand in ("device_type", "country", "activity_type", "plan", "date", "user_id"):
        if cand in lower and lower[cand] != y:
            x = lower[cand]
            break
    if x is None:
        for n in names:
            if n != y:
                x = n
                break
    return AnalysisPlan(
        title="Summary",
        chart_type="bar" if x and y else "none",
        groupby_column=x,
        x_column=x,
        y_column=y,
        aggregation="mean" if y and "duration" in (y or "").lower() else "sum",
        notes=ask[:120],
    )


def run_analysis(
    frame: pd.DataFrame,
    plan: AnalysisPlan,
    *,
    max_table_rows: int = 20,
) -> AnalysisResult:
    """Execute an AnalysisPlan on a guarded frame; return Slack-ready artifacts."""
    if frame is None or frame.empty:
        return AnalysisResult(
            answer="No rows were available to analyse after the guard.",
            table_markdown="_No rows._",
            stats={"row_count": 0},
            plan=plan,
        )

    df = frame.copy()
    cols = {c.lower(): c for c in df.columns}

    def resolve(name: str | None) -> str | None:
        if not name:
            return None
        return cols.get(name.lower())

    group_col = resolve(plan.groupby_column or plan.x_column)
    y_col = resolve(plan.y_column)
    agg = plan.aggregation

    summary = df
    chart_df = None
    stats: dict[str, Any] = {"row_count": int(len(df)), "aggregation": agg}

    # SQL already returned one row per category — chart labels as-is.
    if (
        group_col
        and y_col
        and group_col in df.columns
        and y_col in df.columns
        and int(df[group_col].nunique(dropna=False)) == len(df)
        and len(df) <= max_table_rows
    ):
        ordered = df.sort_values(
            y_col,
            ascending=not plan.sort_descending,
            key=lambda s: pd.to_numeric(s, errors="coerce"),
        )
        summary = ordered
        chart_df = ordered[[group_col, y_col]].copy()
        stats["groups"] = int(len(ordered))
        stats["value_column"] = y_col
        stats["top"] = _records_jsonable(ordered.head(5).to_dict(orient="records"))
        stats["preaggregated"] = True
    elif group_col and y_col and y_col in df.columns and group_col in df.columns:
        how = {"sum": "sum", "mean": "mean", "count": "count", "min": "min", "max": "max"}[
            agg
        ]
        if how == "count":
            grouped = (
                df.groupby(group_col, dropna=False)[y_col]
                .count()
                .reset_index(name="count")
            )
            value_col = "count"
        else:
            grouped = (
                df.groupby(group_col, dropna=False)[y_col]
                .agg(how)
                .reset_index(name=f"{how}_{y_col}")
            )
            value_col = f"{how}_{y_col}"
        grouped = grouped.sort_values(value_col, ascending=not plan.sort_descending)
        summary = grouped
        chart_df = grouped
        stats["groups"] = int(len(grouped))
        stats["value_column"] = value_col
        stats["top"] = _records_jsonable(grouped.head(5).to_dict(orient="records"))
    elif y_col and y_col in df.columns:
        series = pd.to_numeric(df[y_col], errors="coerce")
        stats["y_column"] = y_col
        stats["min"] = _jsonable(series.min())
        stats["max"] = _jsonable(series.max())
        stats["mean"] = _jsonable(series.mean())
        summary = df.head(max_table_rows)
        stats["note"] = (
            "measure only — group/category column was hidden (personal-data guard), "
            "so no bar/line chart was drawn"
        )
    else:
        summary = df.head(max_table_rows)
        stats["note"] = "showed head of result; no clear measure column"

    table = summary.head(max_table_rows)
    table_rows = table.where(pd.notnull(table), None).to_dict(orient="records")
    table_md = _markdown_table(table)
    chart_png = None
    if plan.chart_type != "none" and chart_df is not None and len(chart_df) > 0:
        chart_png = _render_chart(
            chart_df,
            chart_type=plan.chart_type,
            x_col=group_col or chart_df.columns[0],
            y_col=stats.get("value_column") or chart_df.columns[-1],
            title=plan.title or "Analysis",
        )

    answer = _narrative(plan, stats, y_col=y_col, group_col=group_col)
    return AnalysisResult(
        answer=answer,
        table_markdown=table_md,
        table_rows=table_rows,
        chart_png=chart_png,
        stats=stats,
        plan=plan,
        context_had_no_rows=True,
    )


def _narrative(
    plan: AnalysisPlan,
    stats: dict[str, Any],
    *,
    y_col: str | None,
    group_col: str | None,
) -> str:
    bits = [plan.title.strip() or "Analysis results"]
    if stats.get("top") and group_col:
        top = stats["top"][0]
        bits.append(
            f"Top group by {stats.get('value_column')}: "
            f"{top.get(group_col)} → {top.get(stats.get('value_column'))}."
        )
        if len(stats["top"]) > 1:
            bits.append(f"Showing {min(5, len(stats['top']))} leading groups.")
    elif y_col and "mean" in stats:
        bits.append(
            f"{y_col}: min={stats.get('min')}, mean={stats.get('mean')}, "
            f"max={stats.get('max')} (n={stats.get('row_count')})."
        )
    else:
        bits.append(f"Summarised {stats.get('row_count', 0)} guarded rows.")
    if plan.notes:
        bits.append(plan.notes)
    return " ".join(bits)


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    try:
        return df.to_markdown(index=False)
    except Exception:  # noqa: BLE001 — tabulate optional
        cols = list(df.columns)
        lines = ["| " + " | ".join(str(c) for c in cols) + " |",
                 "| " + " | ".join("---" for _ in cols) + " |"]
        for _, row in df.iterrows():
            lines.append("| " + " | ".join(str(row[c]) for c in cols) + " |")
        return "\n".join(lines)


def _render_chart(
    df: pd.DataFrame,
    *,
    chart_type: str,
    x_col: str,
    y_col: str,
    title: str,
) -> bytes | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        logger.exception("matplotlib unavailable")
        return None

    fig, ax = plt.subplots(figsize=(8, 4.5))
    xs = df[x_col].astype(str).tolist()
    ys = pd.to_numeric(df[y_col], errors="coerce").fillna(0).tolist()
    if chart_type == "line":
        ax.plot(xs, ys, marker="o")
    else:
        ax.bar(xs, ys)
    ax.set_title(title)
    ax.set_xlabel(x_col)
    ax.set_ylabel(y_col)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _jsonable(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return str(value)
    return value


def _records_jsonable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _jsonable(v) for k, v in row.items()} for row in rows]


def build_live_analysis_planner(*, settings) -> Callable[..., AnalysisPlan]:
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY unset — using heuristic analysis planner")
        return heuristic_analysis_plan
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    return make_llm_analysis_planner()
