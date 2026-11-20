"""Integration checks for stage-one handoff. Each corruption gets its own temporary run."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .__main__ import config, generate, save_json
from .features import derive_facts
from .schema import TABLES, from_arrow, to_arrow
from .validation import relpath, sha256, validate


class PreM1DatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.base = Path(temp.name)
        cls.clean = cls.base / "clean"
        cls.cfg = config(24, 4242, "2026-10-31", "medium")
        generate(cls.clean, cls.cfg)

    def read(self, name, run=None):
        return from_arrow(pq.read_table((run or self.clean) / relpath(name)))

    def test_clean_and_seed_reproducibility(self):
        result = validate(self.clean)
        self.assertEqual(result["status"], "PASS", result["examples"])
        with tempfile.TemporaryDirectory(dir=self.base) as tmp:
            repeat = Path(tmp) / "repeat"
            generate(repeat, self.cfg)
            manifest = json.loads((self.clean / "manifest.json").read_text(encoding="utf-8"))
            for rel in manifest["files"]:
                self.assertEqual(sha256(self.clean / rel), sha256(repeat / rel), rel)

    def test_held_out_customers_and_template_families(self):
        partitions = ("train", "validation", "test")
        people = {r["customer_id"]: r["split"] for r in self.read("split_customers")}
        families = {r["family_id"]: r["split"] for r in self.read("split_families")}
        observed = {p: set() for p in partitions}
        for note in self.read("texts"):
            split = people[note["customer_id"]]
            self.assertEqual(split, families[note["family_id"]])
            observed[split].add(note["family_id"])
        for p in partitions:
            self.assertTrue(observed[p], p)
            for other in set(partitions) - {p}:
                self.assertTrue(observed[p].isdisjoint(observed[other]))
        self.assertEqual({r["fact_id"] for r in self.read("facts_test")},
                         {r["fact_id"] for r in self.read("facts")
                          if people[r["customer_id"]] == "test"})

    def test_salary_sources_and_vietnam_cutoff(self):
        # One customer, one company, two qualifying salary entries. The other
        # three events are cancelled, before the window or after T in Vietnam.
        tables = {k: self.read(k) for k, spec in TABLES.items() if spec.layer == "source"}
        account = next(a for a in tables["accounts"] if a["opened_on"] < "2025-11-01" and not a["closed_on"])
        company = tables["companies"][0]["company_id"]
        base = dict(account_id=account["account_id"], kind="SALARY", payer_company_id=company,
                    amount_vnd=10_000_000, merchant_id=None, card_id=None, device_id=None)
        events = [
            ("ACCEPT1", "2025-10-31T17:00:00+00:00", "POSTED"),
            ("ACCEPT2", "2026-10-31T16:59:59+00:00", "POSTED"),
            ("TOO_EARLY", "2025-10-31T16:59:59+00:00", "POSTED"),
            ("TOO_LATE", "2026-10-31T17:00:00+00:00", "POSTED"),
            ("REVERSED", "2026-06-15T10:00:00+07:00", "CANCELLED"),
        ]
        tables["transactions"] = [dict(base, txn_id=tid, posted_at=at, status=status)
                                  for tid, at, status in events]
        results = derive_facts(tables, self.cfg)
        salary = [r for r in results["facts"] if r["predicate"] == "nhận_lương_từ"]
        self.assertEqual(len(salary), 1)
        self.assertEqual(salary[0]["customer_id"], account["customer_id"])
        evidence = [r for r in results["fact_sources"] if r["fact_id"] == salary[0]["fact_id"]]
        self.assertEqual({(r["source_table"], tuple(json.loads(r["source_key"]))) for r in evidence},
                         {("transactions", ("ACCEPT1",)), ("transactions", ("ACCEPT2",))})

    def check_mutation(self, name, expected, mutate, *, update_hash=True):
        with tempfile.TemporaryDirectory(dir=self.base) as tmp:
            run = Path(tmp) / "bad"
            shutil.copytree(self.clean, run)
            rel = relpath(name)
            path = run / rel
            table = pq.read_table(path)
            pq.write_table(mutate(table), path, compression="zstd")
            if update_hash:
                # A matching hash proves file integrity only. Semantic corruption
                # must still fail validation under its own specific check.
                manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
                manifest["files"][rel].update(sha256=sha256(path), rows=len(pq.read_table(path)))
                save_json(run / "manifest.json", manifest)
            result = validate(run)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn(expected, result["errors_by_check"], result["examples"])

    def check_rows(self, name, expected, mutate):
        def replace(table):
            rows = from_arrow(table)
            mutate(rows)
            return to_arrow(name, rows)
        self.check_mutation(name, expected, replace)

    def test_wrong_card_owner_with_valid_foreign_keys(self):
        def mutate(rows):
            cards = self.read("cards")
            p = next(r for r in rows if r["kind"] == "PURCHASE")
            p["card_id"] = next(c["card_id"] for c in cards if c["account_id"] != p["account_id"])
        self.check_rows("transactions", "transaction_card_owner", mutate)

    def test_bad_luhn(self):
        def mutate(rows):
            pan = rows[0]["pan_test"]
            rows[0]["pan_test"] = pan[:-1] + str((int(pan[-1]) + 1) % 10)
        self.check_rows("cards", "card_pan_luhn", mutate)

    def test_stale_loan_snapshot(self):
        def mutate(rows):
            rows[0]["outstanding_vnd"] += 1
        self.check_rows("loan_snapshot", "loan_snapshot_reconciliation", mutate)

    def test_overpayment(self):
        def mutate(rows):
            rows[0]["interest_paid_vnd"] += 10**12
        self.check_rows("payment_allocations", "payment_overallocated", mutate)

    def test_broken_foreign_key(self):
        self.check_rows("transactions", "foreign_key",
                        lambda rows: rows[0].update(account_id="NO_SUCH_ACCOUNT"))

    def test_wrong_provenance(self):
        self.check_rows("fact_sources", "derived_fact_sources",
                        lambda rows: rows[0].update(source_key='["NO_SUCH_SOURCE"]'))

    def test_changed_pii_span(self):
        self.check_rows("pii_spans_gold", "span_bounds",
                        lambda rows: rows[0].update(start_cp=-1))

    def test_cross_customer_chunk(self):
        def mutate(rows):
            other = next(r["customer_id"] for r in rows if r["customer_id"] != rows[0]["customer_id"])
            rows[0]["customer_id"] = other
        self.check_rows("rag_chunks", "rag_chunk_owner", mutate)

    def test_family_from_a_different_split(self):
        def mutate(rows):
            families = self.read("split_families")
            splits = {r["family_id"]: r["split"] for r in families}
            rows[0]["family_id"] = next(f for f in splits if splits[f] != splits[rows[0]["family_id"]])
        self.check_rows("texts", "split_leakage", mutate)

    def test_hidden_label_in_m1_view(self):
        self.check_mutation("facts_input", "parquet_schema",
                            lambda table: table.append_column("is_sensitive", pa.array([True] * len(table))))

    def test_hash_detects_changed_file(self):
        name = "fact_sources"
        self.check_mutation(name, "file_hash", lambda table: table.slice(1), update_hash=False)


if __name__ == "__main__":
    unittest.main()
