"""Nhóm Triển khai — `GET /showcase-api/deploy/manifests`: Docker Compose + Helm chart Kubernetes.

Chứng minh tiêu chí "cung cấp Docker image + Helm chart" (sheet dòng 101). Trả nội dung file
thật: docker-compose.yml (settings.showcase_docker_compose) + toàn bộ file trong Helm chart
(settings.showcase_helm_dir). Chỉ đọc.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ...config import get_settings

router = APIRouter(prefix="/showcase-api/deploy", tags=["showcase-deploy"])

_MAX = 60_000  # cắt file quá lớn


def _read(path: Path) -> str:
    txt = path.read_text(encoding="utf-8", errors="replace")
    return txt[:_MAX] + ("\n… (đã cắt bớt)" if len(txt) > _MAX else "")


@router.get("/manifests")
def manifests() -> dict:
    s = get_settings()
    out: dict = {
        "docker": {
            "image_sizes": {
                "gateway": "~2 GB",
                "serving_weights": "~24 GB (LLM + embedding + reranker)",
            },
            "compose": None,
        },
        "helm": {"chart": None, "files": []},
    }

    compose = Path(s.showcase_docker_compose)
    if compose.is_file():
        out["docker"]["compose"] = {"name": compose.name, "content": _read(compose)}

    helm_dir = Path(s.showcase_helm_dir)
    if helm_dir.is_dir():
        chart = helm_dir / "Chart.yaml"
        if chart.is_file():
            out["helm"]["chart"] = _read(chart)
        files = []
        for f in sorted(helm_dir.rglob("*")):
            if f.is_file():
                files.append({"path": str(f.relative_to(helm_dir)), "content": _read(f)})
        out["helm"]["files"] = files

    return out
