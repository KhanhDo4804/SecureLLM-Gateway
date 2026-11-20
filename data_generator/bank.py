"""Synthetic retail-banking source rows for Appendix C.2–C.4 and C.6 cases.

The probabilities, mock catalogues and amounts are demo parameters. All money
is integer VND. Closed dates and valid_to dates are exclusive.
"""

import calendar
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from math import exp
import unicodedata


VN = timezone(timedelta(hours=7))
MERCHANT_TYPES = (
    ("5411", "Siêu thị"), ("5812", "Quán ăn"), ("5651", "Cửa hàng thời trang"),
    ("5942", "Nhà sách"), ("5541", "Trạm nhiên liệu"), ("5912", "Nhà thuốc"),
    ("8062", "Bệnh viện"), ("8661", "Cơ sở tôn giáo"),
)
OCCUPATIONS = {
    "OFFICE": 16_000_000, "TECHNICIAN": 21_000_000, "SERVICE": 11_000_000,
    "SELF_EMPLOYED": 14_000_000, "RETIRED": 8_000_000, None: 9_000_000,
}
SOURCE_NAMES = (
    "areas", "branches", "staff", "companies", "merchants", "devices",
    "customers", "customer_pii", "rm_assignments", "employment", "accounts",
    "cards", "transactions", "customer_financials", "loans", "installments",
    "payment_allocations", "cases",
)
DENSITY_CONTACTS = {"sparse": (2, 7), "medium": (9, 23), "dense": (20, 40)}


def _month(day, offset):
    index = day.year * 12 + day.month - 1 + offset
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _timestamp(day, rng):
    return datetime.combine(day, time(rng.randrange(7, 22), rng.randrange(60)), VN).isoformat()


def _date_between(start, end, rng):
    return start + timedelta(days=rng.randrange((end - start).days + 1))


def _round_money(value):
    return max(1_000, round(value / 1_000) * 1_000)


def _catalogues(cfg, rng, tables):
    counts = cfg["counts"]
    for key in ("areas", "branches", "rms", "companies", "merchants", "devices"):
        if counts[key] < 1:
            raise ValueError(f"counts.{key} must be positive")
    areas = tables["areas"]
    for i in range(counts["areas"]):
        areas.append(dict(area_id=f"AR{i+1:04}", province_code=f"DEMO-P{i%8+1:02}",
                          area_name=f"Phường mô phỏng {i+1}", catalog_version=cfg.get("catalog_version", "demo-v1")))
    for i in range(counts["branches"]):
        tables["branches"].append(dict(branch_id=f"BR{i+1:04}", area_id=areas[i % len(areas)]["area_id"],
                                       branch_name=f"Chi nhánh mô phỏng {i+1}"))
    branches = tables["branches"]
    for i in range(counts["rms"]):
        tables["staff"].append(dict(staff_id=f"RM{i+1:04}", branch_id=branches[i % len(branches)]["branch_id"],
                                    role_code="RM"))
    for i, branch in enumerate(branches):
        for prefix, role in (("CS", "CSKH"), ("IC", "INTERNAL_CONTROL")):
            tables["staff"].append(dict(staff_id=f"{prefix}{i+1:04}", branch_id=branch["branch_id"], role_code=role))
    for i in range(counts["companies"]):
        tables["companies"].append(dict(company_id=f"CO{i+1:05}", area_id=rng.choice(areas)["area_id"],
                                        company_name=f"Doanh nghiệp mô phỏng {i//2+1}"))
    for i in range(counts["merchants"]):
        mcc, category = MERCHANT_TYPES[i % len(MERCHANT_TYPES)]
        tables["merchants"].append(dict(merchant_id=f"ME{i+1:05}", area_id=rng.choice(areas)["area_id"],
                                        mcc=mcc, merchant_name=f"{category} mô phỏng {i+1}"))
    for i in range(counts["devices"]):
        tables["devices"].append(dict(device_id=f"DV{i+1:06}", device_kind=rng.choice(("PHONE", "PHONE", "TABLET", "PC"))))


def _identifiers(customer, i, birth, rng):
    name = " ".join((rng.choice(("Trần", "Nguyễn", "Phan", "Lê", "Vũ", "Lý")),
                     rng.choice(("Khánh", "Minh", "Thanh", "Hải", "Ngọc")),
                     rng.choice(("An", "Bình", "Châu", "Hà", "Linh", "Quân", "Vy"))))
    if i % 11 == 0:
        name = "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c))
    phone = f"09{i:08}"
    phone = (phone, "+84" + phone[1:], f"{phone[:4]}.{phone[4:7]}.{phone[7:]}",
             f"{phone[:4]} {phone[4:7]} {phone[7:]}")[i % 4]
    national = f"001{0 if birth.year < 2000 else 2}{birth.year%100:02}{i:06}"
    return dict(customer_id=customer, full_name=name,
                national_id_test=national if i % 5 else f"TEST-CUST-{i:06}",
                phone_test=phone, email_test=f"khach{i:06}@example.com")


def _pan(i):
    # Test-only numeric range; Luhn-valid positive fixtures.
    stem = f"999{i:012}"
    total = sum((int(d) * 2 // 10 + int(d) * 2 % 10) if j % 2 == 0 else int(d)
                for j, d in enumerate(stem))
    return stem + str(-total % 10)


def _accounts(tables, customer, branch, income, start, end, rng):
    instruments = []
    for j in range(1 + int(rng.random() < 0.22)):
        idx = len(tables["accounts"]) + 1
        acc = f"AC{idx:07}"
        opened = start - timedelta(days=rng.randint(1, 360)) if j == 0 else _date_between(start, end - timedelta(days=20), rng)
        closed = _date_between(opened + timedelta(days=10), end, rng) if j and rng.random() < .25 else None
        tables["accounts"].append(dict(account_id=acc, customer_id=customer, branch_id=branch,
                                       opened_on=opened.isoformat(), closed_on=closed.isoformat() if closed else None,
                                       account_no_test=f"99{idx:012}"))
        card = None
        issued = None
        card_closed = None
        if rng.random() < .88:
            index = len(tables["cards"]) + 1
            card = f"CD{index:07}"
            issued = opened + timedelta(days=rng.randint(0, 6))
            card_closed = closed
            if card_closed is None and issued < end - timedelta(days=30) and rng.random() < .06:
                card_closed = _date_between(max(issued + timedelta(days=1), start), end, rng)
            kind = "CREDIT" if income >= 8_000_000 and rng.random() < .42 else "DEBIT"
            tables["cards"].append(dict(card_id=card, account_id=acc, card_kind=kind, issued_on=issued.isoformat(),
                                        closed_on=card_closed.isoformat() if card_closed else None, pan_test=_pan(index),
                                        credit_limit_vnd=_round_money(income * rng.uniform(1, 4)) if kind == "CREDIT" else None))
        instruments.append((acc, opened, closed, card, issued, card_closed))
    return instruments


def _transactions(tables, instruments, employer, income, merchant_pool, devices, start, end, rng):
    txns = tables["transactions"]

    def record(account, day, kind, amount, company=None, merchant=None, card=None, device=None):
        status = "CANCELLED" if rng.random() < .025 else "POSTED"
        txns.append(dict(txn_id=f"TX{len(txns)+1:09}", account_id=account, posted_at=_timestamp(day, rng),
                         kind=kind, status=status, amount_vnd=_round_money(amount),
                         payer_company_id=company, merchant_id=merchant, card_id=card, device_id=device))

    if employer and rng.random() < .86:
        # A separate payroll event is the evidence for salary. Employment alone
        # does not imply a salary transaction.
        for m in range(13):
            day = _month(start.replace(day=1), m).replace(day=25)
            if start <= day <= end and rng.random() < .90:
                record(instruments[0][0], day, "SALARY", income * rng.uniform(.83, 1.19), company=employer)
    for merchant in merchant_pool:
        for _ in range(1 + int(rng.random() < .55) + int(rng.random() < .22)):
            acc, opened, closed, card, issued, card_closed = rng.choice(instruments)
            day = _date_between(max(opened, start), min(end, closed - timedelta(days=1)) if closed else end, rng)
            if card and (day < issued or (card_closed and day >= card_closed) or rng.random() < .3):
                card = None
            device = rng.choice(devices) if rng.random() < .85 else None
            amount = min(income * .4, max(5_000, income * .009 * exp(rng.gauss(0, 1.05))))
            record(acc, day, "PURCHASE", amount, merchant=merchant, card=card, device=device)


def _loans(tables, customer, income, score, start, end, rng):
    if rng.random() > .53:
        return
    for _ in range(1 + int(rng.random() < .24)):
        number = len(tables["loans"]) + 1
        loan = f"LN{number:07}"
        disbursed = _date_between(start - timedelta(days=110), end - timedelta(days=10), rng)
        term = rng.choice((6, 9, 12, 18, 24))
        principal = _round_money(income * rng.uniform(.8, .32 * term))
        maturity = _month(disbursed, term)
        tables["loans"].append(dict(loan_id=loan, customer_id=customer, loan_type="CONSUMER",
                                    disbursed_on=disbursed.isoformat(), principal_vnd=principal, maturity_on=maturity.isoformat()))
        # An occasional interruption in payments adds noise to the score/arrears relationship.
        if rng.random() < max(.07, min(.50, (810 - score) / 730)):
            stop = rng.randrange(1, term + 1)
        else:
            stop = term + 1
        for seq in range(1, term + 1):
            due = _month(disbursed, seq)
            p = principal // term + int(seq <= principal % term)
            interest = max(0, round((principal - (seq-1)*principal/term) * .011))
            tables["installments"].append(dict(loan_id=loan, installment_no=seq, due_on=due.isoformat(),
                                               principal_due_vnd=p, interest_due_vnd=interest))
            if due > end or seq > stop:
                continue
            # Some installments receive two payments, and the first missed
            # installment may receive only a partial payment.
            split = rng.random() < .24
            parts = [(p, interest)]
            if seq == stop:
                parts = [(p // 2, interest // 2)] if rng.random() < .5 else []
            elif split:
                parts = [(p // 2, interest // 2), (p - p // 2, interest - interest // 2)]
            for idx, (paid_p, paid_i) in enumerate(parts):
                day = due if idx == 0 else min(end, due + timedelta(days=2))
                tables["payment_allocations"].append(dict(loan_id=loan, installment_no=seq, payment_ref=f"P{number}-{seq}-{idx+1}",
                                                          paid_at=_timestamp(day, rng), principal_paid_vnd=paid_p, interest_paid_vnd=paid_i))


def _geography(tables):
    province = {r["area_id"]: r["province_code"] for r in tables["areas"]}
    branch_province = {r["branch_id"]: province[r["area_id"]] for r in tables["branches"]}
    pools = {}
    for name, idfield in (("staff", "staff_id"), ("companies", "company_id"), ("merchants", "merchant_id")):
        local = defaultdict(list)
        for row in tables[name]:
            if name == "staff":
                if row["role_code"] != "RM":
                    continue
                group = branch_province[row["branch_id"]]
            else:
                group = province[row["area_id"]]
            local[group].append(row[idfield])
        pools[name] = local
    return province, pools


def generate_bank(cfg, rng):
    """Return source tables in memory. The caller owns snapshots, facts and splits."""
    end = date.fromisoformat(cfg["as_of"])
    start = _month(end, -12) + timedelta(days=1)
    tables = {name: [] for name in SOURCE_NAMES}
    _catalogues(cfg, rng, tables)
    province, pools = _geography(tables)
    rms = [r["staff_id"] for r in tables["staff"] if r["role_code"] == "RM"]
    branch_of = {r["staff_id"]: r["branch_id"] for r in tables["staff"]}
    all_cos = [r["company_id"] for r in tables["companies"]]
    all_merchants = [r["merchant_id"] for r in tables["merchants"]]
    mcc_by_id = {r["merchant_id"]: r["mcc"] for r in tables["merchants"]}
    all_devices = [r["device_id"] for r in tables["devices"]]
    for i in range(1, cfg["customers"] + 1):
        customer = f"CU{i:06}"
        area = rng.choice(tables["areas"])["area_id"]
        group = province[area]
        age = rng.randint(22, 70)
        birth = _month(end, -12*age) - timedelta(days=rng.randrange(350))
        occupation = rng.choices(list(OCCUPATIONS), weights=(36, 20, 17, 18, 4 if age >= 55 else 0, 5))[0]
        income = _round_money(OCCUPATIONS[occupation] * exp(rng.gauss(0, .37)) * (1 + min(age-22, 24)/100))
        score = round(max(300, min(850, rng.gauss(625 + min(75, income / 400_000), 68))))
        tables["customers"].append(dict(customer_id=customer, birth_on=birth.isoformat(), residence_area_id=area,
                                        occupation_code=occupation))
        tables["customer_pii"].append(_identifiers(customer, i, birth, rng))
        tables["customer_financials"].append(dict(customer_id=customer, as_of_on=end.isoformat(),
                                                  monthly_income_vnd=income if rng.random() < .90 else None,
                                                  credit_score=score if rng.random() < .83 else None))
        rm = rng.choice((pools["staff"][group] if rng.random() < .80 else []) or rms)
        change = start - timedelta(days=400)
        if len(rms) > 1 and rng.random() < .17:
            previous = rng.choice(rms)
            if previous != rm:
                transfer = _date_between(start, end - timedelta(days=8), rng)
                tables["rm_assignments"].append(dict(customer_id=customer, valid_from=change.isoformat(),
                                                      staff_id=previous, valid_to=transfer.isoformat()))
                change = transfer
        tables["rm_assignments"].append(dict(customer_id=customer, valid_from=change.isoformat(), staff_id=rm, valid_to=None))
        employer = None
        if occupation in ("OFFICE", "TECHNICIAN", "SERVICE") and rng.random() < .93:
            employer = rng.choice((pools["companies"][group] if rng.random() < .74 else []) or all_cos)
            tables["employment"].append(dict(customer_id=customer, company_id=employer,
                                              valid_from=(start - timedelta(days=rng.randint(1, 360))).isoformat(), valid_to=None))
        instruments = _accounts(tables, customer, branch_of[rm], income, start, end, rng)
        contacts = rng.randint(*DENSITY_CONTACTS[cfg["density"]])
        if rng.random() < .08:
            contacts = rng.randrange(3)
        frequent = []
        for _ in range(contacts):
            # A local merchant is more likely; low catalogue ranks are popular.
            pool = (pools["merchants"][group] if rng.random() < .67 else []) or all_merchants
            candidate = pool[int(len(pool) * rng.random()**1.7)]
            if income < 14_000_000 and mcc_by_id[candidate] == "5651" and rng.random() < .50:
                continue
            frequent.append(candidate)
        # Most devices serve one customer. Adjacent members of an occasional
        # simulated household share a primary device.
        primary = (i-1) % len(all_devices)
        if i % 10 == 0:
            primary = (primary-1) % len(all_devices)
        device_ids = [all_devices[primary]]
        if rng.random() < .32:
            device_ids.append(all_devices[(cfg["customers"] + i - 1) % len(all_devices)])
        _transactions(tables, instruments, employer, income, frequent, device_ids, start, end, rng)
        _loans(tables, customer, income, score, start, end, rng)
        opened = _date_between(max(change, start), end - timedelta(days=4), rng)
        closed = _date_between(opened + timedelta(days=1), end, rng) if i % 4 == 0 else None
        tables["cases"].append(dict(case_id=f"CASE{i:06}", staff_id=rm, customer_id=customer,
                                    purpose_code="ACCOUNT_REVIEW", opened_at=_timestamp(opened, rng),
                                    closed_at=_timestamp(closed, rng) if closed else None))
    return tables
