"""Chạy từ thư mục gốc: python -m m1_detect validation hoặc python -m m1_detect test."""
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq

from data_gen.schema import arrow_schema
from . import predict_fact


POLICY = "bank-demo-v1"
INPUT = arrow_schema("facts_input")
ERROR = INPUT.append(pa.field("pred_sensitive", pa.bool_(), nullable=False)).append(
    pa.field("is_sensitive", pa.bool_(), nullable=False))


def load_split(dataset, split):
    customers = pq.read_table(dataset / "evaluation/split_customers.parquet",
                              columns=["customer_id"], filters=[("split", "=", split)])
    customer_ids = customers["customer_id"].to_pylist()
    # Chỉ dùng hai ID để chọn tập và kiểm tra độ bao phủ.
    owners = pq.read_table(dataset / "features/facts.parquet", columns=["fact_id", "customer_id"],
                           filters=[("customer_id", "in", customer_ids)])
    expected = set(owners["fact_id"].to_pylist())
    if split == "validation":
        inputs = pq.read_table(dataset / "features/facts_input.parquet", columns=INPUT.names,
                               filters=[("fact_id", "in", sorted(expected))])
    else:
        inputs = pq.read_table(dataset / "features/facts_test.parquet", columns=INPUT.names)
    if (not expected or inputs.num_rows != len(expected) or owners.num_rows != len(expected)
            or set(inputs["fact_id"].to_pylist()) != expected
            or set(owners["customer_id"].to_pylist()) != set(customer_ids)):
        raise ValueError("Tập đầu vào thiếu, trùng hoặc sai khách/fact so với bảng chia tập.")
    return inputs.to_pylist(), len(set(customer_ids))


def evaluate(dataset, inputs, predictions):
    # Chỉ mở nhãn sau khi toàn bộ dự đoán của tập đã hoàn tất.
    labels = pq.read_table(dataset / "evaluation/facts_gold.parquet",
                           columns=["fact_id", "is_sensitive", "policy_version"],
                           filters=[("fact_id", "in", list(predictions))])
    gold = {r["fact_id"]: r["is_sensitive"] for r in labels.to_pylist()}
    if labels.num_rows != len(gold) or gold.keys() != predictions.keys():
        raise ValueError("Nhãn chuẩn thiếu hoặc trùng fact_id.")
    if set(labels["policy_version"].to_pylist()) != {POLICY}:
        raise ValueError("Phiên bản policy không khớp với baseline.")

    counts = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    errors = []
    for row in inputs:
        fid = row["fact_id"]
        pred, actual = predictions[fid], gold[fid]
        if pred:
            counts["TP" if actual else "FP"] += 1
        else:
            counts["FN" if actual else "TN"] += 1
        if pred != actual:
            errors.append({**row, "pred_sensitive": pred, "is_sensitive": actual})
    tp, fp, fn = counts["TP"], counts["FP"], counts["FN"]
    counts.update(precision=tp / (tp + fp) if tp + fp else 0.0,
                  recall=tp / (tp + fn) if tp + fn else 0.0,
                  F1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0)
    return counts, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("split", choices=["validation", "test"])
    parser.add_argument("--dataset", type=Path, default=Path("datasets/bank_4000_medium_s4242"))
    parser.add_argument("--out", type=Path, default=Path("experiments/e1/rule_baseline"))
    args = parser.parse_args()
    out = args.out.resolve()
    if args.split == "test" and (out.exists() or out.is_relative_to(args.dataset.resolve())):
        parser.error("Chọn thư mục đầu ra mới bên ngoài bộ dữ liệu, hoặc dùng --out.")

    fingerprint = hashlib.sha256(Path(__file__).with_name("__init__.py").read_bytes()).hexdigest()
    run_id = f"rule-{POLICY}-{fingerprint[:12]}"
    inputs, n_customers = load_split(args.dataset, args.split)
    predictions = {r["fact_id"]: predict_fact(r["event_kind"], r["observed_value"], r["evidence_text"])
                   for r in inputs}
    counts, errors = evaluate(args.dataset, inputs, predictions)
    metrics = {"split": args.split, "policy_version": POLICY, "model_run_id": run_id,
               "rules_sha256": fingerprint, "customers": n_customers, "facts": len(predictions), **counts}
    if args.split == "test":
        out.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=out.parent) as temp:
            stage = Path(temp) / "result"
            stage.mkdir()
            rows = [{"model_run_id": run_id, "fact_id": fid, "pred_sensitive": pred, "score": None}
                    for fid, pred in predictions.items()]
            pq.write_table(pa.Table.from_pylist(rows, schema=arrow_schema("fact_predictions")),
                           stage / "fact_predictions.parquet")
            pq.write_table(pa.Table.from_pylist(errors, schema=ERROR), stage / "errors.parquet")
            (stage / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
            stage.rename(out)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
