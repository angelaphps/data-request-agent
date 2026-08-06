"""Seed admins into the local governance database.

Semantic meanings live only in ``semantic_layer/`` YAML at runtime — they are
not copied into governance tables. Business/tabular rows live in the business
store (MVP: dummy Postgres via ``BUSINESS_DATABASE_URL``) and are not copied here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_request_agent.catalog import get_semantic_catalog
from data_request_agent.config import Settings
from data_request_agent.governance import Governance, load_admins_yaml


def seed_admins(gov: Governance, admins_path: Path) -> int:
    admins = load_admins_yaml(admins_path)
    return gov.upsert_admins(admins)


def probe_business(settings: Settings) -> dict:
    """Read-only connectivity check against the business database."""
    import psycopg
    from psycopg.rows import dict_row

    url = settings.query_database_url
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=15) as conn:
        users = conn.execute("SELECT COUNT(*) AS n FROM public.users").fetchone()["n"]
        payments = conn.execute(
            "SELECT COUNT(*) AS n FROM public.payments"
        ).fetchone()["n"]
    return {
        "users": users,
        "payments": payments,
        "url_host": url.split("@")[-1].split("/")[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned seed steps without writing",
    )
    parser.add_argument(
        "--skip-business-probe",
        action="store_true",
        help="Do not connect to BUSINESS_DATABASE_URL",
    )
    args = parser.parse_args()
    settings = Settings()
    catalog = get_semantic_catalog(ROOT / settings.semantic_layer_dir)
    if args.dry_run:
        print(
            {
                "governance": settings.database_url.split("@")[-1],
                "business": (settings.business_database_url or "").split("@")[-1],
                "admins_yaml": settings.admins_yaml_path,
                "semantic_layer_dir": settings.semantic_layer_dir,
                "yaml_datasets": len(catalog.datasets),
                "yaml_metrics": len(catalog.metrics),
                "steps": ["admins", "validate_yaml_catalog", "business_probe"],
            }
        )
        return

    gov = Governance(settings.database_url)
    n_admins = seed_admins(gov, ROOT / settings.admins_yaml_path)
    business = None
    if not args.skip_business_probe:
        if not settings.business_database_url and not settings.readonly_database_url:
            raise SystemExit(
                "BUSINESS_DATABASE_URL (or READONLY_DATABASE_URL) must be set"
            )
        business = probe_business(settings)
    gov.audit(
        "seed_completed",
        {
            "admins": n_admins,
            "catalog_source": "semantic_layer_yaml",
            "datasets": len(catalog.datasets),
            "metrics": len(catalog.metrics),
            "business_probe": business,
        },
    )
    print(
        f"seeded admins={n_admins} "
        f"(YAML catalog datasets={len(catalog.datasets)} "
        f"metrics={len(catalog.metrics)}; not written to DB) "
        f"business={business}"
    )


if __name__ == "__main__":
    main()
