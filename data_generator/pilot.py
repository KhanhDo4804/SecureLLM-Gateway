"""Repeat the complete Parquet round trip for three densities across five seeds."""
import argparse
from datetime import date
import json
from pathlib import Path
from statistics import mean, stdev
from tempfile import TemporaryDirectory

from .__main__ import config, generate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--customers", type=int, default=120)
    p.add_argument("--first-seed", type=int, default=4242)
    p.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 31))
    args = p.parse_args()
    if args.customers < 3 or args.out.exists():
        p.error("Use a new output path and at least three customers")
    rows, summary = [], {}
    for density in ("sparse", "medium", "dense"):
        metrics = []
        for seed in range(args.first_seed, args.first_seed + 5):
            cfg = config(args.customers, seed, args.as_of.isoformat(), density)
            with TemporaryDirectory() as temp:
                result = generate(Path(temp) / "pilot", cfg)
            stats = result["statistics"]
            rows.append({"config": cfg, "validation": result})
            metrics.append(stats["d_v_ordinary_facts"]["mean"])
        summary[density] = {"runs": len(metrics), "mean_ordinary_facts_per_customer": mean(metrics),
                            "sd_across_run_means": stdev(metrics)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as f:
        json.dump({"summary": summary, "runs": rows}, f, indent=2, ensure_ascii=False, allow_nan=False)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
