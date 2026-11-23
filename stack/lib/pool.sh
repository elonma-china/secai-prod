#!/usr/bin/env bash
# In IAS_SERVER_POOL cho profile exploit: <slots> slot trên vm-a (cổng 5101..) rồi <slots> slot trên vm-b (5201..).
set -euo pipefail
n="${1:-}"
[[ "$n" =~ ^[0-9]+$ && "$n" -ge 1 ]] || { echo "usage: pool.sh <slots>  (slots phải là số nguyên >= 1, nhận '${n}')" >&2; exit 2; }
out=()
for vm in a:5100 b:5200; do
  name="${vm%%:*}"; base="${vm##*:}"
  for ((i = 1; i <= n; i++)); do out+=("http://vm-${name}:$((base + i))|/ias-pool-home-${name}/slot${i}"); done
done
(IFS=,; echo "${out[*]}")
