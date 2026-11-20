"""Generate or independently validate a stage-one synthetic banking dataset."""
import argparse
from datetime import date
import json
from pathlib import Path
import random
import sys
import tempfile

import pyarrow.parquet as pq

from .schema import TABLES, to_arrow
from .validation import config_id, relpath, sha256, validate


BASE_COUNTS = {
    "areas": 200,
    "branches": 40,
    "rms": 120,
    "companies": 300,
    "merchants": 500,
    "devices": 6000,
}
MINIMUM_COUNTS = {
    "areas": 3,
    "branches": 2,
    "rms": 3,
    "companies": 4,
    "merchants": 12,
    "devices": 3,
}


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def config(customers, seed, as_of, density):
    """Create one small configuration object for the generator."""
    counts = {}
    for name, full_size in BASE_COUNTS.items():
        scaled_size = round(full_size * customers / 4000)
        counts[name] = max(scaled_size, MINIMUM_COUNTS[name])

    return {
        "customers": customers,
        "seed": seed,
        "as_of": as_of,
        "density": density,
        "counts": counts,
        "catalog_version": "demo-v1",
        "policy_version": "bank-demo-v1",
    }


def make_card(cfg):
    return "\n".join([
        "# Thẻ dữ liệu ngân hàng tổng hợp", "",
        f"Ngày chốt {cfg['as_of']} theo giờ Việt Nam. Cấu hình {cfg['density']}, seed {cfg['seed']}.",
        "Dùng cho thí nghiệm phân loại fact, nhận diện PII và kiểm thử quyền truy cập trên hồ sơ giả lập.",
        "Danh mục địa lý, cơ cấu nghề, thu nhập, giao dịch và tham số phân phối là giả định mô phỏng.",
        "Định dạng của các số thử nghiệm không bảo đảm chúng chưa từng được cấp. Không dùng để liên hệ hay thanh toán.",
        "Phạm vi tín dụng gồm khoản vay giải ngân một lần, chưa tái cơ cấu. Phân nhóm là chính sách demo.",
        "Cửa sổ giao dịch là (T trừ 12 tháng, T]. Snapshot và fact chỉ dùng dữ kiện có hiệu lực tương ứng.",
        "Chỉ cấp facts_input/facts_test và phần văn bản thích hợp cho M1. Evaluation chứa nhãn, chứng cứ và cách chia tập.",
        "fact_predictions là bảng rỗng ở thời điểm bàn giao cho M1. Vault và release được tạo ở M2.",
        "Xem manifest.json cho hash và số dòng từng bảng. validation_report.json chứa lỗi và thống kê thực đo.",
        "Khoảng 15–40 fact thường/khách là mục tiêu mô tả. Các khoảng dung sai thống kê đại diện cho ngân hàng thực chưa được hiệu chỉnh.", "",
    ])


def write_tables(root, tables):
    """Write the in-memory tables and return their row counts and hashes."""
    files = {}
    for name in TABLES:
        relative_path = relpath(name)
        path = root / relative_path
        table = to_arrow(name, tables[name])
        pq.write_table(table, path, compression="zstd")
        files[relative_path] = {
            "rows": len(table),
            "sha256": sha256(path),
        }
    return files


def make_manifest(destination, cfg, files):
    return {
        "run_id": destination.name,
        "schema_version": "appendix-c-v1",
        "generator_version": "1.0",
        "config_id": config_id(cfg),
        "T": cfg["as_of"],
        "timezone": "Asia/Ho_Chi_Minh",
        "seed": cfg["seed"],
        "policy_version": cfg["policy_version"],
        "config": cfg,
        "code_sha256": {
            path.name: sha256(path)
            for path in sorted(Path(__file__).parent.glob("*.py"))
        },
        "files": files,
    }


def generate(destination, cfg):
    from .bank import generate_bank
    from .features import build_features

    destination = destination.resolve()
    if destination.exists():
        raise ValueError(f"Destination already exists: {destination}. Choose a new run directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Validate in staging so an incomplete run is never presented as the deliverable.
    with tempfile.TemporaryDirectory(prefix=".data-gen-", dir=destination.parent) as temp:
        root = Path(temp) / "run"
        for layer in ("source", "features", "evaluation"):
            (root / layer).mkdir(parents=True)
        tables = generate_bank(cfg, random.Random(cfg["seed"]))
        build_features(tables, cfg, random.Random(f"features:{cfg['seed']}"))
        tables.setdefault("fact_predictions", [])
        if set(tables) != set(TABLES):
            raise ValueError(f"Unexpected or missing tables: {set(tables).symmetric_difference(TABLES)}")
        files = write_tables(root, tables)
        save_json(root / "manifest.json", make_manifest(destination, cfg, files))
        (root / "data_card.md").write_text(make_card(cfg), encoding="utf-8")
        report = validate(root)
        if report["status"] != "PASS":
            raise ValueError(json.dumps(report, ensure_ascii=False, indent=2))
        save_json(root / "validation_report.json", report)
        root.rename(destination)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--out", required=True, type=Path)
    gen.add_argument("--customers", type=int, default=4000)
    gen.add_argument("--seed", type=int, default=4242)
    gen.add_argument("--as-of", default="2026-10-31", type=date.fromisoformat)
    gen.add_argument("--density", choices=["sparse", "medium", "dense"], default="medium")
    check = sub.add_parser("validate")
    check.add_argument("run", type=Path)
    args = parser.parse_args()
    if args.action == "validate":
        report = validate(args.run)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return int(report["status"] != "PASS")
    if args.customers < 3:
        parser.error("--customers must be at least 3 to populate train, validation and test")
    try:
        report = generate(args.out, config(args.customers, args.seed, args.as_of.isoformat(), args.density))
    except (OSError, ValueError) as exc:
        print(ascii(str(exc)), file=sys.stderr)
        return 1
    stats = report["statistics"]
    print(f"PASS {args.out}: {stats['customers']} customers, {stats['source_transactions']} transactions, {stats['distinct_facts']} facts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
