#!/usr/bin/env bash
# PostToolUse(Bash): sau `gh pr merge`, nhắc agent chạy quy trình review → release prod.
# Chỉ nhắc (không tự release): release đổi prod nên phải do agent chạy có chủ đích và báo kết quả.
cmd=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null) || exit 0
case "$cmd" in
  *"gh pr merge"*) ;;
  *) exit 0 ;;
esac
python3 - <<'PY'
import json
msg = ("Vừa chạy `gh pr merge`. Nếu merge thành công, làm theo CLAUDE.md mục 'Sau khi merge PR → review & release prod': "
       "(1) `scripts/wt-prune.sh --yes`; (2) cập nhật checkout chính `git -C <main> merge --ff-only origin/main`; "
       "(3) `scripts/release-prod.sh plan` rồi đọc diff (deps/Dockerfile/env đổi? rủi ro?); "
       "(4) `scripts/release-prod.sh run --yes` (test + build + smoke + switch, tự rollback nếu health sai); "
       "(5) báo user: sha cũ→mới, số test, health trước/sau. Chỉ dừng hỏi user nếu review thấy rủi ro (xem CLAUDE.md) hoặc gate fail.")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
PY
