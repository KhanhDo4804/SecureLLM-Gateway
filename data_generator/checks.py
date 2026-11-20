"""Semantic checks for the pre-M1 banking dataset (scenario sections 5–6 and C).

The caller checks physical schemas, keys, references and ISO date syntax before
calling validate_business. All calendar-day cutoffs use Vietnam local time.
"""

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone


VN = timezone(timedelta(hours=7))


def _day(timestamp):
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(VN).date().isoformat()


def _inside(day, start, end):
    return start <= day and (end is None or day < end)


def _age(birth, day):
    birth, day = date.fromisoformat(birth), date.fromisoformat(day)
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def _luhn(pan):
    if not pan or not pan.isascii() or not pan.isdigit():
        return False
    digits = [int(digit) for digit in reversed(pan)]
    return sum(d if i % 2 == 0 else (2 * d if d < 5 else 2 * d - 9)
               for i, d in enumerate(digits)) % 10 == 0


def _history(table, rows, group_key, issue):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in group_key)].append((row["valid_from"], row["valid_to"]))
    for owner, periods in groups.items():
        prior_end = ""
        for start, end in sorted(periods, key=lambda period: period[0]):
            if end is not None and end <= start:
                issue("invalid_interval", f"{table} {owner}: empty or reversed interval at {start}")
            if prior_end is None or start < prior_end:
                issue("overlapping_history", f"{table} {owner}: overlapping records at {start}")
            if prior_end is not None:
                prior_end = None if end is None else max(prior_end, end)


def _check_bank(t, cfg, issue):
    cutoff = cfg["as_of"]
    clients = {r["customer_id"]: r for r in t["customers"]}
    accounts = {r["account_id"]: r for r in t["accounts"]}
    cards = {r["card_id"]: r for r in t["cards"]}
    roles = {r["staff_id"]: r["role_code"] for r in t["staff"]}
    if set(clients) != {r["customer_id"] for r in t["customer_pii"]}:
        issue("missing_pii_row", "customer_pii must cover every generated customer")
    for r in t["customers"]:
        if _age(r["birth_on"], cutoff) < 18:
            issue("underage_customer", r["customer_id"])
    for r in t["areas"]:
        if r["catalog_version"] != cfg.get("catalog_version", "demo-v1"):
            issue("catalog_version", r["area_id"])
    for r in t["merchants"]:
        if not re.fullmatch(r"[0-9]{4}", r["mcc"]):
            issue("invalid_mcc", r["merchant_id"])
    for r in t["staff"]:
        if r["role_code"] not in {"RM", "CSKH", "INTERNAL_CONTROL"}:
            issue("staff_role", r["staff_id"])
    _history("rm_assignments", t["rm_assignments"], ["customer_id"], issue)
    _history("employment", t["employment"], ["customer_id", "company_id"], issue)
    for r in t["rm_assignments"]:
        if roles[r["staff_id"]] != "RM":
            issue("assignment_role", f'{r["customer_id"]}: assigned staff is not an RM')
    for r in t["cases"]:
        if r["closed_at"] is not None and datetime.fromisoformat(r["closed_at"]) < datetime.fromisoformat(r["opened_at"]):
            issue("case_dates", r["case_id"])
    for r in t["customer_pii"]:
        cid = r["customer_id"]
        if r["email_test"] and not re.fullmatch(r"[^@\s]+@example\.com", r["email_test"], flags=re.I):
            issue("synthetic_email", cid)
        if r["national_id_test"] and not re.fullmatch(r"(?:[0-9]{12}|TEST[-_].+)", r["national_id_test"]):
            issue("synthetic_national_id", cid)
        if r["phone_test"]:
            phone = re.sub(r"[\s.()-]", "", r["phone_test"])
            if not re.fullmatch(r"(?:0|\+84)[35789][0-9]{8}|TEST[-_].+", phone):
                issue("synthetic_phone", cid)
    for aid, a in accounts.items():
        if not re.fullmatch(r"99[0-9]{12}", a["account_no_test"]):
            issue("synthetic_account_number", aid)
        if a["closed_on"] is not None and a["closed_on"] <= a["opened_on"]:
            issue("account_dates", aid)
        if _age(clients[a["customer_id"]]["birth_on"], a["opened_on"]) < 18:
            issue("product_age", aid)
    for card_id, c in cards.items():
        a = accounts[c["account_id"]]
        if not _inside(c["issued_on"], a["opened_on"], a["closed_on"]):
            issue("card_issue_dates", card_id)
        if c["closed_on"] is not None and c["closed_on"] <= c["issued_on"]:
            issue("card_dates", card_id)
        if a["closed_on"] and (not c["closed_on"] or c["closed_on"] > a["closed_on"]):
            issue("card_outlives_account", card_id)
        if c["card_kind"] not in {"DEBIT", "CREDIT"} or (
            c["card_kind"] == "CREDIT" and (c["credit_limit_vnd"] or 0) <= 0
        ) or (c["card_kind"] == "DEBIT" and c["credit_limit_vnd"] is not None):
            issue("card_limit", card_id)
        if not re.fullmatch(r"999[0-9]{13}", c["pan_test"]) or not _luhn(c["pan_test"]):
            issue("card_pan_luhn", card_id)
    for r in t["transactions"]:
        tid, aid, kind = r["txn_id"], r["account_id"], r["kind"]
        a, day = accounts[aid], _day(r["posted_at"])
        if r["amount_vnd"] <= 0 or r["status"] not in {"POSTED", "CANCELLED"}:
            issue("transaction_domain", tid)
        if not ((kind == "SALARY" and r["payer_company_id"] and r["merchant_id"] is None)
                or (kind == "PURCHASE" and r["merchant_id"] and r["payer_company_id"] is None)):
            issue("transaction_counterparty", tid)
        if r["status"] == "POSTED" and not _inside(day, a["opened_on"], a["closed_on"]):
            issue("transaction_account_dates", tid)
        if r["card_id"] is not None:
            card = cards[r["card_id"]]
            if card["account_id"] != aid:
                issue("transaction_card_owner", tid)
            if r["status"] == "POSTED" and not _inside(day, card["issued_on"], card["closed_on"]):
                issue("transaction_card_dates", tid)
    for r in t["customer_financials"]:
        income, score = r["monthly_income_vnd"], r["credit_score"]
        if r["as_of_on"] != cutoff or (income is not None and income < 0) or (
            score is not None and not 300 <= score <= 850
        ):
            issue("financial_snapshot", r["customer_id"])


def _check_loans(t, cfg, issue):
    cutoff = cfg["as_of"]
    births = {r["customer_id"]: r["birth_on"] for r in t["customers"]}
    loans = {r["loan_id"]: r for r in t["loans"]}
    schedules = defaultdict(list)
    for inst in t["installments"]:
        schedules[inst["loan_id"]].append(inst)
    paid, paid_by_cutoff = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for row in t["payment_allocations"]:
        key = row["loan_id"], row["installment_no"]
        day = _day(row["paid_at"])
        principal, interest = row["principal_paid_vnd"], row["interest_paid_vnd"]
        if min(principal, interest) < 0 or principal + interest <= 0 or day < loans[key[0]]["disbursed_on"]:
            issue("payment_allocation", f'{key}: invalid amount or accounting date')
        paid[key][0] += principal
        paid[key][1] += interest
        if day <= cutoff:
            paid_by_cutoff[key][0] += principal
            paid_by_cutoff[key][1] += interest

    snapshots = {}
    for r in t["loan_snapshot"]:
        if r["as_of_on"] != cutoff:
            issue("snapshot_cutoff", r["loan_id"])
        else:
            snapshots[r["loan_id"]] = r
    for lid, loan in loans.items():
        if loan["principal_vnd"] <= 0 or loan["maturity_on"] <= loan["disbursed_on"]:
            issue("loan_contract", lid)
        if _age(births[loan["customer_id"]], loan["disbursed_on"]) < 18:
            issue("product_age", lid)
        insts = sorted(schedules[lid], key=lambda r: r["installment_no"])
        dues = [r["due_on"] for r in insts]
        if dues != sorted(dues) or any(not loan["disbursed_on"] <= d <= loan["maturity_on"] for d in dues):
            issue("installment_dates", lid)
        if sum(r["principal_due_vnd"] for r in insts) != loan["principal_vnd"]:
            issue("installment_principal", lid)
        outstanding = loan["principal_vnd"]
        max_dpd = 0
        for r in insts:
            key = lid, r["installment_no"]
            pdue, idue = r["principal_due_vnd"], r["interest_due_vnd"]
            if r["installment_no"] <= 0 or min(pdue, idue) < 0 or pdue + idue <= 0:
                issue("installment_amount", str(key))
            if paid[key][0] > pdue or paid[key][1] > idue:
                issue("payment_overallocated", str(key))
            p, i = paid_by_cutoff[key]
            outstanding -= p
            if r["due_on"] < cutoff and (p < pdue or i < idue):
                lag = (date.fromisoformat(cutoff) - date.fromisoformat(r["due_on"])).days
                max_dpd = max(max_dpd, lag)
        snap = snapshots.get(lid)
        if loan["disbursed_on"] > cutoff:
            if snap:
                issue("future_loan_snapshot", lid)
            continue
        group = 1 + sum(max_dpd > limit for limit in (9, 90, 180, 360)) if outstanding > 0 else None
        if not snap or (snap["as_of_on"], snap["outstanding_vnd"], snap["days_past_due"], snap["debt_group"]) != (
            cutoff, outstanding, max_dpd, group
        ):
            issue("loan_snapshot_reconciliation", lid)


def _check_texts(t, cfg, issue):
    from .features import pii_values

    splits = {r["customer_id"]: r["split"] for r in t["split_customers"]}
    families = {r["family_id"]: r["split"] for r in t["split_families"]}
    if set(splits) != {r["customer_id"] for r in t["customers"]}:
        issue("split_customer_coverage", "missing split assignment")
    if (set(splits.values()) | set(families.values())) - {"train", "validation", "test"}:
        issue("split_domain", "unrecognized partition")
    facts = {r["fact_id"]: r for r in t["facts"]}
    inputs = {r["fact_id"]: r for r in t["facts_input"]}
    texts = {r["text_id"]: r for r in t["texts"]}
    for tid, r in texts.items():
        if splits.get(r["customer_id"]) != families.get(r["family_id"]):
            issue("split_leakage", tid)
        if unicodedata.normalize("NFC", r["body"]) != r["body"]:
            issue("text_normalization", tid)
    allowed_fields = defaultdict(set)
    for r in t["fact_texts"]:
        fact = facts[r["fact_id"]]
        tid = r["text_id"]
        if fact["customer_id"] != texts[tid]["customer_id"]:
            issue("fact_text_owner", r["fact_id"])
        allowed_fields[tid].add(fact["predicate"])

    spans = defaultdict(set)
    catalog = pii_values(t)
    for r in t["pii_spans_gold"]:
        tid = r["text_id"]
        start, end, kind = r["start_cp"], r["end_cp"], r["pii_type"]
        rec = texts[tid]
        if not 0 <= start < end <= len(rec["body"]):
            issue("span_bounds", tid)
            continue
        value = rec["body"][start:end]
        if value not in catalog.get(rec["customer_id"], {}).get(kind, ()):
            issue("span_value", f"{tid}: {kind} disagrees with source at [{start},{end})")
        spans[tid].add((start, end, kind))
        allowed_fields[tid].add(kind)

    for r in t["rag_chunks"]:
        tid = r["text_id"]
        rec = texts[tid]
        if r["customer_id"] != rec["customer_id"]:
            issue("rag_chunk_owner", r["chunk_id"])
        if not r["content"] or r["content"] not in rec["body"]:
            issue("rag_chunk_content", r["chunk_id"])
        if r["field_code"] not in allowed_fields[tid]:
            issue("rag_chunk_field", r["chunk_id"])

    for tid, rec in texts.items():
        # An occurrence contained entirely in a longer annotation need not
        # acquire a separate label of its own.
        body = rec["body"]
        annotated = spans[tid]
        for kind, values in catalog.get(rec["customer_id"], {}).items():
            for value in set(values):
                if not value:
                    continue
                start = body.find(value)
                while start != -1:
                    end = start + len(value)
                    if (start, end, kind) not in annotated and not any(
                        a <= start and end <= b and (a, b) != (start, end) for a, b, _ in annotated
                    ):
                        issue("missing_pii_span", f"{tid}: {kind} at [{start},{end})")
                    start = body.find(value, end)
    expected_test = {fid for fid, f in facts.items() if splits.get(f["customer_id"]) == "test"}
    test = {r["fact_id"]: r for r in t["facts_test"]}
    if set(test) != expected_test:
        issue("test_view_membership", "facts_test differs from the held-out customer set")
    if any(r != inputs.get(fid) for fid, r in test.items()):
        issue("test_view_values", "facts_test differs from the corresponding facts_input records")


def _check_derived(t, cfg, issue):
    from .features import derive_facts

    expected = derive_facts(t, cfg)
    keys = {
        "facts": ("fact_id",),
        "object_nodes": ("object_node_id",),
        "facts_gold": ("fact_id",),
        "fact_sources": ("fact_id", "source_table", "source_key"),
        "facts_input": ("fact_id",),
    }
    for name, fields in keys.items():
        actual = {tuple(row[k] for k in fields): row for row in t[name]}
        for row in expected[name]:
            key = tuple(row[k] for k in fields)
            if actual.pop(key, None) != row:
                issue("derived_" + name, f"{name} {key}: missing or inconsistent with source")
        for key in actual:
            issue("derived_" + name, f"{name} {key}: unexpected record")


def validate_business(tables, cfg, issue):
    """Report semantic violations through issue(code, message). Does not modify inputs."""
    _check_bank(tables, cfg, issue)
    _check_loans(tables, cfg, issue)
    _check_texts(tables, cfg, issue)
    _check_derived(tables, cfg, issue)
