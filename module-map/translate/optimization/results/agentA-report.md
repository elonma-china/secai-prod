# Agent A - OCR roi ky tu dac biet (`&` ...) - ket qua

Ban cuoi: module-map/translate/optimization/serving/fast_ocr_amp.py (chay thu tren cong 18084, vLLM 18771 dung chung; 18083 khong dung den).

## Nguyen nhan (hai co che)
1. Roi/doi that (vi, ko, ...): dai da phong to van chua ca doan van dai; model 2B lam roi `&` (ko: `...$%^*()_+`) hoac doi `0`->`O` (vi: `w0rd`->`wOrd`). Doc rieng dong do (cat dong, phong x2) cho ket qua dung 100% trong thu nghiem.
2. Bidi (ar): khong roi - OCR doc thu tu HIEN THI: `+_()*&^P@ss!w0rd#2026$%` (duoi `^&*()_+` troi len dau, ngoac bi lat). Doc lai cung ra thu tu hien thi, cong them nhieu doc nhieu (`__`, `-`, mat `&`) tuy ti le.

## Thay doi
Sau luot doc moi dai, `refine_special`: (a) tim token nghi ngo (>=8 ky tu, >=4 ky tu trong `!@#$%^&*()_+~|\<>{}[]`); khong co -> khong ton them lenh nao; (b) dinh vi dong tren anh bang chieu ngang + uoc luong vi tri theo do rong ink/chieu rong ky tu, cua so +-2 dong (mo rong 6, 14 neu khop < 0.75); doc tung dong x2 (prompt chep tung ky tu), chon dong khop token nhat; (c) bo phieu giua token goc, doc x2 va (chi khi lech) doc x3 (prompt khac); hoa -> nhieu ky tu dac biet hon -> giu goc; ky tu CHEN them khong phai chu-so/dac biet bi loai (`-` bia); (d) dong RTL: noi manh dac biet bi tach, bo 3 phieu, roi doi thu tu hien thi -> logic (dao phan dau toan ky tu trung tinh, lat ngoac). Loi o buoc nay -> giu ket qua goc. Cong tat: `OCR_REREAD=0`. Khong hardcode chuoi.

## So do (cung vLLM, cung may)
| Phep do | Truoc | Sau |
|---|---|---|
| ocrcheck 20 anh dai, du 7/7 | 16/20 (lap lai 2 lan, xac dinh) | 20/20 |
| 20 anh ngan (png+jpg x 10 ngon ngu, ocr_special_char_check) | 17/20 | 20/20 |
| Thoi gian TB anh dai | 5.5-5.8 s | 5.9 s (+0.1..0.4) |
| Thoi gian TB anh ngan | 3.3 s | 3.6 s (+0.3) |
| scan-10p-with-blank.pdf (10 trang @150dpi) + mixed-scan-text-5p.pdf | - | trang trang/nhieu van 0 ky tu, khong bia; 14/15 trang giong het ban cu (1 trang khac chi do batching, kiem lai giong het); tong thoi gian 62.3 s -> 61.4 s |
Ghi chu: "15 anh ngan" duoc do tren ca 20 anh ngan (png/jpg x 10 ngon ngu).

## Rui ro
- Tang so lenh goi chi khi co token nghi ngo; trang co nhieu chuoi (toi da 4/dai) co the +1..2 s.
- Dinh vi dong bang uoc luong; dong khong tim thay (khop <0.6) -> giu goc. Trang nhieu cot da bi cat dai nen it anh huong.
- Doc RTL van nhieu (ar nhan "kalma" doi thinh thoang, khong phai pham vi); chuyen thu tu hien thi->logic dung gia dinh token bat dau bang >=2 ky tu trung tinh o dong co chu RTL.
- Bo phieu 3 doc co the sai neu ca 3 sai nhu nhau (hiem).
- Ket qua phu thuoc batching (nondeterminism nho giua cac lan chay).

## Ap dung vao 18083 (chua lam)
scp -P 20500 .../serving/fast_ocr_amp.py root@ssh3.vast.ai:/workspace/fastmt/fast_ocr_vllm.py   (giu ban .pre-amp)
cp /workspace/fastmt/fast_ocr_vllm.py /workspace/fastmt/fast_ocr_vllm.py.pre-amp  # truoc khi scp
kill $(pgrep -f "[f]ast_ocr_vllm.py"); cd /workspace/fastmt; setsid nohup /workspace/secai-specialist-services/venv/bin/python fast_ocr_vllm.py > ocr-svc.log 2>&1 < /dev/null &
(OCR_PORT mac dinh 18083). Roll back: copy lai .pre-amp.
Thu nghiem dang chay: fast_ocr_exp.py cong 18084 + tunnel local 28084, 28771.
