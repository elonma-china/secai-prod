Docker image:

Kịch bản yêu cầu chạy `docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'` để xác nhận image và dung lượng, kèm ghi chú nguyên văn trong cột F (đóng gói trên vast.ai + Kubernetes cho hệ thống lớn).

Output lệnh `docker images` là bước chạy trực tiếp trên môi trường triển khai, không có file kết quả tiền-trích trong hồ sơ gốc phần AI. Dung lượng image được ghi trong hướng dẫn cài đặt:

**`04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md`** — dòng 24 (image gateway ~2 GB) và dòng 33 (image + trọng số serving ~24 GB):
````
| Dung lượng ổ đĩa trống | Khoảng 20 GB (dữ liệu ~7 GB, cơ sở dữ liệu sau phục hồi ~7 GB, image ~2 GB) |
````
````
| Dung lượng ổ đĩa | Khoảng 100 GB (image và trọng số mô hình ~24 GB) |
````

Helm chart Kubernetes: tìm toàn bộ 4 thư mục hồ sơ gốc phần AI (`find . -iname 'Chart.yaml' -o -iname 'values.yaml' -o -iname '*helm*'`) không có kết quả — không có Helm chart trong hồ sơ gốc phần AI.

**Kết luận:** Kịch bản đối chiếu được một phần: dung lượng image được ghi trong hướng dẫn cài đặt (gateway ~2 GB; serving + trọng số ~24 GB), nhưng output lệnh `docker images` là bước chạy trực tiếp không có file trích trong hồ sơ, và Helm chart Kubernetes không hiện diện trong hồ sơ gốc phần AI (4 thư mục 01–04).
