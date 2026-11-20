# Sinh và kiểm định dữ liệu ngân hàng tổng hợp

Module này thực hiện **giai đoạn 1** theo mục 5–6 và Phụ lục C của kịch bản SecureLLM-Gateway v1.3.1. Đơn vị bàn giao là một thư mục ứng với một ngày chốt, cấu hình và seed. Mỗi bảng là một tệp Parquet có schema, kiểu ngày, timestamp có múi giờ và số tiền nguyên VND. Bảng mã hai khóa, MOVE và release thuộc giai đoạn M2 tiếp theo.

## Chạy từ thư mục gốc dự án

```powershell
python -m pip install -r data_gen/requirements.txt
python -m data_gen generate --out datasets/my_run --customers 4000 --as-of 2026-10-31 --density medium --seed 4242
python -m data_gen validate datasets/my_run
python -m unittest data_gen.test_validation -v
```

Lệnh sinh kiểm tra lại toàn bộ tệp từ đĩa trước khi bàn giao và yêu cầu một đường dẫn đầu ra mới. Lệnh kiểm định độc lập in báo cáo JSON và trả mã thoát 1 khi phát hiện lỗi. Để chạy pilot ở cả ba mức hoạt động, mỗi mức năm seed liên tiếp, dùng:

```powershell
python -m data_gen.pilot --out datasets/my_pilot_report.json --customers 120 --first-seed 4242
```

Pilot dùng các thư mục tạm cho từng lần chạy và lưu báo cáo của cả 15 lần. Với ít khách, các danh mục cũng thu nhỏ. So sánh độ kết nối giữa các mật độ ở **cùng quy mô khách**.

## Dữ liệu bàn giao

| Vị trí | Nội dung |
|---|---|
| `source/` | Danh mục giả lập, khách và PII, RM và lịch sử phụ trách, việc làm, tài khoản, thẻ, lương và mua hàng, tài chính, khoản vay, lịch gốc/lãi, phân bổ thanh toán, snapshot và hồ sơ nghiệp vụ. |
| `features/` | Node, fact đã gộp, văn bản, liên kết fact–văn bản, đoạn truy xuất theo khách/trường, hai view M1 `facts_input` và `facts_test`. |
| `evaluation/` | Nhãn nhạy cảm theo fact, bằng chứng truy nguồn, nhãn PII theo vị trí Unicode, cách chia khách/họ mẫu. Bảng `fact_predictions` có schema và 0 dòng để M1 điền sau. |
| `manifest.json` | Cấu hình, phiên bản, seed, ngày chốt, SHA-256 mã nguồn, SHA-256 và số dòng từng tệp Parquet. |
| `validation_report.json`, `data_card.md` | Kết quả kiểm định lúc bàn giao, phân phối thực đo và phạm vi sử dụng. |

Ví dụ xem 5 dòng M1 thực nhận mà không cần đọc toàn bộ dữ liệu:

```powershell
python -c "import pyarrow.parquet as pq; p=pq.ParquetFile('datasets/bank_4000_medium_s4242/features/facts_input.parquet'); print(next(p.iter_batches(batch_size=5)).to_pylist())"
```

Trong bài toán phân loại, dùng `split_customers` và `facts.customer_id` nội bộ để chọn hàng train/validation, rồi cấp **chỉ các cột đầu vào**. `facts_input` chứa cả ba tập. `facts_test` chỉ chứa hàng của khách test. `fact_id` là mã ghép kết quả, cần loại khỏi đặc trưng huấn luyện. Nhiệm vụ nhận diện PII đọc `texts.body`, bộ chấm dùng span trong `evaluation/`. Các thư mục biểu thị ranh giới dữ liệu logic, người triển khai chịu trách nhiệm cấp quyền đọc phù hợp khi đưa vào dịch vụ.

## Quy ước nghiệp vụ của bản demo

- Cửa sổ giao dịch là (T trừ 12 tháng, T], theo ngày Việt Nam. Sự kiện lương và mua hàng phải được hạch toán khi tài khoản và thẻ liên quan còn hiệu lực mới tạo fact. Nhiều giao dịch cùng khách và cùng đối tượng được gộp, vẫn giữ đầy đủ khóa nguồn trong `fact_sources`.
- Dư nợ tại T bằng tiền giải ngân trừ gốc đã trả đến hết T. Kỳ thiếu gốc hoặc lãi quyết định số ngày quá hạn. Mô phỏng khoản vay chưa tái cơ cấu dùng các ranh giới 10, 91, 181, 361 ngày cho nhóm 2–5. Khách có nhiều khoản đang nợ lấy nhóm cao nhất. Chính sách này chỉ phục vụ thí nghiệm.
- Thu nhập chia theo mốc 10, 25, 50 triệu VND/tháng. Điểm nội bộ 300–850 chia theo 580, 670, 740. Fact nhóm nợ, đang còn khoản vay, bậc thu nhập, hạng điểm và giao dịch y tế/tôn giáo thuộc nhóm nhạy cảm. Merchant được xét theo MCC và từ khóa tên theo chính sách demo `bank-demo-v1`.
- Seed điều khiển quan hệ giữa nghề, thu nhập, địa bàn, RM, sản phẩm và hành vi giao dịch. Ba mật độ điều chỉnh số lần tiếp xúc merchant, trước khi tổng hợp thành fact. Địa danh và doanh nghiệp là danh mục mô phỏng. Tên doanh nghiệp có thể trùng, ID vẫn phân biệt.
- Mẫu âm nằm trong các kiểm thử độc lập. Validator kiểm schema, kiểu, PK/FK, lịch hiệu lực, giao dịch và thẻ, đối soát khoản vay, nhãn và bằng chứng theo fact, cách chia tập, chủ sở hữu đoạn RAG và span PII. Thống kê mô tả gồm phân vị bậc theo loại anchor, thu nhập theo nghề, mức độ dùng danh mục và số khách ngoài khoảng 15–40 fact thường.
- `PASS` tại đây xác nhận các điều kiện bàn giao giai đoạn 1. M5 và Release Gate đánh giá tỷ lệ nối đúng và bản phát hành sau khi M2 tạo hai khóa. Tính đại diện cho ngân hàng thực cần đánh giá riêng.
