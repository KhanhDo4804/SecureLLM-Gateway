# M2 — bản F

Từ thư mục gốc trên Windows, dùng môi trường Python của dự án. Để cài các thư viện M2 trên máy mới, chạy

```powershell
.\.venv\Scripts\python.exe -m pip install -r m2_release/requirements.txt
```

Bản đầu tiên ở `experiments/m2/run_001/`. Để tạo một bản mới với mã ngẫu nhiên mới, chạy

```powershell
.\.venv\Scripts\python.exe -m m2_release --out experiments/m2/run_002
```

Lệnh mặc định dùng bộ `datasets/bank_4000_medium_s4242` và dự đoán `experiments/e1/rule_baseline/fact_predictions.parquet`. M2 lấy các fact của khách test rồi ghép đúng một dự đoán trên mỗi `fact_id`. Khi thư mục đích đã tồn tại, lệnh dừng. Kết quả chỉ xuất hiện ở đường dẫn đích sau khi các kiểm tra thành công.

| Trong thư mục của một lần chạy | Nội dung |
| --- | --- |
| `release/F/identified.tsv` | Fact có `pred_sensitive=False`, gắn với mã `c_` của khách. |
| `release/F/masked.tsv` | Fact có `pred_sensitive=True`, gắn với mã `m_`. Cùng một node đối tượng dùng chung mã `o_` ở cả hai tệp. |
| `vault/mappings.aesgcm` | Bảng mã khách và bảng mã đối tượng nội bộ, mã hóa bằng AES-256-GCM với nonce 12 byte. |
| `vault/key.dpapi` | Khóa AES riêng của lần chạy, được Windows DPAPI bảo vệ theo tài khoản hiện hành. |
| `report.json` | Số dòng và số mã khách xuất hiện ở mỗi phía, triple mẫu, SHA-256 của hai TSV, số fact đã đối soát và kết quả kiểm tra khóa. |

Chỉ thư mục `release/F/` chứa dữ liệu dành cho bên nhận. Trong demo, tiến trình chạy dưới cùng tài khoản Windows và có quyền đọc vault có thể khôi phục ánh xạ. Bộ chấm nội bộ có thể gọi `m2_release.vault.load_mappings(path_to_vault)` trong môi trường đó.

M2 đọc lại mỗi triple trên đĩa, dùng ánh xạ đã giải mã để khôi phục bộ ba nguồn, tìm `fact_id` và kiểm tra phía xuất theo dự đoán. Bộ kiểm tra yêu cầu từng fact xuất hiện đúng một lần. Để chạy các ca thiếu, thừa, trùng dự đoán, sai lần chạy M1, thay dòng, sai phía và ghi đè, dùng

```powershell
.\.venv\Scripts\python.exe -m unittest -v m2_release.test_release
```
