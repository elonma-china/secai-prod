# Agent B - chong lan OCR va dich (pipelining), 11/10/2026

Stack rieng: `secai-optb-gateway`/`secai-optb-worker` cong 18286, volume `secai-optb-data`, overlay `/home/ict/secai-opt/overlay-b`
(`run_stack_b.sh`, `startb.sh`; `SPEC=1 ./startb.sh`). Adapter OCR ban sao tren VM cong 18085 + tunnel 28085. KHONG sua overlay/adapter dang chay.
Do: `agentB/abtest.py` xen ke A (cong 18285) / B (cong 18286), 5 lan moi cau hinh, trung vi `wall_s` (giay). Du lieu: `results/s9.jsonl` (+ s6/s7/s8).

## Thiet ke da chon
1. **Dich suy doan theo trang cho PDF scan** (`translation/speculative.py`, `pdf_documents.py`, `service.py`, `worker.py`): ngay khi OCR xong trang n, cac chunk cua
   trang do duoc dich (cung muoi `salt`, cung `protect_literals`/`_chunks` nhu `translate_blocks`) trong luc cac trang khac con OCR. `translate_blocks` chi
   dung ket qua khi chunk tinh lai TRUNG CHUOI (sau khi quy chuan tien to placeholder) va cung cap ngon ngu; khong thi dich lai nhu cu. Moi kiem tra
   literal/chat luong/retry van chay tren ket qua. Lan thu lai (attempt>0) luon goi MT moi. Loi cua nhanh suy doan khong lam hong job. Huy job/dong context huy cac lenh MT dang chay.
   Chunk trung nhau trong cung job chi dich 1 lan (so lenh MT <= baseline). Mac dinh chi chay suy doan khi uoc luong cho thay co chunk thich ung = 160
   (tai lieu <= ~15k ky tu, giong het baseline); tai lieu lon hon thi tat (dich nhu cu). Co `TRANSLATE_V2_SPEC_PIN_ESTIMATE=true` de ghim co chunk theo uoc luong
   (nhanh hon nhung chunk khac baseline).
2. **Adapter OCR: ma hoa/phong dai song song, OCR dai ngay khi san sang** (`serving/fast_ocr_vllm` patch): truoc day `_prepare` ma hoa tuan tu TAT CA dai (0.5-1.9 s CPU tren VM)
   roi moi goi model. Nay cat dai (nhanh) roi moi dai `to_thread(_encode)` -> OCR ngay. Van ban OCR khong doi (da kiem: cung van ban 4/4 anh tren ban goc luc do).
3. Worker nhan job: cho khi rong 0.5 s -> 0.15 s (bot ~0.2 s hang doi).

Thu nghiem da loai: (a) endpoint `/v1/ocr/stream` (NDJSON, dich tung dong khi model con giai ma). Do: tat ca dai OCR (prefill ~2 s, giai ma <0.6 s) xong gan nhu cung luc nen khong co gi
de chong lan; them 0 den -0.2 s, nhung them rui ro -> bo khoi patch. (b) khoa chunk theo uoc luong cho moi tai lieu: doi chunk so voi baseline -> chi de sau co.

## So do truoc/sau (trung vi, giay, 5 lan, A = stack 18285 baseline, B = overlay-b + adapter 18085, do XEN KE cung luc)
| Mau | Baseline A | B | Giam |
|---|---|---|---|
| Anh 1 trang en / vi / ja / zh / ar | 6.61 / 4.57 / 7.47 / 7.64 / 6.53 | 5.78 / 3.58 / 5.47 / 6.47 / 5.48 | 0.8-2.0 s |
| Anh dai en / vi / ja / zh | 8.85 / 6.55 / 9.54 / 11.43 | 7.13 / 4.52 / 8.05 / 9.29 | 1.5-2.2 s |
| PDF scan 1 trang en / vi | 6.21 / 4.57 | 5.48 / 4.20 | 0.4-0.7 s |
| PDF scan 3 trang en / ja | 11.54 / 13.81 | 9.01 / 10.99 | 2.5-2.8 s (3.8->3.0 s/trang en) |
| PDF scan 10 trang (scan-10p-with-blank) | 15.72 (1.57 s/trang) | 13.57 (1.36); pin-estimate: 12.5 (1.25) | 2.2 / 2.6 s |
So voi ablation (B khong dich suy doan, chi adapter): anh 1 trang en 5.55, anh dai en 7.03, PDF 3 trang en 9.38 -> phan lon muc giam o anh den tu adapter; dich suy doan chi giup PDF nhieu trang (~0.4-0.5 s o 3 trang, ~1 s o 10 trang voi pin).

**Muc tieu KHONG dat**: anh 1 trang <= 5 s chi dat voi vi (3.6 s); en/ja/ar ~5.5, zh 6.5. PDF scan van ~1.3-3 s/trang, bi chan boi OCR tren GPU (10 trang ~ 9.6 s OCR).
Ly do: (1) anh 1 trang: ~0.4 s xep hang + 0.3 s tien xu ly + ~2.8 s OCR (prefill ~48k token dai da phong x2.5 chi phoi) + ~1.5-2 s dich cua cum chunk cuoi; (2) OCR tra ve toan bo dai gan nhu dong thoi
nen khong co chunk nao dich som duoc; (3) cum chunk cuoi luon cho it nhat 1 lenh MT (~1-1.5 s).

## Chat luong / hoi quy
- Literal: moi mau dat nhu baseline cung luc, tru 2 mau khac do ADAPTER: luc chay s9 adapter 18083 da bi agent khac thay (01:26, doc lai chuoi dac biet) nen A dat 7/7 con ban sao cua toi (ban goc truoc 01:26)
  imgS-ar / imgL-ja 6/7 (truoc do ca hai 6/7, xem s5). Khong lien quan pipeline (anh khong dung dich suy doan). PDF: pdfL-en 7/7 ca hai; pdfL-ja 6/7 ca hai.
- Van ban dich: baseline tu no cung khong tat dinh (OCR/MT khac nhau giua cac lan, vd PDF 3 trang A vs A chi giong ~0.4-0.8), nen so khop chuoi tuyet doi khong dung duoc; cac anh ngan giong het nhau.
- P0 (p0run_b, cong 18286): UC-E07 PASS 14.2 s (A 15.6), UC-E18 PASS 8.9 s (A 10.8), UC-E10 PASS literal 7/7 5.8 s.
- Huy job luc OCR (1.5 s / 5 s): `cancelling -> cancelled` < 1 s, nhu baseline, khong job loi, lenh MT suy doan duoc huy.
- OCR sap: ma loi `OCR_UNAVAILABLE` nhu baseline (anh va PDF).
- Loi da gap/ sua khi lam: placeholder bi MT doi `⟦`->`⟨` nen chi thay phan hex cua tien to khi dung lai ket qua suy doan (neu khong, TOKEN_INTEGRITY_FAILED va dich lai, cham hon baseline voi ja/zh).

## Rui ro
- Mot lenh MT suy doan khong dung se bi bo (lang phi); do duoc `wasted=0` o cac mau thu. Co the tang tai MT neu chunk khong trung (da gioi han: tat khi uoc luong tai lieu lon).
- Chua do 20-30 trang, anh JPG, ban nhieu job dong thoi (stack thu chay 1 job/lan).
- Adapter ban sao cung GPU voi tat ca; ket qua do bi nhieu boi cac agent khac (da xen ke A/B de giam).
- `worker.py` ghi them log exception (khong lo noi dung) khi INTERNAL_ERROR.

## Ap dung
Patch (so voi overlay goc, `-p1` tai thu muc overlay):
`/home/ict/AIEngines/secai/secai-agent-gateway/module-map/translate/optimization/patches/ocr-translate-pipeline.patch`
```
patch -p1 -d /home/ict/secai-opt/overlay < .../patches/ocr-translate-pipeline.patch
# bat: TRANSLATE_V2_SPEC_TRANSLATE_ENABLED=true (mac dinh false) cho worker; tuy chon TRANSLATE_V2_SPEC_PIN_ESTIMATE=true
```
Adapter (VM, ban phai `patch` tren file hien dang chay, luu y file da bi doi 01:26 nen co the can ghep tay phan `_split/_encode/_ocr_strip`):
`.../patches/fast_ocr_vllm-parallel-strip-prep.patch`.
Don dep: `docker rm -f secai-optb-gateway secai-optb-worker; docker volume rm secai-optb-data`; tren VM `pkill -f fast_ocr_b.py`; `pkill -f "L 127.0.0.1:28085"` (tunnel).
