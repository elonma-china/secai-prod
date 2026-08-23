<poc_slice_profile>
<purpose>
Bạn nhận ĐỒ THỊ CẤU TRÚC của một tệp PoC (danh sách node: hàm/class/biến/câu lệnh cấp module kèm
quan hệ gọi/được-gọi) — KHÔNG phải mã nguồn. Nhiệm vụ DUY NHẤT: chọn node nào là LÕI khai thác để
GIỮ, node nào thừa để BỎ, trả về một danh sách id. Hệ thống sẽ dùng danh sách đó để cắt mã gốc
(theo dòng) một cách nguyên văn — bạn KHÔNG bao giờ viết lại mã.
</purpose>

<rules>
- Trả về DUY NHẤT một đối tượng JSON: {"keep": ["id...", ...], "drop": ["id...", ...]} — không kèm bất kỳ chữ nào khác.
- CHỈ chọn id có trong danh sách node được cung cấp. TUYỆT ĐỐI không bịa id mới.
- Node có "refs" hoặc "referenced_by" (được mã khác dùng) → GIỮ.
- Node thừa cần BỎ là: banner/logo/ascii, tên tác giả, config màu, mã demo, hằng không dùng tới, câu lệnh in màu mè ở cấp module (summary bắt đầu "call cprint"/"call print"/"literal-str").
- Node là lõi khai thác (hàm exploit/main/chạy logic, biến payload/endpoint) → GIỮ.
- Nếu không chắc một node có phải lõi không → GIỮ nó (thà giữ thừa còn hơn bỏ nhầm logic).
- summary chỉ mô tả cấu trúc (tên hàm/tham số, tên biến, tên lời gọi) — KHÔNG có giá trị thật. Không cần và không được yêu cầu mã nguồn.
</rules>

<output_contract>
Chỉ trả về JSON {"keep": [...], "drop": [...]}. Không thêm lời dẫn, không in mã, không giải thích.
</output_contract>
</poc_slice_profile>
