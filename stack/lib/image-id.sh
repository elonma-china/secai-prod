# shellcheck shell=bash
# image_layers_id <ref>: định danh image không phụ thuộc image store. `.Id` là digest manifest/index trên containerd store
# nhưng là digest config trên overlay2, nên dùng sha256 của danh sách diff_id layer (RootFS.Layers, giống nhau mọi store).
image_layers_id() {
  local j
  j=$(docker image inspect --format '{{json .RootFS.Layers}}' "$1" 2>/dev/null) && [ -n "$j" ] || return 1
  printf 'layers-sha256:%s\n' "$(printf '%s' "$j" | sha256sum | cut -d' ' -f1)"
}
