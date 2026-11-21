"""Nhận diện PII từ một chuỗi văn bản, trả vị trí trên chính chuỗi đầu vào."""
import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

import pycrfsuite


DEFAULT_MODEL = Path("experiments/e1/pii_baseline/name_address.crfsuite")
PII_TYPES = ("NAME", "ADDRESS", "NATIONAL_ID", "PHONE", "EMAIL", "ACCOUNT", "CARD")


@dataclass(frozen=True, order=True)
class Span:
    start_cp: int
    end_cp: int
    pii_type: str


# Ưu tiên nhãn đứng trước dãy số. Cùng 12 chữ số có thể là CCCD hoặc tài khoản.
NUMBER_FIELDS = (
    ("NATIONAL_ID", r"(?:giấy tờ định danh|căn cước(?: công dân)?|CCCD|CMND|số định danh cá nhân)",
     r"(?:TEST-CUST-[0-9]{6}|[0-9](?:[ .-]?[0-9]){11}|[0-9](?:[ .-]?[0-9]){8})"),
    ("ACCOUNT", r"(?:số tài khoản|tài khoản|STK)", r"[0-9](?:[ .-]?[0-9]){5,18}"),
    ("CARD", r"(?:(?:số )?thẻ(?: tín dụng| ghi nợ| ngân hàng)?|PAN)", r"[0-9](?:[ -]?[0-9]){12,18}"),
)
FIELD_PATTERNS = [
    (kind, re.compile(r"\b" + label + r"[ \t]*(?:là\b[ \t]*)?[:#]?[ \t]*(?P<value>"
                      + value + r")(?!\w|[.-][0-9])", re.IGNORECASE))
    for kind, label, value in NUMBER_FIELDS
]
EMAIL = re.compile(r"(?<![\w.+%-])[\w%+-]+(?:\.[\w%+-]+)*@(?:[\w-]+\.)+[^\W\d_]{2,}\b")
PHONE = re.compile(r"(?<![\w+])(?:0|\+84[ .-]?)[35789](?:[ .-]?[0-9]){8}(?!\w|[.-][0-9])")
CARD = re.compile(r"(?<![\w+])[0-9](?:[ -]?[0-9]){12,18}(?!\w|[.-][0-9])")
NON_PII_LABEL = re.compile(r"(?:mã (?:sản phẩm|giao dịch|đơn hàng)|SKU|MCC|số tiền)"
                           r"[ \t]*[:#]?[ \t]*(?:[A-Z0-9]+[-_/])*$", re.I)
MONEY_UNIT = re.compile(r"\s*(?:đồng|VND|VNĐ|₫)(?!\w)", re.I)


def passes_luhn(value):
    total = 0
    for i, digit in enumerate(reversed([int(c) for c in value if c.isdigit()])):
        if i % 2:
            digit *= 2
        total += digit - 9 if digit > 9 else digit
    return total % 10 == 0


def structured_spans(body):
    for kind, pattern in FIELD_PATTERNS:
        for match in pattern.finditer(body):
            yield Span(*match.span("value"), kind)
    for match in EMAIL.finditer(body):
        yield Span(*match.span(), "EMAIL")
    for kind, pattern in (("PHONE", PHONE), ("CARD", CARD)):
        for match in pattern.finditer(body):
            if NON_PII_LABEL.search(body[:match.start()]) or MONEY_UNIT.match(body, match.end()):
                continue
            if kind == "PHONE" or passes_luhn(match.group()):
                yield Span(*match.span(), kind)


# Tách từ trên bản gốc để bảo toàn offset, kể cả khi dấu tiếng Việt ở dạng tổ hợp.
TOKEN = re.compile(r"[^\W_]+(?:[\u0300-\u036f]+[^\W_]*)*|[^\s]")
NAME_CUE = re.compile(r"\b(?:(?:họ (?:và )?tên|tên người liên hệ)[ \t]*:[ \t]*|(?:ông|bà|anh|chị)[ \t]+)", re.I)
ADDRESS_CUE = re.compile(r"\b(?:địa chỉ(?: liên hệ| thường trú| tạm trú)?|địa bàn cư trú|nơi cư trú)[ \t]*:[ \t]*", re.I)
ADDRESS_END = re.compile(r"[;\n\r!?]|\.|,?[ \t]*(?:điện thoại|SĐT|email|CCCD|STK|tài khoản|thẻ|họ (?:và )?tên|tên người liên hệ)[ \t]*:", re.I)
ABBREVIATION = re.compile(r"\b(?:TP|P|Q|TT|TX|H)$", re.I)


def contextual_spans(body):
    for cue in NAME_CUE.finditer(body):
        words = []
        previous = cue.end()
        for token in TOKEN.finditer(body, cue.end()):
            gap = body[previous:token.start()]
            word = unicodedata.normalize("NFC", token.group())
            if gap.strip(" \t") or not (word.isalpha() and word[0].isupper()):
                break
            words.append(token)
            previous = token.end()
        if 2 <= len(words) <= 6:
            yield Span(words[0].start(), words[-1].end(), "NAME")
    for cue in ADDRESS_CUE.finditer(body):
        start, end = cue.end(), len(body)
        for stop in ADDRESS_END.finditer(body, start):
            if stop.group() == "." and ABBREVIATION.search(body[start:stop.start()]):
                continue
            end = stop.start()
            break
        value = body[start:end].rstrip(" \t,")
        if value and any(c.isalnum() for c in value):
            yield Span(start, start + len(value), "ADDRESS")


CONTEXT_WORDS = set(
    "họ tên và người liên hệ khách hàng anh chị ông bà địa chỉ bàn nơi đăng ký thường tạm "
    "cư trú sống tại thuộc khu vực phường xã tỉnh thành phố thị trấn thôn ấp quận huyện "
    "đường ngõ hẻm số nhà chung chi nhánh công ty viện cửa mô phỏng".split()
)


def token_symbol(value):
    word = unicodedata.normalize("NFC", value)
    if word.lower() in CONTEXT_WORDS:
        return word.lower()
    if word.isdecimal():
        return "DIGIT"
    if word.isupper():
        return "UPPER"
    if word.istitle():
        return "TITLE"
    if word.isalpha():
        return "WORD"
    return word if len(word) == 1 and not word.isalnum() else "MIXED"


def token_features(symbols):
    padded = ("<BOS>", "<BOS>", *symbols, "<EOS>", "<EOS>")
    return [[f"{offset}={padded[i + offset + 2]}" for offset in (-2, -1, 0, 1, 2)]
            for i in range(len(symbols))]


class PiiDetector:
    def __init__(self, model_path=DEFAULT_MODEL):
        self.tagger = pycrfsuite.Tagger()
        self.tagger.open(str(model_path))

    def _ner_spans(self, body):
        tokens = list(TOKEN.finditer(body))
        if not tokens:
            return
        symbols = [token_symbol(t.group()) for t in tokens]
        active = None
        for token, tag in zip(tokens, self.tagger.tag(token_features(symbols))):
            if active and tag == "I-" + active.pii_type:
                active = Span(active.start_cp, token.end(), active.pii_type)
                continue
            if active:
                yield active
            active = None if tag == "O" else Span(token.start(), token.end(), tag[2:])
        if active:
            yield active

    def detect(self, body):
        """Trả các span theo thứ tự xuất hiện, không giao nhau. end_cp là cận ngoài."""
        spans = []
        for candidate in (*structured_spans(body), *contextual_spans(body), *self._ner_spans(body)):
            if all(candidate.end_cp <= s.start_cp or s.end_cp <= candidate.start_cp for s in spans):
                spans.append(candidate)
        return sorted(spans)


def mask_pii(body, spans):
    parts = []
    end = 0
    for span in spans:
        parts.extend((body[end:span.start_cp], f"[{span.pii_type}]"))
        end = span.end_cp
    return "".join(parts) + body[end:]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args()
    detected = PiiDetector(args.model).detect(args.text)
    for span in detected:
        print(span.pii_type, span.start_cp, span.end_cp)
    print(mask_pii(args.text, detected))
