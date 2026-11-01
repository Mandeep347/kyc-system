"""Generates docs/schema_reference.md from the SQLAlchemy models.
Run from the project root:  python scripts/make_schema_docs.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import CheckConstraint, UniqueConstraint  # noqa: E402

import app.models  # noqa: E402,F401  (registers the tables)
from app.core.database import Base  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "schema_reference.md"


def key_cell(col) -> str:
    keys = []
    if col.primary_key:
        keys.append("PK")
    keys += [f"FK to {fk.column.table.name}.{fk.column.name}" for fk in col.foreign_keys]
    if col.unique:
        keys.append("UNIQUE")
    return ", ".join(keys)


def main() -> None:
    tables = Base.metadata.sorted_tables
    lines = ["# Schema reference (generated)", "",
             "Generated from the SQLAlchemy models by `python scripts/make_schema_docs.py`. "
             "Do not edit by hand.", "",
             "## ER diagram", "", "```mermaid", "erDiagram"]
    for t in tables:
        for fk in sorted(t.foreign_keys, key=lambda f: (f.column.table.name, f.parent.name)):
            left = "|o" if fk.parent.nullable else "||"          # optional vs mandatory parent
            lines.append(f'    {fk.column.table.name.upper()} {left}--o{{ {t.name.upper()} : "{fk.parent.name}"')
    lines += ["```", "",
              "`audit_events.entity_id` is a deliberate soft reference (no FK): one column points at "
              "customers, documents, reviews, alerts or ratings.", ""]

    for t in tables:
        lines += [f"## {t.name}", "", "| Column | Type | Nullable | Keys | Default |", "|---|---|---|---|---|"]
        for c in t.columns:
            default = ""
            if c.server_default is not None:
                default = str(c.server_default.arg)
            lines.append(f"| {c.name} | {c.type} | {'yes' if c.nullable else 'no'} | {key_cell(c)} | {default} |")

        checks = sorted((c for c in t.constraints if isinstance(c, CheckConstraint)), key=lambda c: str(c.name))
        uniques = [c for c in t.constraints if isinstance(c, UniqueConstraint)]
        if checks or uniques or t.indexes:
            lines.append("")
        for u in uniques:
            lines.append(f"- UNIQUE `{u.name}`: ({', '.join(col.name for col in u.columns)})")
        for c in checks:
            lines.append(f"- CHECK `{c.name}`: `{c.sqltext}`")
        for idx in sorted(t.indexes, key=lambda i: str(i.name)):
            where = idx.dialect_options["postgresql"]["where"]
            extra = f" WHERE {where}" if where is not None else ""
            kind = "UNIQUE INDEX" if idx.unique else "INDEX"
            lines.append(f"- {kind} `{idx.name}` on ({', '.join(c.name for c in idx.columns)}){extra}")
        lines.append("")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()