"""Derive the internal banking snapshot, graph, labels and text views at T."""

import json
import unicodedata
from hashlib import blake2b
from random import Random
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone


VN = timezone(timedelta(hours=7))
INCOME_CUTS = (10_000_000, 25_000_000, 50_000_000)
SCORE_CUTS = (580, 670, 740)
SENSITIVE_MCC = {"5912": "HEALTH", "8062": "HEALTH", "8661": "RELIGION"}


def _local_date(value):
    instant = datetime.fromisoformat(value)
    if instant.utcoffset() is None:
        raise ValueError("posted_at/paid_at must include timezone")
    return instant.astimezone(VN).date()


def _start_exclusive(day):
    # A twelve-calendar-month window ending on leap day starts after Feb 28.
    if day.month == 2 and day.day == 29:
        return date(day.year - 1, 2, 28)
    return day.replace(year=day.year - 1)


def derive_snapshots(tables, cfg):
    """Unrestructured, one-disbursement loans under the versioned demo policy."""
    as_of = cfg["as_of"]
    day = date.fromisoformat(as_of)
    schedules = defaultdict(list)
    for row in tables["installments"]:
        schedules[row["loan_id"]].append(row)
    paid = defaultdict(lambda: [0, 0])
    for row in tables["payment_allocations"]:
        if _local_date(row["paid_at"]) <= day:
            parts = paid[row["loan_id"], row["installment_no"]]
            parts[0] += row["principal_paid_vnd"]
            parts[1] += row["interest_paid_vnd"]
    result = []
    for loan in tables["loans"]:
        if loan["disbursed_on"] > as_of:
            continue
        outstanding = loan["principal_vnd"]
        earliest = day
        for installment in schedules[loan["loan_id"]]:
            principal, interest = paid[loan["loan_id"], installment["installment_no"]]
            outstanding -= principal
            if (principal < installment["principal_due_vnd"]
                    or interest < installment["interest_due_vnd"]):
                earliest = min(earliest, date.fromisoformat(installment["due_on"]))
        dpd = (day - earliest).days
        result.append(dict(loan_id=loan["loan_id"], as_of_on=as_of,
                           outstanding_vnd=outstanding, days_past_due=dpd,
                           debt_group=1 + bisect_right((10, 91, 181, 361), dpd)
                           if outstanding > 0 else None))
    return result


def _active(row, day, start, end):
    return row[start] <= day and (row[end] is None or day < row[end])


def _index(rows, field):
    return {row[field]: row for row in rows}


def _source_key(*parts):
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def merchant_group(merchant):
    """Classify a *purchase* from raw merchant name and MCC under bank-demo-v1."""
    if merchant["mcc"] in SENSITIVE_MCC:
        return SENSITIVE_MCC[merchant["mcc"]]
    name = unicodedata.normalize("NFC", merchant["merchant_name"]).casefold()
    if any(word in name for word in ("bệnh viện", "phòng khám", "nhà thuốc")):
        return "HEALTH"
    if any(word in name for word in ("giáo xứ", "nhà thờ", "tịnh xá", "chùa ")):
        return "RELIGION"
    return None


def derive_facts(tables, cfg):
    """Derive expected graph, full PK evidence, labels and raw-observation view.

    Source occurrences are accumulated first, then grouped into distinct triples.
    Input values and prose are assembled from source observations, including
    the same PURCHASE fields for ordinary merchants and protected categories.
    """
    as_of = cfg["as_of"]
    day = date.fromisoformat(as_of)
    lower = _start_exclusive(day)
    accounts = _index(tables["accounts"], "account_id")
    cards = _index(tables["cards"], "card_id")
    areas = _index(tables["areas"], "area_id")
    companies = _index(tables["companies"], "company_id")
    branches = _index(tables["branches"], "branch_id")
    staff = _index(tables["staff"], "staff_id")
    devices = _index(tables["devices"], "device_id")
    merchants = _index(tables["merchants"], "merchant_id")
    loans = _index(tables["loans"], "loan_id")
    nodes = {}
    grouped = {}

    def add(customer, predicate, node_type, key, event_kind, evidence, obs, source):
        node_id = f"{node_type}:{key}"
        if node_id not in nodes:
            nodes[node_id] = dict(object_node_id=node_id, node_type=node_type,
                                  source_key=str(key))
        triple = customer, predicate, node_id
        if triple not in grouped:
            grouped[triple] = (event_kind, {})
        grouped[triple][1][source] = (str(obs), evidence)

    for r in tables["customers"]:
        key = r["residence_area_id"]
        area_name = areas[key]["area_name"]
        add(r["customer_id"], "sống_tại", "area", key, "CUSTOMER_PROFILE",
            f"Khu vực cư trú tại ngày chốt là {area_name}.", area_name,
            ("customers", _source_key(r["customer_id"])))
    for r in accounts.values():
        if r["opened_on"] <= as_of:
            key = r["branch_id"]
            name = branches[key]["branch_name"]
            add(r["customer_id"], "mở_tài_khoản_tại", "branch", key, "ACCOUNT_OPENING",
                f"Hồ sơ mở tài khoản ghi chi nhánh {name}.", name,
                ("accounts", _source_key(r["account_id"])))
    for r in tables["rm_assignments"]:
        if _active(r, as_of, "valid_from", "valid_to"):
            key = r["staff_id"]
            branch = branches[staff[key]["branch_id"]]["branch_name"]
            add(r["customer_id"], "được_quản_lý_bởi", "staff", key, "ASSIGNMENT",
                f"Chuyên viên tại {branch} phụ trách hồ sơ.", branch,
                ("rm_assignments", _source_key(r["customer_id"], r["valid_from"])))
    for r in tables["employment"]:
        if _active(r, as_of, "valid_from", "valid_to"):
            key = r["company_id"]
            name = companies[key]["company_name"]
            add(r["customer_id"], "làm_việc_tại", "company", key, "EMPLOYMENT",
                f"Đơn vị sử dụng lao động trong hồ sơ là {name}.", name,
                ("employment", _source_key(r["customer_id"], key, r["valid_from"])))
    for r in tables["transactions"]:
        if r["status"] != "POSTED":
            continue
        posted = _local_date(r["posted_at"])
        if not lower < posted <= day:
            continue
        account = accounts[r["account_id"]]
        local_day = posted.isoformat()
        if not _active(account, local_day, "opened_on", "closed_on"):
            continue
        if r["card_id"] is not None:
            card = cards[r["card_id"]]
            if (card["account_id"] != account["account_id"]
                    or not _active(card, local_day, "issued_on", "closed_on")):
                continue
        subject = account["customer_id"]
        source = ("transactions", _source_key(r["txn_id"]))
        if r["kind"] == "SALARY":
            key = r["payer_company_id"]
            name = companies[key]["company_name"]
            add(subject, "nhận_lương_từ", "company", key, "SALARY",
                f"Đơn vị chuyển khoản lương là {name}.", name, source)
        elif r["kind"] == "PURCHASE":
            merchant = merchants[r["merchant_id"]]
            group = merchant_group(merchant)
            value = f"{merchant['merchant_name']} (MCC {merchant['mcc']})"
            add(subject,
                "giao_dịch_nhóm_nhạy_cảm" if group else "giao_dịch_tại",
                "merchant_group" if group else "merchant", group or r["merchant_id"],
                "PURCHASE", f"Bút toán thanh toán tại {value}.", value, source)
        else:
            raise ValueError(f"Unsupported transaction kind: {r['kind']}")
        if r["device_id"] is not None:
            key = r["device_id"]
            kind = devices[key]["device_kind"]
            add(subject, "dùng_thiết_bị", "device", key, "DEVICE_ACTIVITY",
                f"Giao dịch được ghi nhận qua thiết bị loại {kind}.", kind, source)

    by_customer = defaultdict(list)
    for r in tables["loan_snapshot"]:
        by_customer[loans[r["loan_id"]]["customer_id"]].append(r)
    for customer, snapshots in by_customer.items():
        active = [s for s in snapshots if s["outstanding_vnd"] > 0]
        status = "ACTIVE" if active else "CLOSED"
        for s in snapshots:
            amount = s["outstanding_vnd"]
            source = ("loan_snapshot", _source_key(s["loan_id"], s["as_of_on"]))
            add(customer, "trạng_thái_vay", "loan_status", status, "LOAN_BALANCE",
                f"Dư gốc của hợp đồng tại ngày chốt là {amount} đồng.", amount, source)
        if active:
            worst = max(s["debt_group"] for s in active)
            for s in active:
                dpd = s["days_past_due"]
                add(customer, "nhóm_nợ", "debt_group", f"G{worst}", "LOAN_ARREARS",
                    f"Hợp đồng có số ngày trễ hạn là {dpd}.", dpd,
                    ("loan_snapshot", _source_key(s["loan_id"], s["as_of_on"])))
    for r in tables["customer_financials"]:
        if r["as_of_on"] != as_of:
            continue
        source = ("customer_financials", _source_key(r["customer_id"], as_of))
        value = r["monthly_income_vnd"]
        if value is not None:
            band = f"I{1 + bisect_right(INCOME_CUTS, value)}"
            add(r["customer_id"], "bậc_thu_nhập", "income_band", band, "INCOME_REVIEW",
                f"Giá trị bình quân kê khai mỗi tháng là {value} đồng.", value, source)
        value = r["credit_score"]
        if value is not None:
            band = f"S{1 + bisect_right(SCORE_CUTS, value)}"
            add(r["customer_id"], "hạng_điểm", "score_band", band, "SCORE_REVIEW",
                f"Kết quả chấm điểm nội bộ là {value}.", value, source)

    facts, sources, gold, inputs = [], [], [], []
    split, family_split = _partitions(tables, cfg)
    families = _families_by_split(family_split)
    for (customer, predicate, node), (event_kind, evidence) in grouped.items():
        key = _source_key(customer, predicate, node).encode("utf-8")
        digest = blake2b(key, digest_size=16, person=b"bank-fact-v1").hexdigest()
        fact_id = "F" + digest
        facts.append(dict(fact_id=fact_id, customer_id=customer, predicate=predicate,
                          object_node_id=node))
        typ = nodes[node]["node_type"]
        sensitive = typ in {"income_band", "debt_group", "score_band", "merchant_group"} or (
            typ == "loan_status" and nodes[node]["source_key"] == "ACTIVE")
        gold.append(dict(fact_id=fact_id, is_sensitive=sensitive,
                         reason_code=typ.upper() if sensitive else "ORDINARY",
                         policy_version=cfg.get("policy_version", "bank-demo-v1")))
        observations = {}
        for (table, key), (value, prose) in sorted(evidence.items()):
            sources.append(dict(fact_id=fact_id, source_table=table, source_key=key))
            observations.setdefault(value, prose)
        part = split[customer]
        _, wrapper = _template(fact_id, families[part])
        raw = " | ".join(observations)
        if part == "train":
            sentence = " ".join(observations.values())
        else:
            variants = HELD_OUT_WORDING[event_kind]
            sentence = variants[0 if part == "validation" else 1].format(raw)
        inputs.append(dict(fact_id=fact_id, event_kind=event_kind, observed_value=raw,
                           evidence_text=unicodedata.normalize("NFC", wrapper.format(sentence))))
    for collection in (facts, gold, inputs):
        collection.sort(key=lambda row: row["fact_id"])
    return dict(object_nodes=[nodes[key] for key in sorted(nodes)], facts=facts,
                fact_sources=sources, facts_gold=gold, facts_input=inputs)


# Distinct note families. Paraphrases of each family stay in its own split.
FAMILIES = {
    "call_note": ("Trong cuộc gọi, chuyên viên ghi lại như sau. {}", "Ghi chú sau cuộc gọi. {}"),
    "case_history": ("Diễn biến hồ sơ tại lần rà soát này. {}", "Lịch sử xử lý có ghi. {}"),
    "statement": ("Trích lục từ bảng kê. {}", "Bảng kê kỳ này thể hiện nội dung sau. {}"),
    "ticket": ("Phiếu hỗ trợ\nNội dung đối chiếu: {}", "Yêu cầu hỗ trợ đã ghi nhận. {}"),
    "customer_reply": ("Khách hỏi thông tin hiện tại. Nhân viên phản hồi: {}", "Trao đổi với khách: {}"),
    "handover": ("Thông tin bàn giao cho người tiếp nhận. {}", "Nhận bàn giao với nội dung sau. {}"),
    "audit_note": ("Kết quả đối chiếu hồ sơ gốc. {}", "Kiểm tra tại ngày chốt cho thấy: {}"),
    "email": ("Kính gửi bộ phận phụ trách,\n{}", "Thông báo cho bộ phận xử lý. {}"),
    "checklist": ("Mục đã xác minh trong danh sách kiểm tra: {}", "Đã đánh dấu bước xác minh. {}"),
    "appointment": ("Trong buổi hẹn, nhân viên xác nhận nội dung sau. {}", "Ghi nhận khi gặp khách: {}"),
    "correction": ("Nội dung sau khi cập nhật hồ sơ. {}", "Giá trị đã đối chiếu khi điều chỉnh: {}"),
    "review": ("Bản tổng hợp cuối kỳ ghi nhận: {}", "Kết quả rà soát định kỳ. {}"),
}

# Separate phrasings for held-out families. Even a snippet without its note
# header uses different business language. A purchase keeps the merchant's
# raw name and MCC regardless of the sensitivity policy.
HELD_OUT_WORDING = {
    "CUSTOMER_PROFILE": ("Khách hiện ở khu vực {}.", "Địa chỉ liên hệ thuộc {}."),
    "ACCOUNT_OPENING": ("Đơn mở mới do {} tiếp nhận.", "Nơi đăng ký tài khoản ban đầu là {}."),
    "ASSIGNMENT": ("Danh mục chăm sóc do người bên {} theo dõi.", "Đầu mối xử lý thuộc {}."),
    "EMPLOYMENT": ("Người sử dụng lao động trên hợp đồng là {}.", "Nơi công tác đã xác nhận: {}."),
    "SALARY": ("Khoản tiền công được {} chuyển tới.", "Bên chi trả qua bảng lương ghi {}."),
    "PURCHASE": ("Trên biên nhận mua hàng ghi {}.", "Bên chấp nhận thanh toán trên sao kê là {}."),
    "DEVICE_ACTIVITY": ("Phiên thực hiện qua {}.", "Kênh truy cập ghi loại máy {}."),
    "LOAN_BALANCE": ("Số gốc còn phải hoàn lại theo hợp đồng, tính bằng đồng: {}.",
                     "Đối soát phần vốn chưa thu, theo đồng: {}."),
    "LOAN_ARREARS": ("So với hạn của kỳ còn thiếu, số ngày ghi nhận là {}.",
                     "Độ trễ tính từ kỳ sớm nhất còn thiếu, theo ngày: {}."),
    "INCOME_REVIEW": ("Nguồn tiền đều đặn mỗi tháng khoảng {} đồng.",
                      "Khoản khách kê khai nhận bình quân một tháng là {} đồng."),
    "SCORE_REVIEW": ("Thang đánh giá nội bộ ghi {} điểm.", "Kỳ xếp hạng gần nhất cho kết quả {} điểm."),
}


def _split(keys, rng):
    keys = sorted(keys)
    rng.shuffle(keys)
    holdout = max(1, round(0.15 * len(keys))) if len(keys) >= 3 else 0
    return {key: ("train" if i < len(keys) - 2 * holdout else
                  "validation" if i < len(keys) - holdout else "test")
            for i, key in enumerate(keys)}


def _partitions(tables, cfg):
    # Isolate partitioning from how many draws the business simulation took.
    seed = cfg["seed"]
    return (_split((r["customer_id"] for r in tables["customers"]), Random(f"clients:{seed}")),
            _split(FAMILIES, Random(f"families:{seed}")))


def _families_by_split(assignment):
    families = defaultdict(list)
    for family, part in sorted(assignment.items()):
        families[part].append(family)
    return families


def _template(fact_id, available):
    # Opaque fact IDs are already uniformly distributed. A family and its
    # paraphrase can be replayed by the validator without exposing its split.
    index = int(fact_id[-8:], 16)
    family = available[index % len(available)]
    templates = FAMILIES[family]
    return family, templates[(index // len(available)) % len(templates)]


def pii_values(tables):
    """Literal Unicode surfaces used for PII annotation, grouped by customer."""
    result = defaultdict(lambda: defaultdict(list))
    for r in tables["customer_pii"]:
        kinds = result[r["customer_id"]]
        for col, kind in (("full_name", "NAME"), ("phone_test", "PHONE"),
                          ("national_id_test", "NATIONAL_ID"), ("email_test", "EMAIL")):
            if r[col]:
                kinds[kind].append(unicodedata.normalize("NFC", r[col]))
    areas = _index(tables["areas"], "area_id")
    for r in tables["customers"]:
        name = areas[r["residence_area_id"]]["area_name"]
        result[r["customer_id"]]["ADDRESS"].append(unicodedata.normalize("NFC", name))
    accounts = _index(tables["accounts"], "account_id")
    for r in accounts.values():
        result[r["customer_id"]]["ACCOUNT"].append(r["account_no_test"])
    for r in tables["cards"]:
        owner = accounts[r["account_id"]]["customer_id"]
        result[owner]["CARD"].append(r["pan_test"])
    return result


def build_features(tables, cfg, rng):
    """Complete the pre-M1 handoff. Uses a seeded RNG supplied by the caller."""
    tables["loan_snapshot"] = derive_snapshots(tables, cfg)
    tables.update(derive_facts(tables, cfg))
    split, family_split = _partitions(tables, cfg)
    tables["split_customers"] = [dict(customer_id=k, split=v) for k, v in sorted(split.items())]
    tables["split_families"] = [dict(family_id=k, split=v) for k, v in sorted(family_split.items())]
    families = _families_by_split(family_split)
    surfaces = pii_values(tables)
    texts, links, spans, chunks, test = [], [], [], [], []
    labels = {"NAME": "Tên người liên hệ", "PHONE": "Điện thoại", "ACCOUNT": "Tài khoản",
              "NATIONAL_ID": "Giấy tờ định danh", "EMAIL": "Email",
              "CARD": "Thẻ", "ADDRESS": "Địa bàn cư trú"}
    for i, (fact, obs) in enumerate(zip(tables["facts"], tables["facts_input"]), 1):
        customer, fact_id = fact["customer_id"], fact["fact_id"]
        part = split[customer]
        family, _ = _template(fact_id, families[part])
        body = obs["evidence_text"]
        text_id = f"T{i:08d}"
        # Evidence text already contains the actual area surface on residence facts.
        if fact["predicate"] == "sống_tại":
            value = surfaces[customer]["ADDRESS"][0]
            start = body.index(value)
            spans.append(dict(text_id=text_id, start_cp=start, end_cp=start+len(value),
                              pii_type="ADDRESS"))
        chunks.append(dict(chunk_id=f"{text_id}-O", text_id=text_id, customer_id=customer,
                           field_code=fact["predicate"], content=body))
        # The PII sentence is a separate retrievable field even when both
        # sentences share one source note. Some notes contain no added PII.
        if rng.random() < 0.45:
            kind = rng.choice(sorted(surfaces[customer]))
            value = rng.choice(surfaces[customer][kind])
            label = labels[kind] + ": "
            start = len(body) + 1 + len(label)
            content = label + value + "."
            body += "\n" + content
            spans.append(dict(text_id=text_id, start_cp=start, end_cp=start+len(value),
                              pii_type=kind))
            chunks.append(dict(chunk_id=f"{text_id}-P", text_id=text_id, customer_id=customer,
                               field_code=kind, content=content))
        texts.append(dict(text_id=text_id, customer_id=customer, family_id=family, body=body))
        links.append(dict(fact_id=fact_id, text_id=text_id))
        if part == "test":
            test.append(obs.copy())
    tables.update(texts=texts, fact_texts=links, pii_spans_gold=spans, rag_chunks=chunks,
                  facts_test=test, fact_predictions=[])
