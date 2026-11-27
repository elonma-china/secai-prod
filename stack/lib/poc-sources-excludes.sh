# Một danh sách duy nhất: thư mục cấp 1 của poc-sources/ không đi vào gói dữ liệu (pack.sh) và không đưa lên VM (eval-vast.sh packtest).
# Chúng chỉ được đọc khi dựng lại catalog (collector) hoặc git pull, không phải lúc chạy; bản đích chỉ-đọc. `.git` bị loại ở mọi độ sâu.
# priority/ (PoC chuẩn của team) vẫn được giữ.
POC_SOURCES_SKIP=(trickest-cve PoC-in-GitHub '_manual-untracked-backup-*')
