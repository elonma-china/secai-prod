"""Registry knowledge/playbook cho lane1. `load_skill(vuln_class)` trả Skill khớp lớp lỗ hổng,
fallback `generic_rce` khi chưa có playbook riêng (MVP: ognl-rce, sqli-info-leak, rce).

Thêm skill mới = tạo file trong package này rồi map vuln_class vào `_REGISTRY`.
"""
from __future__ import annotations

from . import generic_rce, ognl_rce, sqli
from .base import Skill

_REGISTRY: dict[str, Skill] = {
    "ognl-rce": ognl_rce.SKILL,
    "sqli-info-leak": sqli.SKILL,
    "sqli": sqli.SKILL,
    "rce": generic_rce.SKILL,
    "generic_rce": generic_rce.SKILL,
}

_FALLBACK = generic_rce.SKILL


def load_skill(vuln_class: str, cve: str = "") -> Skill:
    """Trả Skill theo vuln_class (không phân biệt hoa/thường, chuẩn hoá '_'→'-'); miss → generic_rce."""
    key = (vuln_class or "").strip().lower().replace("_", "-")
    return _REGISTRY.get(key, _FALLBACK)


__all__ = ["Skill", "load_skill"]
