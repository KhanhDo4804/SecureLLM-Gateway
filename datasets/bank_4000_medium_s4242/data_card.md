# Thẻ dữ liệu ngân hàng tổng hợp

Ngày chốt 2026-10-31 theo giờ Việt Nam. Cấu hình medium, seed 4242.
Dùng cho thí nghiệm phân loại fact, nhận diện PII và kiểm thử quyền truy cập trên hồ sơ giả lập.
Danh mục địa lý, cơ cấu nghề, thu nhập, giao dịch và tham số phân phối là giả định mô phỏng.
Định dạng của các số thử nghiệm không bảo đảm chúng chưa từng được cấp. Không dùng để liên hệ hay thanh toán.
Phạm vi tín dụng gồm khoản vay giải ngân một lần, chưa tái cơ cấu. Phân nhóm là chính sách demo.
Cửa sổ giao dịch là (T trừ 12 tháng, T]. Snapshot và fact chỉ dùng dữ kiện có hiệu lực tương ứng.
Chỉ cấp facts_input/facts_test và phần văn bản thích hợp cho M1. Evaluation chứa nhãn, chứng cứ và cách chia tập.
fact_predictions là bảng rỗng ở thời điểm bàn giao cho M1. Vault và release được tạo ở M2.
Xem manifest.json cho hash và số dòng từng bảng. validation_report.json chứa lỗi và thống kê thực đo.
Khoảng 15–40 fact thường/khách là mục tiêu mô tả. Các khoảng dung sai thống kê đại diện cho ngân hàng thực chưa được hiệu chỉnh.
