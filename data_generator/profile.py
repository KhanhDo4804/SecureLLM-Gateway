"""Descriptive checks of the generated corpus before masking or MOVE."""
from collections import Counter, defaultdict
from math import ceil


def distribution(values):
    v = sorted(values)
    if not v:
        return {"n": 0}
    result = {"n": len(v), "min": v[0], "max": v[-1], "mean": round(sum(v) / len(v), 3)}
    for p in (10, 50, 90, 99):
        result[f"p{p}"] = v[max(0, ceil(p * len(v) / 100) - 1)]
    return result


def profile(t):
    gold = {r["fact_id"]: r["is_sensitive"] for r in t["facts_gold"]}
    customers = [r["customer_id"] for r in t["customers"]]
    split = {r["customer_id"]: r["split"] for r in t["split_customers"]}
    objects = {r["object_node_id"]: r["node_type"] for r in t["object_nodes"]}
    ordinary = Counter()
    neighbors = defaultdict(set)
    anchor_customers = defaultdict(set)
    classes = defaultdict(Counter)
    subjects = set()
    employed, salary = set(), set()
    for row in t["facts"]:
        cid, obj = row["customer_id"], row["object_node_id"]
        sensitive = gold[row["fact_id"]]
        classes[split[cid]]["sensitive" if sensitive else "ordinary"] += 1
        anchor_customers[obj].add(cid)
        if sensitive:
            subjects.add(cid)
        else:
            ordinary[cid] += 1
            neighbors[cid].add(obj)
        if row["predicate"] == "làm_việc_tại":
            employed.add((cid, obj))
        elif row["predicate"] == "nhận_lương_từ":
            salary.add((cid, obj))
    degrees = defaultdict(list)
    for obj, users in anchor_customers.items():
        degrees[objects[obj]].append(len(users))
    outside = sum(not 15 <= ordinary[c] <= 40 for c in customers)
    return {
        "customers": len(customers), "subjects_with_sensitive_fact": len(subjects),
        "source_transactions": len(t["transactions"]), "distinct_facts": len(t["facts"]),
        "d_v_ordinary_facts": distribution(ordinary[c] for c in customers),
        "g_v_ordinary_neighbors": distribution(len(neighbors[c]) for c in customers),
        "customers_outside_15_to_40": outside, "fraction_outside_15_to_40": outside / len(customers),
        "employment_salary_same_customer_company_pairs": len(employed & salary),
        "customers_by_split": dict(Counter(split.values())),
        "template_families_by_split": dict(Counter(r["split"] for r in t["split_families"])),
        "fact_labels_by_split": dict(classes),
        "anchor_degrees_before_masking": {
            kind: {**distribution(ds), "degree_one_fraction": sum(d == 1 for d in ds) / len(ds)}
            for kind, ds in sorted(degrees.items())
        },
    }
