"""Luật phân loại fact theo chính sách bank-demo-v1."""
import re
import unicodedata


def predict_fact(event_kind: str, observed_value: str, evidence_text: str | None) -> bool:
    """Trả về True khi fact nhạy cảm theo chính sách ngân hàng mô phỏng."""
    if event_kind in {"INCOME_REVIEW", "SCORE_REVIEW", "LOAN_ARREARS"}:
        return True

    if event_kind == "LOAN_BALANCE":
        balances = [int(value) for value in observed_value.split("|")]
        return any(balance > 0 for balance in balances)

    if event_kind == "PURCHASE":
        description = unicodedata.normalize("NFC", f"{observed_value} {evidence_text or ''}").casefold()
        codes = re.findall(r"\bmcc\s*[:=]?\s*(\d{4})\b", description)
        if any(code in {"5912", "8062", "8661"} for code in codes):
            return True
        merchant_terms = ("nhà thuốc", "bệnh viện", "phòng khám", "tôn giáo",
                          "giáo xứ", "nhà thờ", "tịnh xá", "chùa")
        return any(term in description for term in merchant_terms)

    if event_kind in {"ACCOUNT_OPENING", "ASSIGNMENT", "CUSTOMER_PROFILE",
                      "DEVICE_ACTIVITY", "EMPLOYMENT", "SALARY"}:
        return False

    raise ValueError(f"Loại nghiệp vụ chưa được định nghĩa trong baseline: {event_kind}")
