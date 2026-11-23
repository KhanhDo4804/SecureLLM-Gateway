# M3 — Kiểm quyền theo khách và trường

M4 gọi `Policy.load()` một lần rồi dùng `policy.authorize(staff_id, customer_id, case_id, purpose_code, field_code, requested_at)` cho **từng cặp khách–trường**. Lấy `staff_id` từ lớp xác thực. Metadata vai trò, phân công và hồ sơ đến từ ba bảng Parquet nội bộ. M3 chỉ đọc cột `field_code` của `rag_chunks` để biết danh mục trường. Quyết định đọc cho phép M4 truy xuất trong đúng phạm vi khách và trường đã hỏi.

```python
from datetime import datetime
from m3_policy import Policy

policy = Policy.load()
decision = policy.authorize("RM0065", "CU000004", "CASE000004", "ACCOUNT_REVIEW",
                            "ACCOUNT", datetime.fromisoformat("2026-06-24T15:56:59+07:00"))
# can_read=True, model_level="deny", display_level="full", reason_code="ALLOW"
```

## Chính sách bank-access-demo-v1

Bảng dưới là **giả định cấp trường của demo**. Mỗi ô ghi mức **gửi model ngoài / hiển thị cho nhân viên**. Dấu × là từ chối đọc với cả hai mức bằng `deny`. Mức `masked` yêu cầu M4 che giá trị trước khi dùng tại đích tương ứng. Trường `sống_tại` dùng cùng mức bảo vệ với địa chỉ vì nội dung có thể nêu nơi cư trú.

| Field code | RM | CSKH | Kiểm soát nội bộ |
| --- | --- | --- | --- |
| `mở_tài_khoản_tại`, `được_quản_lý_bởi` | full / full | full / full | deny / full |
| `NAME`, `PHONE`, `EMAIL`, `ADDRESS`, `sống_tại` | masked / full | masked / masked | deny / full |
| `làm_việc_tại`, `nhận_lương_từ`, `giao_dịch_tại`, `dùng_thiết_bị` | full / full | × | deny / full |
| `bậc_thu_nhập`, `hạng_điểm`, `nhóm_nợ`, `trạng_thái_vay`, `giao_dịch_nhóm_nhạy_cảm` | deny / full | × | deny / full |
| `ACCOUNT` | deny / full | masked / masked | deny / full |
| `NATIONAL_ID`, `CARD` | × | × | × |

Các mức trên chỉ áp dụng sau khi kiểm tra điều kiện nghiệp vụ:

- **RM** cần hồ sơ `ACCOUNT_REVIEW` đúng nhân viên, khách và mục đích, cùng phân công đang hiệu lực. Khoảng ngày phân công là `[valid_from, valid_to)` theo ngày Việt Nam. `valid_to` trống nghĩa là phân công chưa có ngày kết thúc.
- **CSKH** cần hồ sơ được giao với mục đích `CUSTOMER_SERVICE`. **Kiểm soát nội bộ** cần vụ việc `CONTROL_REVIEW` và phê duyệt nội bộ gắn với hồ sơ, ghi rõ các trường và khoảng thời gian được đọc. Dataset hiện chỉ có hồ sơ RM và thiếu bảng phê duyệt này. `Policy.load()` không cấp quyền kiểm soát khi thiếu bằng chứng. Fixture E5 cấp `ControlScope` cho một vụ việc giả lập.
- Cả ba vai trò đều dùng khoảng thời gian hồ sơ `[opened_at, closed_at)`. Hồ sơ chưa có `closed_at` được xem là đang mở kể từ `opened_at`. `requested_at` phải có múi giờ. Phân tích viên, nhà nghiên cứu ngoài, quản trị khóa và vai trò lạ bị từ chối tại API hồ sơ khách.

## Chạy E5 từ thư mục gốc

```powershell
.\.venv\Scripts\python.exe -X utf8 -m m3_policy
```

Kết quả đã chạy nằm trong `experiments/e5/m3_policy/`. Khi chạy lại, thêm `--out experiments/e5/m3_policy_run_002` để lưu kết quả mới. `metrics.json` chứa số liệu theo nguồn và tổng hợp. `decisions.jsonl` chỉ chứa mã ca kiểm thử, quyết định và mã lý do. Đáp án viết tay nằm trong hàm `examples()` của `__main__.py`.

E5 gồm 7 yêu cầu dùng metadata của dataset và 34 yêu cầu dùng fixture. Các yêu cầu dataset có cả tình huống nhập mã hồ sơ sai. Độ trễ p50/p95 tính theo thứ hạng gần nhất trên 100 lượt lặp toàn bộ 41 ca, sau một lượt chạy làm nóng. Đồng hồ chỉ đo lời gọi `authorize()` khi metadata đã nằm trong bộ nhớ.
