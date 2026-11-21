import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from . import vault
from .__main__ import build, load_inputs, reconcile


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.dataset = root / "dataset"
        (self.dataset / "features").mkdir(parents=True)
        (self.dataset / "evaluation").mkdir()
        self.pred = root / "e1/pred.parquet"
        self.pred.parent.mkdir()
        self.out = root / "run"
        pq.write_table(pa.table({"customer_id": ["a", "b", "c"], "split": ["test", "test", "train"]}),
                       self.dataset / "evaluation/split_customers.parquet")
        pq.write_table(pa.table({"fact_id": ["f1", "f2", "f3", "f4"], "customer_id": ["a", "a", "b", "c"],
                                 "predicate": ["làm_việc_tại", "nhận_lương_từ", "làm_việc_tại", "sống_tại"],
                                 "object_node_id": ["company:1", "company:1", "company:2", "area:1"]}),
                       self.dataset / "features/facts.parquet")
        self.write_preds(["f3", "f1", "f2"], [False, False, True])

    def write_preds(self, ids, flags, runs=None):
        pq.write_table(pa.table({"model_run_id": runs or ["demo"] * len(ids), "fact_id": ids,
                                 "pred_sensitive": flags}), self.pred)

    def test_invalid_predictions_never_publish(self):
        for ids, flags, runs in [
            (["f1", "f2"], [True, False], None),
            (["f1", "f2", "f3", "f4"], [True] * 4, None),
            (["f1", "f1", "f3"], [True] * 3, None),
            (["f1", "f2", "f3"], [True] * 3, ["a", "b", "a"]),
            (["f1", "f2", "f3"], [None, True, False], None),
        ]:
            with self.subTest(ids=ids, flags=flags, runs=runs):
                self.write_preds(ids, flags, runs)
                with self.assertRaises(ValueError):
                    build(self.dataset, self.pred, self.out)
                self.assertFalse(self.out.exists())

    def test_files_and_reconciliation(self):
        report = build(self.dataset, self.pred, self.out)
        stores = report["stores"]
        self.assertEqual((stores["identified.tsv"]["distinct_heads"], stores["masked.tsv"]["distinct_heads"]), (2, 1))
        with self.assertRaises(FileExistsError):
            build(self.dataset, self.pred, self.out)
        facts, pred, _ = load_inputs(self.dataset, self.pred)
        maps = vault.load_mappings(self.out / "vault")
        release = self.out / "release/F"
        def read_rows(name):
            with (release / name).open(encoding="utf-8", newline="") as stream:
                return list(csv.reader(stream, delimiter="\t"))

        header, *identified = read_rows("identified.tsv")
        _, masked = read_rows("masked.tsv")
        self.assertEqual(len({tail for _, _, tail in identified}), 2)
        self.assertIn(masked[2], [row[2] for row in identified])
        # Thay một dòng bằng dòng khác vẫn giữ nguyên tổng số dòng.
        misrouted = [maps["customers"]["a"]["c_v"], masked[1], masked[2]]
        for forged in ([identified[0], identified[0]], [identified[0], misrouted], [identified[0]]):
            with self.subTest(forged=forged):
                with (release / "identified.tsv").open("w", encoding="utf-8", newline="") as stream:
                    csv.writer(stream, delimiter="\t").writerows([header, *forged])
                with self.assertRaises(ValueError):
                    reconcile(release, facts, pred, maps)


if __name__ == "__main__":
    unittest.main()
