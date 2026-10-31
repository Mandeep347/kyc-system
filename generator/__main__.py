import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from app.core.config import settings
from generator.config import GenConfig
from generator.export import export_all
from generator.loader import load
from generator.pipeline import build_all


def repro_check(args) -> int:
    """Runs the generator twice in separate processes and compares file checksums."""
    base = Path(args.export_dir)
    manifests = []
    for n, hash_seed in ((1, "1"), (2, "2")):     # different hash seeds on purpose
        out = base / f"repro_run_{n}"
        cmd = [sys.executable, "-m", "generator", "--no-load",
               "--scale", str(args.scale), "--seed", str(args.seed),
               "--as-of", args.as_of.isoformat(), "--export-dir", str(out)]
        if args.no_anomalies:
            cmd.append("--no-anomalies")
        print(f"\n=== run {n} (PYTHONHASHSEED={hash_seed}) ===")
        subprocess.run(cmd, check=True, env={**os.environ, "PYTHONHASHSEED": hash_seed})
        manifests.append(json.loads((out / "manifest.json").read_text(encoding="utf-8")))
        shutil.rmtree(out)                         # we only need the checksums

    a, b = manifests[0]["files"], manifests[1]["files"]
    print("\n=== comparison ===")
    for name in sorted(a):
        same = a[name] == b.get(name)
        print(f"{name:<44} rows={a[name]['rows']!s:<7} {a[name]['sha256'][:12]}  "
              f"{'MATCH' if same else 'DIFFERENT'}")
    identical = a == b
    print("\nRESULT:", "ALL FILES MATCH - generation is reproducible" if identical
          else "MISMATCH - generation is NOT reproducible")

    base.mkdir(parents=True, exist_ok=True)
    report = {"seed": args.seed, "as_of": args.as_of.isoformat(), "scale": args.scale,
              "identical": identical, "run_1": a, "run_2": b}
    (base / "reproducibility_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if identical else 1


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m generator")
    p.add_argument("--scale", type=float, default=1.0, help="1.0 = full, 0.01 = starter")
    p.add_argument("--seed", type=int, default=settings.seed)
    p.add_argument("--as-of", type=date.fromisoformat, default=settings.as_of_date)
    p.add_argument("--no-load", action="store_true", help="skip the database")
    p.add_argument("--no-export", action="store_true", help="skip the CSV/JSON/SQL files")
    p.add_argument("--no-anomalies", action="store_true", help="clean baseline only")
    p.add_argument("--export-dir", default="output")
    p.add_argument("--repro-check", action="store_true",
                   help="generate twice in separate processes and compare checksums")
    args = p.parse_args()

    if args.repro_check:
        sys.exit(repro_check(args))

    cfg = GenConfig(seed=args.seed, as_of=args.as_of, scale=args.scale)
    print(f"Generating: seed={cfg.seed} as_of={cfg.as_of} scale={cfg.scale} "
          f"anomalies={'off' if args.no_anomalies else 'on'}")
    ds = build_all(cfg, with_anomalies=not args.no_anomalies)
    for name, rows in ds.tables.items():
        print(f"  built {name}: {len(rows)} rows")
    if ds.ground_truth:
        print("  ground truth:", dict(Counter(g["issue_type"] for g in ds.ground_truth)))

    if not args.no_export:
        manifest = export_all(ds, cfg, args.export_dir)
        print(f"Exported {len(manifest['files'])} files to {args.export_dir}/")
    if not args.no_load:
        print("Loading into Postgres...")
        load(ds.tables)
    print("Done.")


if __name__ == "__main__":
    main()