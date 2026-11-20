"""Reopen the delivered Parquet files and check their integrity and semantics."""
from collections import Counter
from datetime import date
from pathlib import Path
import hashlib
import json

import pyarrow.parquet as pq

from .schema import TABLES, REFS, arrow_schema, from_arrow
from .profile import profile
from .diagnostics import diagnostics


def config_id(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def relpath(name):
    return f"{TABLES[name].layer}/{name}.parquet"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class Findings:
    def __init__(self):
        self.counts = Counter()
        self.examples = []

    def add(self, code, detail):
        self.counts[code] += 1
        if self.counts[code] <= 3 and len(self.examples) < 100:
            self.examples.append({"check": code, "detail": str(detail)})


def check_keys(tables, issue):
    single = {}
    for name, table in TABLES.items():
        seen = set()
        for row in tables[name]:
            key = tuple(row[k] for k in table.primary)
            if key in seen:
                issue("duplicate_pk", (name, key))
            seen.add(key)
        if len(table.primary) == 1:
            single[name] = {k[0] for k in seen}
    for name, table in TABLES.items():
        refs = [(x.rstrip("?"), REFS[x.rstrip("?")]) for x in table.columns if x.rstrip("?") in REFS]
        for row in tables[name]:
            for field, target in refs:
                value = row[field]
                if value is not None and value not in single[target]:
                    issue("foreign_key", (name, field, value))
    periods = {(r["loan_id"], r["installment_no"]) for r in tables["installments"]}
    for r in tables["payment_allocations"]:
        if (r["loan_id"], r["installment_no"]) not in periods:
            issue("installment_fk", r["payment_ref"])
    for name, keys in (("facts", ("customer_id", "predicate", "object_node_id")),
                       ("object_nodes", ("node_type", "source_key"))):
        seen = set()
        for r in tables[name]:
            k = tuple(r[c] for c in keys)
            if k in seen:
                issue("duplicate_business_key", (name, k))
            seen.add(k)


def validate(root):
    from .checks import validate_business

    root = Path(root)
    findings = Findings()
    issue = findings.add
    tables = {}
    stats = None
    cfg = None
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        cfg = manifest["config"]
        date.fromisoformat(cfg["as_of"])
        files = manifest["files"]
        if (manifest.get("schema_version") != "appendix-c-v1" or
                manifest.get("timezone") != "Asia/Ho_Chi_Minh" or
                manifest.get("config_id") != config_id(cfg)):
            issue("manifest_version", "Unexpected schema, timezone or configuration fingerprint")
        if set(files) != {relpath(n) for n in TABLES}:
            issue("manifest_inventory", "Expected the Appendix C pre-M1 table set")
        if manifest["T"] != cfg["as_of"] or manifest["seed"] != cfg["seed"] or manifest["policy_version"] != cfg["policy_version"]:
            issue("manifest_config", "Inconsistent run configuration")
        for name in TABLES:
            rel = relpath(name)
            path = root / rel
            try:
                desc = files[rel]
                if sha256(path) != desc["sha256"]:
                    issue("file_hash", rel)
                data = pq.ParquetFile(path).read()
                expected = arrow_schema(name)
                if not data.schema.equals(expected):
                    issue("parquet_schema", rel)
                    continue
                if len(data) != desc["rows"]:
                    issue("row_count", rel)
                for field in expected:
                    if not field.nullable and data[field.name].null_count:
                        issue("null", (rel, field.name))
                tables[name] = from_arrow(data)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                issue("read_table", f"{rel}: {exc}")
        for layer in ("source", "features", "evaluation"):
            for path in (root / layer).glob("*.parquet"):
                if path.relative_to(root).as_posix() not in files:
                    issue("unexpected_file", path.name)
        if set(tables) == set(TABLES) and not findings.counts.get("null"):
            check_keys(tables, issue)
            structural = {"duplicate_pk", "foreign_key", "installment_fk", "duplicate_business_key"}
            if not structural.intersection(findings.counts):
                if len(tables["customers"]) != cfg["customers"] or not tables["customers"]:
                    issue("population", "Customer count differs from configuration or empty")
                if tables["fact_predictions"]:
                    issue("premature_predictions", "M1 predictions must be empty at generator handoff")
                for name, size in cfg["counts"].items():
                    actual = sum(r["role_code"] == "RM" for r in tables["staff"]) if name == "rms" else len(tables[name])
                    if size != actual:
                        issue("catalog_count", (name, size, actual))
                validate_business(tables, cfg, issue)
                if not findings.counts:
                    stats = profile(tables)
                    stats["business_diagnostics"] = diagnostics(tables, cfg)
    except (ValueError, TypeError, KeyError, IndexError, OSError, ArithmeticError, AttributeError) as exc:
        issue("invalid_dataset", str(exc))
    return {"status": "FAIL" if findings.counts else "PASS", "error_count": sum(findings.counts.values()),
            "errors_by_check": dict(findings.counts), "examples": findings.examples, "statistics": stats}
