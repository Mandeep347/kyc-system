import csv
import hashlib
import io
import json
import platform
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

from app.core.database import Base
import app.models  # noqa: F401  (registers the tables)
from generator.config import GenConfig
from generator.loader import LOAD_ORDER

GT_COLUMNS = ["ground_truth_id", "issue_type", "entity_type", "entity_id",
              "customer_id", "expected_detection", "details"]
DECISION_COLUMNS = ["customer_id", "expected_kyc_status", "mandatory_total",
                    "mandatory_satisfied", "completeness_pct", "missing_categories"]
INSERT_BATCH = 1000

SOURCE_MANIFEST = {
    "external_data_used": False,
    "sources": [],
    "provenance": ("All records are synthetic, produced by `python -m generator` with Faker "
                   "(locale en_IN) and Python's random module. No real people, companies or "
                   "documents. No public dataset was downloaded or redistributed."),
    "email_domain": "example.com (reserved by IETF, no real mailboxes)",
}


# ---------- value formatting ----------

def _cell(v) -> str:
    """One CSV cell. Order matters: bool before int, datetime before date."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, list):
        return "{" + ",".join(str(x) for x in v) + "}"      # Postgres array literal
    if isinstance(v, dict):
        return json.dumps(v, sort_keys=True, separators=(",", ":"))
    return str(v)


def _json_default(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    raise TypeError(f"cannot serialise {type(v)}")


def _sql(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, datetime):
        return "'" + v.isoformat(sep=" ") + "'"
    if isinstance(v, date):
        return "'" + v.isoformat() + "'"
    if isinstance(v, list):
        return "ARRAY[" + ",".join(str(x) for x in v) + "]::integer[]"
    if isinstance(v, dict):
        text = json.dumps(v, sort_keys=True, separators=(",", ":")).replace("'", "''")
        return "'" + text + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


# ---------- file bodies ----------

def _csv_text(cols: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(cols)
    for r in rows:
        writer.writerow([_cell(r[c]) for c in cols])
    return buf.getvalue()


def _json_text(cols: list[str], rows: list[dict]) -> str:
    lines = [json.dumps({c: r[c] for c in cols}, default=_json_default) for r in rows]
    return "[\n" + ",\n".join(lines) + "\n]\n"


def _sql_text(tables: dict[str, list[dict]], cfg: GenConfig) -> str:
    parts = [f"-- KYC seed data (synthetic). seed={cfg.seed} as_of={cfg.as_of} scale={cfg.scale}",
             "BEGIN;",
             "TRUNCATE TABLE " + ", ".join(LOAD_ORDER) + " CASCADE;"]
    for name in LOAD_ORDER:
        rows = tables.get(name, [])
        cols = [c.name for c in Base.metadata.tables[name].columns]
        col_list = ", ".join(f'"{c}"' for c in cols)
        for i in range(0, len(rows), INSERT_BATCH):
            values = ",\n".join(
                "(" + ", ".join(_sql(r[c]) for c in cols) + ")" for r in rows[i:i + INSERT_BATCH])
            parts.append(f"INSERT INTO {name} ({col_list}) VALUES\n{values};")
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


# ---------- the export ----------

def export_all(ds, cfg: GenConfig, out_dir) -> dict:
    """Writes every file under out_dir and returns the manifest."""
    out = Path(out_dir)
    files: dict[str, dict] = {}

    def save(rel: str, text: str, rows: int | None) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")          # raw bytes: no newline translation on Windows
        path.write_bytes(data)
        files[rel] = {"rows": rows, "sha256": hashlib.sha256(data).hexdigest()}

    for name in LOAD_ORDER:
        cols = [c.name for c in Base.metadata.tables[name].columns]
        rows = ds.tables.get(name, [])
        save(f"csv/{name}.csv", _csv_text(cols, rows), len(rows))
        save(f"json/{name}.json", _json_text(cols, rows), len(rows))
    save("seed.sql", _sql_text(ds.tables, cfg), None)

    save("ground_truth/ground_truth.csv", _csv_text(GT_COLUMNS, ds.ground_truth), len(ds.ground_truth))
    save("ground_truth/ground_truth.json", _json_text(GT_COLUMNS, ds.ground_truth), len(ds.ground_truth))
    save("ground_truth/expected_decisions.csv",
         _csv_text(DECISION_COLUMNS, ds.decisions), len(ds.decisions))
    save("source_manifest.json", json.dumps(SOURCE_MANIFEST, indent=2) + "\n", None)

    manifest = {
        "command": f"python -m generator --scale {cfg.scale} --seed {cfg.seed} --as-of {cfg.as_of}",
        "seed": cfg.seed,
        "as_of": cfg.as_of.isoformat(),
        "scale": cfg.scale,
        "anomalies_injected": dict(Counter(g["issue_type"] for g in ds.ground_truth)),
        "files": dict(sorted(files.items())),
        "environment": {"python": platform.python_version(), "faker": version("faker")},
    }
    (out / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    return manifest