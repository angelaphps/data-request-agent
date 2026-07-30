"""Tunables and environment settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Governance DB (admins, catalog, approvals, audit, checkpoints)
    database_url: str = "postgresql://test:test@localhost:5432/data_request_agent"

    # Business tabular store — MVP: dummy Postgres URL. Post-MVP: BigQuery
    # via BigQueryStore (TabularStore seam); this URL stays for local/demo.
    business_database_url: str | None = None
    readonly_database_url: str | None = None

    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_signing_secret: str = ""
    admin_channel_id: str = ""
    openai_api_key: str = ""

    row_cap: int = 10_000
    statement_timeout_ms: int = 30_000
    result_expiry_hours: int = 72
    # Small team: leave / illness — approvals stay actionable for two work days.
    approval_expiry_hours: int = 48
    approval_group_size: int = 3
    max_planner_retries: int = 3
    max_clarify_questions: int = 2
    # Analysis reply (Stage 3): inline Slack markdown preview only — not the full extract.
    # Chart/answer may use more rows from the guarded result (up to row_cap).
    analysis_summary_max_rows: int = 20

    semantic_layer_dir: str = "semantic_layer"
    admins_yaml_path: str = "config/admins.yaml"

    @property
    def query_database_url(self) -> str:
        """URL used for business query execution.

        Preference: explicit read-only URL, else business URL, else governance
        URL (local-only demos).
        """
        return (
            self.readonly_database_url
            or self.business_database_url
            or self.database_url
        )
