# Gói phát hành GHCR + Docker Hub: phát hành thật và thử trên VM Vast sạch (2026-11-27/28)

Nhánh `feat/stack-ghcr`. Kế hoạch: `docs/superpowers/plans/2026-11-27-stack-ghcr.md`. Image dự án ở GHCR public, dữ liệu ở
repo Docker Hub private `docker.io/secai1999/secai-data` (gói miễn phí, quyết định của chủ dự án).

## Kết quả: ĐẠT

Trên VM Vast sạch, chạy đúng như người vận hành (curl `stack/secai` từ raw GitHub ở commit `dee42670` → `./secai install` có tải
dữ liệu thật bằng token Docker Hub chỉ đọc → `status` → `check` → nghiệm thu API với stub → `upgrade` → `rollback` →
`data update` → bài thử tự rollback): **19/19 bước đạt, verdict `pass`**.

## Đã phát hành (digest đọc lại từ registry, không lấy từ máy local)

Image GHCR, đọc lại **ẩn danh** (`stack/lib/registry.py digest`, như máy đích):

| Package | Tag | Digest |
|---|---|---|
| `ghcr.io/elonma-china/secai-agent-gateway` | `2026.11.28-11e95cc3` (V1) | `sha256:3b17e653239521dab74e55a8cbd190d648f213a3f1e51977205bf65ded2e5585` |
| `ghcr.io/elonma-china/secai-pentestcode-vm` | `2026.11.28-11e95cc3` | `sha256:ed0d75c196a93b39d73264f608ed8b4db3b14a427175413043601ca4f17639fd` |
| `ghcr.io/elonma-china/secai-stack` | `2026.11.28-11e95cc3` | `sha256:2752b527de1ea14eb30b6aa8c29881c2bf259cac65711aea7748c60c93d49a7b` |
| `ghcr.io/elonma-china/secai-agent-gateway` | `2026.11.28-dee42670` (V2) | `sha256:91592514c76385f17889f50abcb53a5e9fb0aaf43da1fc12613d0ea894ab76f4` |
| `ghcr.io/elonma-china/secai-pentestcode-vm` | `2026.11.28-dee42670` | `sha256:b9981bf6f57c0b93086f6e5d1ec9c0a1aa4da0ccd631cdd37a79f95eff07f347` |
| `ghcr.io/elonma-china/secai-stack` | `2026.11.28-dee42670` | `sha256:ba48baebab8b438ae1a5a542cb0f1c1a3840c54671dba1150fe3184613e80986` |
| `ghcr.io/elonma-china/secai-stack` | `stable` (→ V2) | `sha256:ba48baebab8b438ae1a5a542cb0f1c1a3840c54671dba1150fe3184613e80986` |

Dữ liệu Docker Hub (repo `is_private=true`; đọc lại bằng token chỉ đọc; ẩn danh trả 401):

| Tag | Digest | Ghi chú |
|---|---|---|
| `docker.io/secai1999/secai-data:2026.11.27` | `sha256:67508336e64c5692c4ee79c69e8e457ae2105fc6c903ef6c3a647a817f062179` | đóng gói 2026-11-27 15:21Z |
| `docker.io/secai1999/secai-data:2026.11.27.2` | `sha256:9011a80d8e6af7f42d3350193e7f6220c36acc6bf3e9f897f3771d89890d7171` | **cùng nội dung** (chỉ khác nhãn version), để thử `data update` |

Image nền ghim trong cả hai manifest: qdrant `v1.10.1` `sha256:6a34a799…`, neo4j `5.20-community` `sha256:52d3dec8…`,
redis `7-alpine` `sha256:7f7d30b7…`, elasticsearch `8.15.3` `sha256:01c17320…`. Manifest V1/V2 ghi dữ liệu mặc định
`2026.11.27` kèm digest đã kiểm.

## Gate phát hành (trên máy dev, không chạm prod)

| | V1 (`11e95cc3`) | V2 (`dee42670`) |
|---|---|---|
| pytest toàn bộ trên `git archive` | **3574 passed, 18 skipped, 7 xfailed** (22 phút) | **3574 passed, 18 skipped, 7 xfailed** (28 phút) |
| build FULL, import app, `/app/src` == archive | đạt | đạt |
| smoke `secai-stack-gateway-smoke-20261128` trên 127.0.0.1 | đạt (`git_sha` đúng, dep không tệ hơn prod, languages 200) | đạt |
| quét secret bundle (luật + secret thật) | sạch | sạch |
| đọc lại ẩn danh sau push (`published.json`) | khớp manifest | khớp manifest |

`publish-data` (gói 5,2 GB đóng từ dữ liệu thật hôm 27): quét 1.357.237 nội dung / 7,44 GB gồm payload Qdrant 842.928 điểm qua
API chỉ đọc; **0 phát hiện chặn, 0 khớp secret thật**; trong corpus công khai chỉ đếm (exploitdb 2.578 gán password…, chi tiết
ở `data-release.json`). Kiểm `is_private` qua Docker Hub API trước push. Push 14 phút. Token ghi chỉ đi qua stdin của
`docker login` vào `DOCKER_CONFIG` tạm.

Lần chạy V1 đầu (`c33427b7`) dừng trước mọi push ở bước kiểm tag: tôi thay `registry.py` (thêm mã 3 = tag chưa có) giữa lúc nó
chạy. Không có gì được push; đã sửa và phát hành lại từ commit mới.

## VM thử

Vast instance **62894822**, Spain, KVM Ubuntu 22.04, 62 GB RAM, đĩa 150 GB, **$0,203/giờ**, thuê 01:11:08, huỷ 01:38:38 +07
(27,5 phút, khoảng **$0,09**). `vastai show instance 62894822 --raw` → `{"instances": null}`; tài khoản còn 0 instance, 0 timer.

| Bước | Thời gian | Kết quả |
|---|---|---|
| fetch (curl `secai` + `model_stub.py` từ raw GitHub ở `dee42670`) | 1 s | sha256 ghi trong `fetch.log` |
| install V1, dữ liệu `2026.11.27` từ Docker Hub bằng token chỉ đọc | 13 phút 46 giây | Qdrant 842.928/842.928 điểm khớp MANIFEST; Neo4j 587.393 nút / 5.414.825 quan hệ khớp; `check.sh all` đạt |
| status1 | 1 s | `version=2026.11.28-11e95cc3`, `health_git_sha=11e95cc3` = manifest, `images_match=true` |
| nghiệm thu API (`run-api.sh`, model stub) | 2 s | đạt UI-CHAT-1, UI-CHAT-2, UI-TR-1, UI-RPT-1; bỏ qua có lý do UI-POC-1..3 (cần LLM thật gọi tool + lab), UI-TR-2/3 (profile translation không thuộc bộ phát hành) |
| upgrade → V2 | 33 s | `health_git_sha=dee42670`, `images_match=true` |
| rollback → V1 | 26 s | đạt |
| data update `2026.11.27.2` | 7 phút 5 giây | nạp bên cạnh bộ cũ, số đếm khớp, bộ cũ giữ ở `/opt/secai/data/2026.11.27` |
| failtest: tắt stub rồi upgrade V2 | 54 s | mã 4 (đã tự quay về V1; check sau khi quay về trượt vì stub đang tắt, đúng như thiết kế); status5 = V1, `images_match=true` |
| bật lại stub, chờ gateway thấy stub, check3 | 15 s | đạt |

Token chỉ đọc chép lên VM qua stdin (mode 0600), xoá ngay sau bài thử (kiểm lại: không còn tệp, không có `/root/.docker`).
Token ghi không bao giờ rời máy dev. Kết quả đã gom và kiểm sha256 ở `local/stack-eval/results-20261128-013803` (không có
token/khoá nào trong đó).

## Review độc lập

Hai lượt review nhiều góc nhìn có kiểm chứng phản biện (38 phát hiện, 35 xác nhận, đã sửa hết; danh sách trong lịch sử commit
`36462209`, `18bc1a35`, `11e95cc3`). Đáng kể: bộ nạp secret thật bắt nhầm `TOKENIZER`/`*_FILE`; rollback/upgrade có thể để
trạng thái lẫn lộn; `--from-pkg` bỏ quét payload Qdrant; `registry.py` coi 429/5xx là "tag chưa có"; `prune-data` có thể xoá
bản dữ liệu mà `stable` dùng; `DOCKER_CONFIG` tạm phải có `config.json` mồi và giữ Docker context.

## Chưa kiểm / giới hạn

- **Chất lượng model**: chỉ có stub (`model_stub.py`). Đã chứng minh đường ống, khớp version/digest, dữ liệu nạp đúng số đếm,
  truy vấn RAG chính xác CVE dùng dữ liệu thật; chưa chứng minh chất lượng LLM/embedding/rerank.
- UI-POC-1..3 (exploit-v2 thật) cần LLM gọi tool và lab: chưa chạy.
- `2026.11.27.2` cùng nội dung với `2026.11.27`: `data update` được kiểm về cơ chế (tải, nạp bộ mới bên cạnh, chuyển, giữ bộ cũ),
  không phải về khác biệt dữ liệu.
- `data rollback`, `prune-data` thật trên Docker Hub (cần token có quyền Delete), đăng nhập qua Docker context không mặc định:
  chỉ kiểm bằng test giả.
- Mật khẩu Neo4j prod dài 9 ký tự, dưới ngưỡng 12 ký tự của tầng "secret thật" nên không được so khớp.
- Một hồ sơ JSON trong `~/secai-releases` (`devspace-review-tokens.json`, chủ uid 10001, 0600) không đọc được khi nạp secret thật.
- Lưu lượng Docker Hub: mỗi lần cài kéo khoảng 5,2 GB dữ liệu từ repo private; chưa đo giới hạn băng thông của gói miễn phí.
- Chuyển prod trên máy này sang gói: chưa làm (kế hoạch + rollback ở `stack/README.md`).
