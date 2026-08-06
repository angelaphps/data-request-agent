"""YAML semantic layer — single runtime source of truth for meanings.

Reads ``semantic_layer/`` only. Catalog tables in the governance DB are not
used. Cross-datastore catalog moves can revisit a DB layer later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from data_request_agent.catalog_yaml import normalize_dataset_doc
from data_request_agent.sql_inspect import CatalogObject


@dataclass
class SemanticCatalog:
    """In-memory catalog loaded from authored YAML."""

    root: Path
    datasets: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, str]] = field(default_factory=list)
    golden_queries: list[dict[str, str]] = field(default_factory=list)

    def metric_names(self) -> list[str]:
        return sorted({m["name"] for m in self.metrics})

    def get_dataset(self, name: str) -> dict[str, Any] | None:
        for ds in self.datasets:
            if ds["name"] == name:
                return ds
        return None

    def get_metric(self, name: str) -> dict[str, Any] | None:
        for m in self.metrics:
            if m["name"] == name:
                return m
        return None

    def personal_column_names(self) -> set[str]:
        names: set[str] = set()
        for ds in self.datasets:
            for col in ds.get("columns") or []:
                if (col.get("sensitivity") or "none") == "personal":
                    names.add(str(col["name"]).lower())
        return names

    def catalog_objects(self) -> dict[tuple[str, str], CatalogObject]:
        out: dict[tuple[str, str], CatalogObject] = {}
        for ds in self.datasets:
            schema = str(ds["table_schema"]).lower()
            table = str(ds["table_name"]).lower()
            cols = ds.get("columns") or []
            out[(schema, table)] = CatalogObject(
                schema=schema,
                table=table,
                columns=frozenset(c["name"] for c in cols),
                column_sensitivity={
                    c["name"]: c.get("sensitivity") or "none" for c in cols
                },
            )
        return out

    def catalog_brief_text(self) -> str:
        """Description-only brief for scope/SQL LLMs (never data rows)."""
        lines: list[str] = []
        if self.relationships:
            lines.append("JOIN KEYS (use these — do not invent relationships):")
            for r in self.relationships:
                lines.append(
                    f"  - {r['from_dataset']}.{r['from_column']} → "
                    f"{r['to_dataset']}.{r['to_column']}"
                    + (f" — {r['description']}" if r.get("description") else "")
                )
            lines.append("")

        if self.golden_queries:
            lines.append(
                "GOLDEN QUERY EXAMPLES (patterns only — adapt; still inspect/trial):"
            )
            for g in self.golden_queries:
                lines.append(
                    f"  - {g['name']} ({g['dataset_name']}): {g['description']}"
                )
                lines.append(f"    SQL: {g['sql']}")
            lines.append("")

        for ds in sorted(self.datasets, key=lambda d: d["name"]):
            lines.append(
                f"Dataset {ds['name']} ({ds['table_schema']}.{ds['table_name']}, "
                f"sensitivity={ds.get('sensitivity', 'none')}): "
                f"{ds.get('description')}"
            )
            for c in ds.get("columns") or []:
                extra = []
                if c.get("data_type"):
                    extra.append(str(c["data_type"]))
                if c.get("role"):
                    extra.append(f"role={c['role']}")
                extra.append(f"sens={c.get('sensitivity', 'none')}")
                lines.append(
                    f"  - column {c['name']} [{', '.join(extra)}]: "
                    f"{c.get('description') or ''}"
                )

        lines.append("Metrics / measures:")
        for m in sorted(self.metrics, key=lambda x: x["name"]):
            expr = f" | {m['expression']}" if m.get("expression") else ""
            lines.append(
                f"  - {m['name']} "
                f"(dataset={m.get('dataset_name') or m.get('dataset')}): "
                f"{m.get('definition') or ''}{expr}"
            )
        return "\n".join(lines)

    def schema_slice(
        self,
        *,
        column_names: list[str],
        dtypes: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """Description-only schema for columns present in a guarded result."""
        wanted = {c.lower(): c for c in column_names}
        dtypes = dtypes or {}
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for ds in self.datasets:
            for col in ds.get("columns") or []:
                key = str(col["name"]).lower()
                if key not in wanted or key in seen:
                    continue
                seen.add(key)
                original = wanted[key]
                out.append(
                    {
                        "name": original,
                        "description": col.get("description") or "",
                        "sensitivity": col.get("sensitivity") or "none",
                        "dtype": (
                            dtypes.get(original)
                            or dtypes.get(key)
                            or col.get("data_type")
                            or ""
                        ),
                        "dataset": ds["name"],
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


def load_semantic_catalog(semantic_layer_dir: str | Path) -> SemanticCatalog:
    """Load datasets + metrics YAML into an in-memory catalog."""
    root = Path(semantic_layer_dir)
    if not root.is_absolute():
        pkg_root = Path(__file__).resolve().parents[1]
        candidate = pkg_root / root
        root = candidate if candidate.exists() else Path.cwd() / root

    catalog = SemanticCatalog(root=root)
    datasets_dir = root / "datasets"
    if datasets_dir.is_dir():
        for path in sorted(datasets_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text()) or {}
            norm = normalize_dataset_doc(data)
            catalog.datasets.append(norm)
            catalog.relationships.extend(norm.get("relationships") or [])
            catalog.golden_queries.extend(norm.get("golden_queries") or [])
            for m in norm.get("measures") or []:
                catalog.metrics.append(
                    {
                        "name": m["name"],
                        "definition": m.get("definition") or "",
                        "expression": m.get("expression") or "",
                        "dataset_name": m.get("dataset") or norm["name"],
                    }
                )

    metrics_path = root / "metrics.yaml"
    if metrics_path.exists():
        doc = yaml.safe_load(metrics_path.read_text()) or {}
        for m in doc.get("metrics") or []:
            catalog.metrics.append(
                {
                    "name": m["name"],
                    "definition": (m.get("definition") or "").strip(),
                    "expression": m.get("expression") or "",
                    "dataset_name": m.get("dataset") or "",
                }
            )
    return catalog


@lru_cache(maxsize=4)
def _cached_catalog(resolved_path: str, mtime_key: float) -> SemanticCatalog:
    return load_semantic_catalog(resolved_path)


def get_semantic_catalog(
    semantic_layer_dir: str | Path | None = None,
) -> SemanticCatalog:
    """Return catalog for ``semantic_layer_dir`` (cached; reloads on mtime)."""
    from data_request_agent.config import Settings

    raw = Path(semantic_layer_dir or Settings().semantic_layer_dir)
    if not raw.is_absolute():
        pkg_root = Path(__file__).resolve().parents[1]
        candidate = pkg_root / raw
        root = candidate if candidate.exists() else Path.cwd() / raw
    else:
        root = raw
    mtime = 0.0
    if root.exists():
        mtimes = [root.stat().st_mtime]
        ds = root / "datasets"
        if ds.is_dir():
            mtimes.extend(p.stat().st_mtime for p in ds.glob("*.yaml"))
        metrics = root / "metrics.yaml"
        if metrics.exists():
            mtimes.append(metrics.stat().st_mtime)
        mtime = max(mtimes) if mtimes else 0.0
    return _cached_catalog(str(root.resolve()), mtime)
