"""Chạy E5 trên các ca có đáp án lập sẵn cho bank_4000_medium_s4242."""
import argparse
from datetime import date, datetime
import json
from math import ceil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from . import ControlScope, DATASET, Policy, VERSION


def ts(value):
    return datetime.fromisoformat(value)


def fixture_policy(catalog):
    staff = [{"staff_id": i, "role_code": role} for i, role in (
        ("r", "RM"), ("s", "CSKH"), ("i", "INTERNAL_CONTROL"), ("a", "ANALYST"),
        ("e", "EXTERNAL_RESEARCHER"), ("k", "KEY_ADMIN"), ("x", "OTHER"))]
    assignments = [{"staff_id": "r", "customer_id": "A", "valid_from": date(2026, 5, 2),
                    "valid_to": date(2026, 5, 4)}]
    cases = []
    for cid, sid, customer, purpose in (
        ("rm", "r", "A", "ACCOUNT_REVIEW"), ("support", "s", "A", "CUSTOMER_SERVICE"),
        ("audit", "i", "A", "CONTROL_REVIEW"), ("no-scope", "i", "A", "CONTROL_REVIEW"),
        ("other-purpose", "s", "A", "ACCOUNT_REVIEW"), ("outside", "r", "B", "ACCOUNT_REVIEW")):
        cases.append(dict(case_id=cid, staff_id=sid, customer_id=customer, purpose_code=purpose,
                          opened_at=ts("2026-05-01T00:00:00+07:00"), closed_at=None))
    scope = ControlScope(frozenset(["ACCOUNT"]), ts("2026-05-02T00:00:00+07:00"),
                         ts("2026-05-04T00:00:00+07:00"))
    return Policy(staff, assignments, cases, catalog, {"audit": scope})


def examples():
    # Các kỳ vọng là đáp án viết tay, độc lập với bảng luật và kết quả authorize().
    rows = []

    def add(code, source, base, expected, **changes):
        rows.append((code, source, {**base, **changes}, expected))

    def no(reason):
        return False, "deny", "deny", reason

    local = (True, "deny", "full", "ALLOW")
    full = (True, "full", "full", "ALLOW")
    partial = (True, "masked", "masked", "ALLOW")
    real = dict(staff_id="RM0065", customer_id="CU000004", case_id="CASE000004",
                purpose_code="ACCOUNT_REVIEW", field_code="ACCOUNT",
                requested_at=ts("2026-05-19T15:34:00+07:00"))
    add("bank_open", "dataset", real, local)
    add("bank_preopen", "dataset", real, no("CASE_NOT_OPEN_YET"), requested_at=ts("2026-05-19T15:33:59+07:00"))
    add("bank_last_second", "dataset", real, local, requested_at=ts("2026-06-24T15:56:59+07:00"))
    add("bank_closed", "dataset", real, no("CASE_CLOSED"), requested_at=ts("2026-06-24T15:57:00+07:00"))
    add("bank_other_owner_case", "dataset", real, no("CASE_SUBJECT_MISMATCH"),
        customer_id="CU000001", case_id="CASE000001")
    add("bank_wrong_purpose", "dataset", real, no("PURPOSE_NOT_PERMITTED"), purpose_code="CUSTOMER_SERVICE")
    add("bank_unknown_case", "dataset", real, no("UNKNOWN_CASE"), case_id="missing")

    rm = dict(real, staff_id="r", customer_id="A", case_id="rm", requested_at=ts("2026-05-03T12:00:00+07:00"))
    add("portfolio_before_start", "fixture", rm, no("NO_ACTIVE_ASSIGNMENT"), requested_at=ts("2026-05-01T16:59:59+00:00"))
    add("portfolio_start", "fixture", rm, local, requested_at=ts("2026-05-01T17:00:00+00:00"))
    add("portfolio_last_second", "fixture", rm, local, requested_at=ts("2026-05-03T16:59:59+00:00"))
    add("portfolio_end", "fixture", rm, no("NO_ACTIVE_ASSIGNMENT"), requested_at=ts("2026-05-03T17:00:00+00:00"))
    # Hai phần của một yêu cầu: cùng người dùng, hồ sơ, thời điểm, loại trường.
    add("two_customers_A", "fixture", rm, local)
    add("two_customers_B", "fixture", rm, no("CASE_SUBJECT_MISMATCH"), customer_id="B")
    add("open_case_outside_portfolio", "fixture", rm, no("NO_ACTIVE_ASSIGNMENT"), customer_id="B", case_id="outside")
    add("rm_basic", "fixture", rm, full, field_code="mở_tài_khoản_tại")
    add("rm_sensitive_fact", "fixture", rm, local, field_code="nhóm_nợ")
    add("rm_contact", "fixture", rm, (True, "masked", "full", "ALLOW"), field_code="PHONE")
    add("rm_forbidden_id", "fixture", rm, no("FIELD_NOT_PERMITTED"), field_code="NATIONAL_ID")
    add("unknown_field", "fixture", rm, no("UNKNOWN_FIELD"), field_code="new_field")
    add("unknown_purpose", "fixture", rm, no("PURPOSE_NOT_PERMITTED"), purpose_code="OTHER")
    add("unknown_staff", "fixture", rm, no("UNKNOWN_STAFF"), staff_id="unknown")
    add("naive_timestamp", "fixture", rm, no("INVALID_REQUEST_TIME"), requested_at=ts("2026-05-03T12:00:00"))

    support = dict(rm, staff_id="s", case_id="support", purpose_code="CUSTOMER_SERVICE")
    add("support_basic", "fixture", support, full, field_code="được_quản_lý_bởi")
    for field in ("NAME", "PHONE", "EMAIL", "ADDRESS", "ACCOUNT", "sống_tại"):
        add("support_partial_" + field, "fixture", support, partial, field_code=field)
    add("support_credit", "fixture", support, no("FIELD_NOT_PERMITTED"), field_code="hạng_điểm")
    add("support_card", "fixture", support, no("FIELD_NOT_PERMITTED"), field_code="CARD")
    add("support_mismatch", "fixture", support, no("CASE_PURPOSE_MISMATCH"), case_id="other-purpose")

    audit = dict(rm, staff_id="i", case_id="audit", purpose_code="CONTROL_REVIEW")
    add("control_approved", "fixture", audit, local)
    add("control_field_outside", "fixture", audit, no("OUTSIDE_CONTROL_SCOPE"), field_code="EMAIL")
    add("control_no_scope", "fixture", audit, no("CONTROL_SCOPE_MISSING"), case_id="no-scope")
    add("control_before", "fixture", audit, no("CONTROL_SCOPE_INACTIVE"), requested_at=ts("2026-05-01T12:00:00+07:00"))
    add("control_expired", "fixture", audit, no("CONTROL_SCOPE_INACTIVE"), requested_at=ts("2026-05-04T00:00:00+07:00"))
    for sid, label in (("a", "analyst"), ("e", "researcher"), ("k", "key_admin"), ("x", "unknown_role")):
        add(label, "fixture", rm, no("ROLE_NOT_PERMITTED"), staff_id=sid)
    return rows


def evaluate():
    bank = Policy.load()
    policies = {"dataset": bank, "fixture": fixture_policy(bank.field_codes)}
    rows = examples()
    totals = {name: dict(expected_allow=0, expected_deny=0, false_allow=0, false_deny=0,
                         model_mismatches=0, display_mismatches=0, failed_cases=0) for name in policies}
    logs = []
    for code, source, request, expected in rows:
        d = policies[source].authorize(**request)
        actual = (d.can_read, d.model_level, d.display_level, d.reason_code)
        t = totals[source]
        t["expected_allow" if expected[0] else "expected_deny"] += 1
        t["false_allow"] += d.can_read and not expected[0]
        t["false_deny"] += expected[0] and not d.can_read
        t["model_mismatches"] += d.model_level != expected[1]
        t["display_mismatches"] += d.display_level != expected[2]
        t["failed_cases"] += actual != expected or d.policy_version != "bank-access-demo-v1"
        logs.append({"case_code": code, "decision": {"can_read": d.can_read, "model_level": d.model_level,
                     "display_level": d.display_level}, "reason_code": d.reason_code})

    timings = []
    for _ in range(100):
        for _, source, request, _ in rows:
            authorize = policies[source].authorize
            start = perf_counter_ns()
            authorize(**request)
            timings.append(perf_counter_ns() - start)
    timings.sort()
    latency = {"calls": len(timings), "includes_parquet_load": False}
    for p in (50, 95):
        latency[f"p{p}_us"] = timings[ceil(p * len(timings) / 100) - 1] / 1000
    overall = {k: sum(t[k] for t in totals.values()) for k in totals["dataset"]}
    return dict(dataset=DATASET.name, policy_version=VERSION, cases=len(rows), by_source=totals,
                overall=overall, latency=latency), logs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("experiments/e5/m3_policy"))
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Use --out to choose a new result directory.")
    report, logs = evaluate()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.out.parent) as tmp:
        stage = Path(tmp) / "result"
        stage.mkdir()
        (stage / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (stage / "decisions.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in logs),
                                              encoding="utf-8")
        stage.rename(args.out)
    print(json.dumps(report, indent=2))
    if report["overall"]["failed_cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
