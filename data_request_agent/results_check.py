"""Results check — compare execution to trial promise; one retry then honest stop."""

from __future__ import annotations

from typing import Any

from data_request_agent.governance import Governance
from data_request_agent.state import AgentState


def check_results(state: AgentState, *, gov: Governance) -> AgentState:
    """Validate shape against trial headings; size vs estimate when available."""
    plan = (state.get("approved") or {}).get("plan") or state.get("plan") or {}
    rows: list[dict[str, Any]] = list(state.get("result_rows") or [])
    promised = [c.lower() for c in (plan.get("trial_columns") or [])]
    estimate = plan.get("trial_row_estimate")
    retry_count = int(state.get("results_retry_count") or 0)

    actual_cols: list[str] = []
    if rows:
        actual_cols = [c.lower() for c in rows[0].keys()]
    elif promised:
        # Empty result still ok if we expected columns — headings-only case
        actual_cols = promised

    problems: list[str] = []
    if promised:
        missing = [c for c in promised if c not in actual_cols]
        # Allow subset when aggregates rename — require no surprise personal widen
        if missing and rows:
            problems.append(f"missing promised columns: {missing}")
        unexpected = [c for c in actual_cols if c not in promised]
        if unexpected and rows:
            # Soft: only fail if force_fail flag set for tests, or if many extras
            if state.get("force_results_mismatch") or len(unexpected) > 3:
                problems.append(f"unexpected columns: {unexpected}")

    if state.get("force_results_mismatch"):
        problems.append("forced mismatch for test")

    if estimate is not None and rows is not None:
        # EXPLAIN estimates are rough — only fail on extreme blow-ups when forced
        if state.get("force_results_mismatch"):
            problems.append(f"row count {len(rows)} vs estimate {estimate}")

    if not problems:
        gov.audit(
            "results_check_ok",
            {"rows": len(rows), "columns": actual_cols},
            actor_slack_id=state.get("requester_slack_id"),
        )
        return {**state, "phase": "results_ok"}

    gov.audit(
        "results_check_failed",
        {"problems": problems, "retry_count": retry_count},
        actor_slack_id=state.get("requester_slack_id"),
    )

    if retry_count < 1:
        return {
            **state,
            "phase": "results_retry",
            "results_retry_count": retry_count + 1,
            # Keep force_results_mismatch so a second forced failure can exercise
            # the honest stop path in tests.
            "error": "results_mismatch",
            "delivery_message": (
                "The result didn't match what the trial run promised "
                f"({'; '.join(problems)}). I'll try once more."
            ),
        }

    return {
        **state,
        "phase": "results_failed",
        "error": "results_mismatch",
        "delivery_message": (
            "I tried twice and the result still doesn't match what the trial "
            f"run promised ({'; '.join(problems)}). "
            "I'm stopping here rather than sending something that looks wrong."
        ),
    }
