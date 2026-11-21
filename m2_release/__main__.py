"""Tạo bản F từ dự đoán M1 đã lưu, sau đó đọc lại TSV để đối soát theo fact_id."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory

import pyarrow.parquet as pq

from . import vault


DATASET = Path("datasets/bank_4000_medium_s4242")
PREDICTIONS = Path("experiments/e1/rule_baseline/fact_predictions.parquet")
HEADER = ["head", "relation", "tail"]
STORES = {False: "identified.tsv", True: "masked.tsv"}
# Chỉ các quan hệ trong Phụ lục C.5 được phép ra ngoài.
RELATIONS = set("""sống_tại mở_tài_khoản_tại được_quản_lý_bởi làm_việc_tại nhận_lương_từ
giao_dịch_tại giao_dịch_nhóm_nhạy_cảm dùng_thiết_bị nhóm_nợ trạng_thái_vay bậc_thu_nhập hạng_điểm""".split())


def load_inputs(dataset, prediction_file):
    split = pq.read_table(dataset / "evaluation/split_customers.parquet", columns=["customer_id"],
                          filters=[("split", "=", "test")])
    customers = set(split["customer_id"].to_pylist())
    if not customers or None in customers or len(customers) != split.num_rows:
        raise ValueError("Empty, null or duplicate customer in test split.")
    facts = pq.read_table(dataset / "features/facts.parquet",
                          columns=["fact_id", "customer_id", "predicate", "object_node_id"],
                          filters=[("customer_id", "in", sorted(customers))]).to_pylist()
    fact_ids = {f["fact_id"] for f in facts}
    if len(fact_ids) != len(facts) or {f["customer_id"] for f in facts} != customers:
        raise ValueError("Duplicate fact_id or missing customer in test facts.")
    for fact in facts:
        if not all(fact.values()) or fact["predicate"] not in RELATIONS:
            raise ValueError("Invalid test fact or relation outside release schema.")

    rows = pq.read_table(prediction_file, columns=["fact_id", "model_run_id", "pred_sensitive"]).to_pylist()
    runs = {r["model_run_id"] for r in rows}
    if len(runs) != 1 or not all(runs):
        raise ValueError("Expected predictions from exactly one nonempty model_run_id.")
    predictions = {}
    for row in rows:
        fid = row["fact_id"]
        if fid in predictions or type(row["pred_sensitive"]) is not bool:
            raise ValueError("Duplicate prediction or invalid pred_sensitive.")
        predictions[fid] = row["pred_sensitive"]
    if fact_ids != predictions.keys():
        missing = len(fact_ids - predictions.keys())
        extra = len(predictions.keys() - fact_ids)
        raise ValueError(f"Prediction coverage mismatch: missing={missing}, extra={extra}.")
    return facts, predictions, runs.pop()


def random_codes(values, prefix):
    result = {}
    used = set()
    for value in sorted(set(values)):
        code = prefix + secrets.token_hex(16)
        while code in used:
            code = prefix + secrets.token_hex(16)
        used.add(code)
        result[value] = code
    return result


def make_mappings(facts):
    customers = {f["customer_id"] for f in facts}
    clear = random_codes(customers, "c_")
    masked = random_codes(customers, "m_")
    return {
        "customers": {v: {"c_v": clear[v], "m_v": masked[v]} for v in customers},
        "objects": random_codes((f["object_node_id"] for f in facts), "o_"),
    }


def write_tsvs(release, facts, predictions, mappings):
    release.mkdir(parents=True)
    stores = {flag: [] for flag in STORES}
    for f in facts:
        sensitive = predictions[f["fact_id"]]
        codes = mappings["customers"][f["customer_id"]]
        head = codes["m_v"] if sensitive else codes["c_v"]
        tail = mappings["objects"][f["object_node_id"]]
        stores[sensitive].append((head, f["predicate"], tail))
    for flag, filename in STORES.items():
        # Không để thứ tự bản ghi nguồn trở thành liên kết giữa hai phía.
        secrets.SystemRandom().shuffle(stores[flag])
        with (release / filename).open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(HEADER)
            writer.writerows(stores[flag])


def reconcile(release, facts, predictions, mappings):
    # Giải mã từng triple trên đĩa về bộ ba nguồn, rồi truy ngược ra fact_id.
    original = {(f["customer_id"], f["predicate"], f["object_node_id"]): f["fact_id"] for f in facts}
    if len(original) != len(facts):
        raise ValueError("Duplicate normalized source triple.")
    if {p.name for p in release.iterdir()} != set(STORES.values()):
        raise ValueError("Unexpected release files.")
    objects = {code: node for node, code in mappings["objects"].items()}
    seen = set()
    result = {}
    for flag, filename in STORES.items():
        side = "m_v" if flag else "c_v"
        owners = {codes[side]: customer for customer, codes in mappings["customers"].items()}
        heads = set()
        sample = None
        count = 0
        path = release / filename
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t", strict=True)
            if next(reader, None) != HEADER:
                raise ValueError(f"Invalid header in {filename}.")
            for head, relation, tail in reader:
                triple = owners.get(head), relation, objects.get(tail)
                fid = original.get(triple)
                if fid is None or fid in seen or predictions[fid] != flag:
                    raise ValueError(f"Unknown, repeated or misrouted triple in {filename}, row {reader.line_num}.")
                seen.add(fid)
                heads.add(head)
                count += 1
                if sample is None:
                    sample = [head, relation, tail]
        result[filename] = dict(rows=count, distinct_heads=len(heads), sample=sample,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if seen != predictions.keys():
        raise ValueError(f"Missing {len(predictions.keys() - seen)} test facts in release.")
    return result


def build(dataset, prediction_file, out):
    out = out.resolve()
    if out.exists():
        raise FileExistsError(f"Run already exists: {out}. Choose a new --out.")
    if out.is_relative_to(dataset.resolve()) or out.is_relative_to(prediction_file.resolve().parent):
        raise ValueError("Choose a run directory outside the dataset and M1 results.")
    facts, predictions, model_run = load_inputs(dataset, prediction_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as tmp:
        stage = Path(tmp) / "run"
        stage.mkdir()
        release = stage / "release/F"
        mappings = make_mappings(facts)
        write_tsvs(release, facts, predictions, mappings)
        restored = vault.save_and_check(stage / "vault", mappings)
        stores = reconcile(release, facts, predictions, restored)
        report = dict(variant="F", dataset=dataset.name, model_run_id=model_run,
                      test_customers=len(restored["customers"]), reconciled_fact_ids=len(facts),
                      vault_round_trip=True, wrong_key_rejected=True, stores=stores)
        (stage / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        stage.rename(out)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--predictions", type=Path, default=PREDICTIONS)
    parser.add_argument("--out", type=Path, default=Path("experiments/m2/run_001"))
    args = parser.parse_args()
    try:
        report = build(args.dataset, args.predictions, args.out)
    except (FileExistsError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
