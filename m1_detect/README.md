# Nhận diện PII trong văn bản ngân hàng

Chạy các lệnh từ thư mục gốc của dự án bằng PowerShell. Môi trường `.venv` và mô hình đã huấn luyện nằm trong workspace hiện tại. Để thử một câu, chạy

```powershell
.\.venv\Scripts\python.exe -X utf8 -m m1_detect.pii --text "Tên người liên hệ: Nguyễn Ngọc Hà. Điện thoại: 0901.234.567."
```

Kết quả gồm loại PII, vị trí bắt đầu và vị trí kết thúc. Dòng cuối cho xem văn bản sau khi che.

```text
NAME 19 33
PHONE 47 59
Tên người liên hệ: [NAME]. Điện thoại: [PHONE].
```

## Cách nhận diện

Trong `pii.py`, `PiiDetector.detect(body)` chỉ nhận chuỗi văn bản. Dùng `body[span.start_cp:span.end_cp]` để lấy đúng đoạn trên bản gốc. Hai lần xuất hiện của cùng một giá trị tạo hai span riêng.

| Loại PII | Cách tìm |
| --- | --- |
| CCCD, giấy tờ định danh và số tài khoản | Đọc cụm đứng trước dãy số, như “CCCD” hoặc “STK”. Giấy tờ mô phỏng dạng `TEST-CUST-…` cũng được hỗ trợ khi có nhãn. |
| Điện thoại và email | Nhận diện theo định dạng. Luật điện thoại hỗ trợ số di động Việt Nam có đầu 0 hoặc +84, viết liền hoặc có dấu phân cách. |
| Thẻ | Tìm số sau nhãn thẻ. Với dãy số thiếu nhãn, kiểm tra độ dài và chữ số kiểm tra Luhn. |
| Họ tên và địa chỉ | Mô hình NER dạng CRF học cách đánh dấu từ bắt đầu và từ tiếp theo của mỗi thực thể. Luật bổ sung xử lý tên sau cách xưng hô và địa chỉ ghi thành trường rõ ràng. |

Khi nhiều cách cùng nhận diện một đoạn, bộ nhận diện ưu tiên luật cho chuỗi định danh, rồi đến luật ngữ cảnh và mô hình NER. Đầu ra là các span đã sắp xếp, không chồng lấn. `mask_pii(body, spans)` thay từng span bằng nhãn như `[NAME]`.

## Dữ liệu và cách chấm

`pii_eval.py` chọn văn bản từ `features/texts.parquet` theo khách và kiểm tra họ mẫu đúng tập. Ở train, mô hình học từ nhãn `NAME` và `ADDRESS` trong `evaluation/pii_spans_gold.parquet`. Các chuỗi có đặc trưng và nhãn giống hệt nhau được gộp lại khi học. Ở validation và test, chương trình dự đoán xong mới mở nhãn tương ứng để chấm.

Một dự đoán đúng phải khớp cả `text_id`, `pii_type`, `start_cp` và `end_cp`. Văn bản không chứa PII vẫn được đưa vào chấm. Mỗi thư mục kết quả có dự đoán theo span trong `pii_predictions.parquet`, số liệu tổng và theo loại trong `metrics.json`, và các span thừa hoặc thiếu trong `errors.parquet`. Hai tệp Parquet chỉ lưu mã và vị trí để tra lại văn bản.

Trên `bank_4000_medium_s4242`, 51.098 văn bản train cho 4.270 chuỗi đặc trưng và nhãn khác nhau. Validation tìm đúng 5.565 span trên 10.824 văn bản. Test tìm đúng 5.520 span trên 10.986 văn bản, trong đó 5.719 văn bản không có PII. Cả hai tập đạt F1 bằng 1 ở cả bảy loại. Dữ liệu sinh dùng nhiều cụm và định dạng lặp lại. Khi thử độc lập câu “Đã gặp Nguyễn Minh Khôi tại quầy.”, bộ nhận diện bỏ sót họ tên.

## Cài đặt và chạy lại

Để tạo môi trường trên máy mới, chạy

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r m1_detect/requirements.txt
```

Để tái lập ở thư mục kết quả riêng, chạy

```powershell
$piiModel = "experiments/e1/pii_rerun/name_address.crfsuite"
.\.venv\Scripts\python.exe -m m1_detect.pii_eval train --model $piiModel
.\.venv\Scripts\python.exe -m m1_detect.pii_eval validation --model $piiModel
.\.venv\Scripts\python.exe -m m1_detect.pii_eval test --model $piiModel
```

Để kiểm tra các trường hợp Unicode, MCC, mã sản phẩm, số tiền và dãy số dễ nhầm bằng mô hình đã bàn giao, chạy

```powershell
.\.venv\Scripts\python.exe -m unittest -v m1_detect.test_pii
```
