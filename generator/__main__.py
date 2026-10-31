import argparse
from datetime import date

from app.core.config import settings
from generator.config import GenConfig
from generator.loader import load
from generator.pipeline import build_all


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m generator")
    p.add_argument("--scale", type=float, default=1.0, help="1.0 = full, 0.01 = starter")
    p.add_argument("--seed", type=int, default=settings.seed)
    p.add_argument("--as-of", type=date.fromisoformat, default=settings.as_of_date)
    p.add_argument("--no-load", action="store_true", help="build only, skip the database")
    args = p.parse_args()

    cfg = GenConfig(seed=args.seed, as_of=args.as_of, scale=args.scale)
    print(f"Generating: seed={cfg.seed} as_of={cfg.as_of} scale={cfg.scale}")
    ds = build_all(cfg)
    for name, rows in ds.tables.items():
        print(f"  built {name}: {len(rows)} rows")
    if not args.no_load:
        print("Loading into Postgres...")
        load(ds.tables)
    print("Done.")


if __name__ == "__main__":
    main()