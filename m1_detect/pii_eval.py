"""Huấn luyện NER ở train hoặc chấm bộ nhận diện PII ở validation/test."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq
import pycrfsuite

from .pii import DEFAULT_MODEL, PII_TYPES, TOKEN, PiiDetector, Span, token_features, token_symbol


SPAN_SCHEMA = pa.schema([("text_id", pa.string()), ("start_cp", pa.int32()),
                         ("end_cp", pa.int32()), ("pii_type", pa.string())])


def load_texts(dataset, split):
    def selected_ids(table, column):
        selected = pq.read_table(dataset / f"evaluation/{table}.parquet", columns=[column],
                                 filters=[("split", "=", split)])
        return set(selected[column].to_pylist())

    customers = selected_ids("split_customers", "customer_id")
    families = selected_ids("split_families", "family_id")
    rows = pq.read_table(dataset / "features/texts.parquet",
                         columns=["customer_id", "family_id", "text_id", "body"],
                         filters=[("customer_id", "in", sorted(customers))]).to_pylist()
    if not rows or any(r["family_id"] not in families for r in rows):
        raise ValueError("Tập rỗng hoặc họ mẫu văn bản không đúng tập.")
    texts = {r["text_id"]: r["body"] for r in rows}
    if len(texts) != len(rows) or {r["customer_id"] for r in rows} != customers:
        raise ValueError("Trùng text_id hoặc thiếu khách.")
    return texts, len(customers)


def load_labels(dataset, texts):
    rows = pq.read_table(dataset / "evaluation/pii_spans_gold.parquet", columns=SPAN_SCHEMA.names,
                         filters=[("text_id", "in", sorted(texts))]).to_pylist()
    labels = defaultdict(set)
    for row in rows:
        tid = row["text_id"]
        span = Span(row["start_cp"], row["end_cp"], row["pii_type"])
        if (not 0 <= span.start_cp < span.end_cp <= len(texts[tid])
                or span.pii_type not in PII_TYPES or span in labels[tid]):
            raise ValueError(f"Nhãn span không hợp lệ ở {tid}.")
        labels[tid].add(span)
    return labels


def bio_tags(tokens, spans):
    tags = ["O"] * len(tokens)
    for s in spans:
        if s.pii_type not in ("NAME", "ADDRESS"):
            continue
        covered = [i for i, t in enumerate(tokens) if s.start_cp <= t.start() and t.end() <= s.end_cp]
        if (not covered or tokens[covered[0]].start() != s.start_cp
                or tokens[covered[-1]].end() != s.end_cp):
            raise ValueError("Span chuẩn không khớp ranh giới token.")
        for i in covered:
            tags[i] = ("B-" if i == covered[0] else "I-") + s.pii_type
    return tuple(tags)


def train(texts, labels, model_path):
    trainer = pycrfsuite.Trainer(verbose=False)
    seen = set()
    for tid, body in texts.items():
        tokens = list(TOKEN.finditer(body))
        if not tokens:
            continue
        symbols = tuple(token_symbol(t.group()) for t in tokens)
        tags = bio_tags(tokens, labels.get(tid, ()))
        # Những câu có cùng đặc trưng và nhãn chỉ dạy một lần.
        if (symbols, tags) not in seen:
            trainer.append(token_features(symbols), tags)
            seen.add((symbols, tags))
    trainer.set_params({"max_iterations": 100, "c2": 0.1, "feature.possible_transitions": True})
    model_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=model_path.parent) as tmp:
        staged = Path(tmp) / "ner.crfsuite"
        trainer.train(str(staged))
        staged.replace(model_path)
    return len(seen)


def metrics(tp, fp, fn):
    def divide(a, b):
        return a / b if b else 0.0

    return dict(TP=tp, FP=fp, FN=fn, precision=divide(tp, tp + fp),
                recall=divide(tp, tp + fn), F1=divide(2 * tp, 2 * tp + fp + fn))


def evaluate(predictions, gold):
    counts = {kind: [0, 0, 0] for kind in PII_TYPES}
    errors = []
    exact_texts = 0
    for tid, predicted in predictions.items():
        actual = gold.get(tid, set())
        exact_texts += predicted == actual
        for s in predicted & actual:
            counts[s.pii_type][0] += 1
        for error, index, spans in (("FP", 1, predicted - actual), ("FN", 2, actual - predicted)):
            for s in sorted(spans):
                counts[s.pii_type][index] += 1
                errors.append(dict(text_id=tid, start_cp=s.start_cp, end_cp=s.end_cp,
                                   pii_type=s.pii_type, error=error))
    summary = {"texts": len(predictions), "texts_without_gold_pii": len(predictions) - len(gold),
               "texts_exact": exact_texts, "micro": metrics(*map(sum, zip(*counts.values()))),
               "by_type": {kind: metrics(*values) for kind, values in counts.items()}}
    return summary, errors


def save_results(out, predictions, report, errors):
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [dict(text_id=tid, start_cp=s.start_cp, end_cp=s.end_cp, pii_type=s.pii_type)
            for tid, spans in predictions.items() for s in sorted(spans)]
    with TemporaryDirectory(dir=out.parent) as tmp:
        stage = Path(tmp) / "result"
        stage.mkdir()
        pq.write_table(pa.Table.from_pylist(rows, schema=SPAN_SCHEMA), stage / "pii_predictions.parquet")
        error_schema = SPAN_SCHEMA.append(pa.field("error", pa.string()))
        pq.write_table(pa.Table.from_pylist(errors, schema=error_schema), stage / "errors.parquet")
        (stage / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stage.rename(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("split", choices=("train", "validation", "test"))
    parser.add_argument("--dataset", type=Path, default=Path("datasets/bank_4000_medium_s4242"))
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--out", type=Path, help="Thư mục kết quả mới cho validation hoặc test.")
    args = parser.parse_args()
    out = args.out or args.model.parent / args.split
    if args.split != "train" and out.exists():
        parser.error("Thư mục kết quả đã tồn tại. Dùng --out để lưu lần chấm mới.")
    texts, customers = load_texts(args.dataset, args.split)
    if args.split == "train":
        sequences = train(texts, load_labels(args.dataset, texts), args.model)
        print(f"train: {customers} customers, {len(texts)} texts, {sequences} unique training sequences.")
        return
    detector = PiiDetector(args.model)
    predictions = {tid: set(detector.detect(body)) for tid, body in texts.items()}
    # Chỉ mở nhãn validation/test sau khi đã dự đoán xong toàn bộ văn bản.
    report, errors = evaluate(predictions, load_labels(args.dataset, texts))
    report.update(split=args.split, dataset=args.dataset.name, customers=customers,
                  ner_sha256=hashlib.sha256(args.model.read_bytes()).hexdigest(),
                  rules_sha256=hashlib.sha256(Path(__file__).with_name("pii.py").read_bytes()).hexdigest())
    save_results(out, predictions, report, errors)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
