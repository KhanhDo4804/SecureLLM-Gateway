import unicodedata
import unittest

from .pii import PiiDetector, mask_pii


class PiiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = PiiDetector()

    def check_spans(self, text, expected):
        spans = self.detector.detect(text)
        self.assertEqual([(s.pii_type, text[s.start_cp:s.end_cp]) for s in spans], expected)
        self.assertTrue(all(a.end_cp <= b.start_cp for a, b in zip(spans, spans[1:])))
        return spans

    def test_unicode_offsets(self):
        name = unicodedata.normalize("NFD", "Đặng Minh Hà")
        text = f"🙂 Anh {name} vừa gọi lại."
        spans = self.check_spans(text, [("NAME", name)])
        self.assertEqual(mask_pii(text, spans), "🙂 Anh [NAME] vừa gọi lại.")

    def test_address_and_phone_on_same_line(self):
        address = "12 Nguyễn Trãi, P. Bến Thành, TP. Hồ Chí Minh"
        text = f"Địa chỉ: {address}, SĐT: +84 912 345 678."
        self.check_spans(text, [("ADDRESS", address), ("PHONE", "+84 912 345 678")])

    def test_context_disambiguates_numbers(self):
        text = "CCCD: 012345678901. STK: 012345678901. Tài khoản: 0901234567."
        self.check_spans(text, [("NATIONAL_ID", "012345678901"), ("ACCOUNT", "012345678901"),
                                ("ACCOUNT", "0901234567")])

    def test_email_card_and_demo_id(self):
        text = "Email: ha.minh+test@example.org. PAN: 4111 1111 1111 1111. CCCD: TEST-CUST-123456."
        self.check_spans(text, [("EMAIL", "ha.minh+test@example.org"), ("CARD", "4111 1111 1111 1111"),
                                ("NATIONAL_ID", "TEST-CUST-123456")])

    def test_negative_cases(self):
        for text in ("", "MCC 8062.", "Mã sản phẩm: ABC-0901234567.",
                     "Số tiền 4111111111111111 đồng.", "Giá 0901234567 VND."):
            with self.subTest(text=text):
                self.check_spans(text, [])

    def test_ner_in_residence_sentence(self):
        text = "Khách hiện ở khu vực Phường mô phỏng 17."
        self.check_spans(text, [("ADDRESS", "Phường mô phỏng 17")])


if __name__ == "__main__":
    unittest.main()
