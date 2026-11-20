"""Descriptive pilot checks for source data at T (scenario §6.4).

Thresholds concern the simulated model, not actual bank customers. Business
constraints and release decisions are handled separately by the validator/gate.
"""

from collections import Counter, defaultdict
from datetime import date
from math import sqrt

from .features import _local_date, _start_exclusive
from .profile import distribution


def _ratio(count, total):
    return round(count / total, 5) if total else None


def _correlation(pairs):
    """Pearson's r with pairwise deletion; undefined if a variable is constant."""
    if len(pairs) < 2:
        return {"pairs": len(pairs), "pearson_r": None}
    x_mean = sum(x for x, _ in pairs) / len(pairs)
    y_mean = sum(y for _, y in pairs) / len(pairs)
    cross = sum((x-x_mean)*(y-y_mean) for x, y in pairs)
    scale = sqrt(sum((x-x_mean)**2 for x, _ in pairs) * sum((y-y_mean)**2 for _, y in pairs))
    return {"pairs": len(pairs), "pearson_r": round(cross / scale, 5) if scale else None}


def _income_and_debt(t):
    occupation = {r["customer_id"]: r["occupation_code"] or "UNDECLARED" for r in t["customers"]}
    borrower = {r["loan_id"]: r["customer_id"] for r in t["loans"]}
    highest = {}
    for r in t["loan_snapshot"]:
        owner = borrower[r["loan_id"]]
        if r["outstanding_vnd"] > 0:
            highest[owner] = max(highest.get(owner, 0), r["debt_group"])
    incomes, scores = defaultdict(list), defaultdict(list)
    employment_counts, active_counts = Counter(occupation.values()), Counter(highest.values())
    income_score, credit_debt = [], []
    missing_income = 0
    for r in t["customer_financials"]:
        customer = r["customer_id"]
        income, score = r["monthly_income_vnd"], r["credit_score"]
        if income is None:
            missing_income += 1
        else:
            incomes[occupation[customer]].append(income)
            if score is not None:
                income_score.append((income, score))
        if customer in highest and score is not None:
            group = highest[customer]
            scores[group].append(score)
            credit_debt.append((score, group))
    return {
        "monthly_income_vnd_by_occupation": {
            k: {"customers": n, "missing": n-len(incomes[k]), **distribution(incomes[k])}
            for k, n in sorted(employment_counts.items())
        },
        "credit_score_by_worst_active_debt_group": {
            f"G{g}": {"active_borrowers": active_counts[g],
                       "missing_score": active_counts[g]-len(scores[g]), **distribution(scores[g])}
            for g in range(1, 6)
        },
        "income_and_credit_score": _correlation(income_score),
        "credit_score_and_worst_active_debt_group_ordinal": _correlation(credit_debt),
    }, missing_income


def _locality(t, cutoff):
    province = {r["area_id"]: r["province_code"] for r in t["areas"]}
    residence = {r["customer_id"]: province[r["residence_area_id"]] for r in t["customers"]}
    branches = {r["branch_id"]: province[r["area_id"]] for r in t["branches"]}
    rms = {r["staff_id"]: branches[r["branch_id"]] for r in t["staff"] if r["role_code"] == "RM"}
    load = Counter()
    same = 0
    for r in t["rm_assignments"]:
        if r["valid_from"] <= cutoff and (not r["valid_to"] or cutoff < r["valid_to"]):
            rm = r["staff_id"]
            load[rm] += 1
            same += rms[rm] == residence[r["customer_id"]]
    active = sum(load.values())
    return {"active_assignments": active, "same_province_as_rm_branch": same,
            "same_province_fraction": _ratio(same, active),
            "customers_per_rm_including_empty_portfolios": distribution(load[r] for r in rms)}


def _activity(t, cutoff):
    as_of = date.fromisoformat(cutoff)
    lower = _start_exclusive(as_of)
    owner = {r["account_id"]: r["customer_id"] for r in t["accounts"]}
    use, cancellations, in_window = Counter(), 0, 0
    merchant_contacts, device_contacts = defaultdict(set), defaultdict(set)
    purchase_values = []
    for r in t["transactions"]:
        if not lower < _local_date(r["posted_at"]) <= as_of:
            continue
        in_window += 1
        cancellations += r["status"] == "CANCELLED"
        if r["status"] != "POSTED":
            continue
        customer = owner[r["account_id"]]
        if r["device_id"]:
            device_contacts[customer].add(r["device_id"])
        if r["kind"] == "PURCHASE":
            merchant = r["merchant_id"]
            merchant_contacts[customer].add(merchant)
            use[merchant] += 1
            purchase_values.append(r["amount_vnd"])
    customers = [r["customer_id"] for r in t["customers"]]
    purchased = len(purchase_values)
    return {
        "window": {"after_exclusive": str(lower), "through_inclusive": cutoff},
        "transaction_count": in_window,
        "cancelled_transaction_count": cancellations,
        "posted_purchase_amount_vnd": distribution(purchase_values),
        "distinct_raw_merchants_per_customer": distribution(len(merchant_contacts[c]) for c in customers),
        "distinct_devices_per_customer": distribution(len(device_contacts[c]) for c in customers),
        "top_5_merchants_share_of_posted_purchase_events": _ratio(sum(n for _, n in use.most_common(5)), purchased),
    }


def diagnostics(t, cfg):
    """Return a JSON report. Statistical warnings never cause a hard failure."""
    finance, missing = _income_and_debt(t)
    catalogues = {"area": len(t["areas"]), "branch": len(t["branches"]), "company": len(t["companies"]),
                  "staff": sum(r["role_code"] == "RM" for r in t["staff"]),
                  "device": len(t["devices"]), "merchant": len(t["merchants"])}
    node_types = {r["object_node_id"]: r["node_type"] for r in t["object_nodes"]}
    observed = defaultdict(set)
    for r in t["facts"]:
        key = r["object_node_id"]
        observed[node_types[key]].add(key)
    activity = _activity(t, cfg["as_of"])
    report = {
        "financial_profiles": finance, "rm_geography": _locality(t, cfg["as_of"]),
        "transaction_activity": activity,
        "catalogue_usage_in_normalized_facts": {
            k: {"catalogue": n, "used": len(observed[k]), "unused": n-len(observed[k])}
            for k, n in catalogues.items()
        },
        "pilot_tolerances": {}, "warnings": [],
    }
    # Broad predeclared tolerances relative to the generator's Bernoulli
    # probabilities. Small samples are reported without testing these bounds.
    def monitor(key, count, n, low, high, min_n, reason):
        value = _ratio(count, n)
        status = "INSUFFICIENT_SAMPLE" if n < min_n else (
            "WITHIN_RANGE" if low <= count / n <= high else "WARNING"
        )
        report["pilot_tolerances"][key] = {
            "count": count, "denominator": n, "fraction": value, "range": [low, high],
            "minimum_sample": min_n, "status": status, "basis": reason,
        }
        if status == "WARNING":
            report["warnings"].append(key)

    monitor("missing_monthly_income", missing, len(t["customer_financials"]), .02, .20, 200,
            "Demo generator leaves approximately 10% of incomes missing.")
    monitor("cancelled_transactions", activity["cancelled_transaction_count"], activity["transaction_count"],
            .005, .06, 500, "Demo generator cancels approximately 2.5% of source events.")
    return report
