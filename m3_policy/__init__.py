"""M3: quyết định quyền trên một cặp khách–trường từ metadata nội bộ."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq


VERSION = "bank-access-demo-v1"
DATASET = Path("datasets/bank_4000_medium_s4242")
VN = ZoneInfo("Asia/Ho_Chi_Minh")
PURPOSE = {"RM": "ACCOUNT_REVIEW", "CSKH": "CUSTOMER_SERVICE", "INTERNAL_CONTROL": "CONTROL_REVIEW"}

# Giá trị mỗi ô là (gửi tới model ngoài, hiển thị cho nhân viên).
FULL = ("full", "full")
LOCAL = ("deny", "full")
PARTIAL = ("masked", "masked")
BASIC = ("mở_tài_khoản_tại", "được_quản_lý_bởi")
CONTACT = ("NAME", "PHONE", "EMAIL", "ADDRESS", "sống_tại")
BUSINESS = ("làm_việc_tại", "nhận_lương_từ", "giao_dịch_tại", "dùng_thiết_bị")
FINANCE = ("bậc_thu_nhập", "hạng_điểm", "nhóm_nợ", "trạng_thái_vay", "giao_dịch_nhóm_nhạy_cảm")
FIELDS = {
    "RM": {**dict.fromkeys(BASIC + BUSINESS, FULL), **dict.fromkeys(CONTACT, ("masked", "full")),
           **dict.fromkeys(FINANCE + ("ACCOUNT",), LOCAL)},
    "CSKH": {**dict.fromkeys(BASIC, FULL), **dict.fromkeys(CONTACT + ("ACCOUNT",), PARTIAL)},
    "INTERNAL_CONTROL": dict.fromkeys(BASIC + CONTACT + BUSINESS + FINANCE + ("ACCOUNT",), LOCAL),
}


@dataclass(frozen=True)
class Decision:
    can_read: bool
    model_level: str
    display_level: str
    reason_code: str
    policy_version: str = VERSION


def deny(reason):
    return Decision(False, "deny", "deny", reason)


@dataclass(frozen=True)
class ControlScope:
    """Phê duyệt nội bộ gắn với case_id của kiểm soát viên."""
    fields: frozenset[str]
    valid_from: datetime
    valid_until: datetime


def index_by(rows, key):
    index = {row[key]: row for row in rows}
    if len(index) != len(rows) or None in index:
        raise ValueError(f"Invalid or duplicate {key} in policy metadata.")
    return index


class Policy:
    def __init__(self, staff, assignments, cases, field_codes, control_scopes=None):
        self.staff = index_by(staff, "staff_id")
        self.cases = index_by(cases, "case_id")
        self.assignments = defaultdict(list)
        for row in assignments:
            self.assignments[(row["staff_id"], row["customer_id"])].append((row["valid_from"], row["valid_to"]))
        self.field_codes = frozenset(field_codes)
        self.control_scopes = dict(control_scopes or {})

    @classmethod
    def load(cls, dataset=DATASET):
        source = Path(dataset) / "source"
        staff = pq.read_table(source / "staff.parquet", columns=["staff_id", "role_code"])
        assignments = pq.read_table(source / "rm_assignments.parquet",
                                    columns=["customer_id", "staff_id", "valid_from", "valid_to"])
        cases = pq.read_table(source / "cases.parquet",
                              columns=["case_id", "customer_id", "staff_id", "purpose_code", "opened_at", "closed_at"])
        chunks = pq.read_table(Path(dataset) / "features/rag_chunks.parquet", columns=["field_code"])
        return cls(staff.to_pylist(), assignments.to_pylist(), cases.to_pylist(),
                   chunks["field_code"].unique().to_pylist())

    def authorize(self, staff_id, customer_id, case_id, purpose_code, field_code, requested_at):
        """staff_id do lớp xác thực cung cấp; requested_at là datetime có múi giờ."""
        if not isinstance(requested_at, datetime) or requested_at.utcoffset() is None:
            return deny("INVALID_REQUEST_TIME")
        employee = self.staff.get(staff_id)
        if employee is None:
            return deny("UNKNOWN_STAFF")
        role = employee["role_code"]
        if role not in PURPOSE:
            return deny("ROLE_NOT_PERMITTED")
        if field_code not in self.field_codes:
            return deny("UNKNOWN_FIELD")
        if purpose_code != PURPOSE[role]:
            return deny("PURPOSE_NOT_PERMITTED")
        case = self.cases.get(case_id)
        if case is None:
            return deny("UNKNOWN_CASE")
        if case["staff_id"] != staff_id or case["customer_id"] != customer_id:
            return deny("CASE_SUBJECT_MISMATCH")
        if case["purpose_code"] != purpose_code:
            return deny("CASE_PURPOSE_MISMATCH")
        if requested_at < case["opened_at"]:
            return deny("CASE_NOT_OPEN_YET")
        if case["closed_at"] is not None and requested_at >= case["closed_at"]:
            return deny("CASE_CLOSED")

        if role == "RM":
            day = requested_at.astimezone(VN).date()
            periods = self.assignments.get((staff_id, customer_id), ())
            if not any(start <= day and (end is None or day < end) for start, end in periods):
                return deny("NO_ACTIVE_ASSIGNMENT")
        if role == "INTERNAL_CONTROL":
            scope = self.control_scopes.get(case_id)
            if scope is None:
                return deny("CONTROL_SCOPE_MISSING")
            if not scope.valid_from <= requested_at < scope.valid_until:
                return deny("CONTROL_SCOPE_INACTIVE")
            if field_code not in scope.fields:
                return deny("OUTSIDE_CONTROL_SCOPE")

        levels = FIELDS[role].get(field_code)
        if levels is None:
            return deny("FIELD_NOT_PERMITTED")
        return Decision(True, levels[0], levels[1], "ALLOW")
