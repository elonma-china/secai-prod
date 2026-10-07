"""Pure chunkers for the knowledge KB (no network, no Qdrant).

Each ``chunk_*`` function turns one downloaded source into payload dicts that follow
docs/plans/kb-knowledge-grounding.md §3.1. ``text`` is what gets embedded AND reranked, so it
always starts with a title line. The embedding model accepts 512 tokens at most (serving
``max_model_len``), hence MAX_CHARS stays near the 1000-char clip used by sync_autoscan_to_kb.py.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterator

MAX_CHARS = 1000
MIN_CHARS = 120
POINT_NS = uuid.UUID("6f1f6a52-3c1f-5d0e-9a47-2c7a0f6b6e11")  # fixed: re-ingest is idempotent
KNOWLEDGE_KINDS = ("cheatsheet", "requirement", "mitigation", "taxonomy")

# Weaponised payloads only; plain talk about "SQL injection" or "XSS" is defensive content.
_ATTACK_RE = re.compile(
    r"union\s+(?:all\s+)?select\s.+\sfrom\s|<script[^>]*>\s*alert\s*\(|/etc/passwd|"
    r";\s*drop\s+table|or\s+1\s*=\s*1\s*(?:--|#|/\*)|'\s*or\s*'?1'?\s*=\s*'?1|\bsleep\s*\(\s*\d+\s*\)|"
    r"<!ENTITY\s+\S+\s+SYSTEM|javascript\s*:\s*alert|\bon(?:error|load|mouseover|focus)\s*=|<svg[^>]*\bon\w+\s*=|"
    r"<img[^>]+src\s*=\s*['\"]?\s*(?:x|javascript)|bash\s+-i\s+>&\s*/dev/tcp|nc\s+-e\s+/bin/|"
    r"\$\{jndi:|\.\./\.\./\.\./|(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts?|rules)|"
    r"you\s+are\s+now\s+(?:in\s+)?(?:developer|dan|jailbreak)|b[oỏ]\s+qua\s+(?:m[oọ]i\s+)?h[uư][oớ]ng\s+d[aẫ]n\s+tr[uư][oớ]c",
    re.I,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def point_id(doc_id: str) -> str:
    return str(uuid.uuid5(POINT_NS, doc_id))


def sensitivity_of(text: str) -> str:
    return "contains_attack_example" if _ATTACK_RE.search(text) else "defensive"


_REL_LINK = re.compile(r"\[([^\]]+)\]\((?!https?://|mailto:|#)[^)\s]+\)")


_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)(?:\{:[^}]*\})?")


def strip_relative_links(text: str) -> str:
    """`[Label](Other_Cheat_Sheet.md)` → `Label`. A relative link is meaningless outside the repo and the
    model turned it into dead URLs (measured: 425 chunks carried them)."""
    return _REL_LINK.sub(r"\1", _IMAGE.sub("", text))


def norm_ws(text: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\r\n?", "\n", text)).strip()


def strip_markup(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return norm_ws(re.sub(r"\s*\n\s*", "\n", html.unescape(text)))


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Pack paragraphs/code fences into pieces <= limit; hard-split oversize ones on lines/words."""
    blocks: list[str] = []
    for part in re.split(r"(```.*?```)", text, flags=re.S):
        if part.startswith("```"):
            blocks.append(part.strip())
        else:
            blocks.extend(b.strip() for b in re.split(r"\n\s*\n", part) if b.strip())
    pieces: list[str] = []
    for b in blocks:
        fence = re.match(r"```([^\n`]*)\n", b) if b.startswith("```") else None
        if fence and len(b) > limit:
            # A fenced block over the limit: split its body on lines and give every piece its own fence,
            # so no chunk starts or ends inside an unterminated code block.
            lang, body = fence.group(1), b[fence.end():].removesuffix("```").rstrip("\n")
            room = max(200, limit - len(lang) - 10)
            cur: list[str] = []
            size = 0
            for line in body.split("\n"):
                while len(line) > room:
                    if cur:
                        pieces.append(f"```{lang}\n" + "\n".join(cur) + "\n```")
                        cur, size = [], 0
                    pieces.append(f"```{lang}\n{line[:room]}\n```")
                    line = line[room:]
                if cur and size + len(line) + 1 > room:
                    pieces.append(f"```{lang}\n" + "\n".join(cur) + "\n```")
                    cur, size = [], 0
                cur.append(line)
                size += len(line) + 1
            if cur:
                pieces.append(f"```{lang}\n" + "\n".join(cur) + "\n```")
            continue
        while len(b) > limit:
            cut = b.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = b.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            pieces.append(b[:cut].strip())
            b = b[cut:].strip()
        if b:
            pieces.append(b)
    out: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + 2 + len(p) > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def _merge_small(parts: list[str], limit: int = MAX_CHARS) -> list[str]:
    merged: list[str] = []
    for p in parts:
        if merged and (len(p) < MIN_CHARS or len(merged[-1]) < MIN_CHARS) and len(merged[-1]) + 2 + len(p) <= limit:
            merged[-1] = f"{merged[-1]}\n\n{p}"
        else:
            merged.append(p)
    return merged


def make_chunk(*, kind: str, doc_id: str, title: str, body: str, source: str, source_url: str,
               license_: str, attribution: str, version: str, batch: str, parent_id: str = "",
               section: str = "", tags: list[str] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    body = strip_relative_links(body)
    text = f"{title}\n\n{body}".strip()
    payload: dict[str, Any] = {
        "kind": kind, "chunk_kind": kind, "chunk_id": doc_id, "doc_id": doc_id,
        "parent_id": parent_id or doc_id.split("#")[0], "title": title, "text": text,
        "source": source, "source_url": source_url, "license": license_,
        "attribution": attribution, "version": version, "ingest_batch": batch, "lang": "en",
        "section": section, "tags": tags or [], "sensitivity": sensitivity_of(body),
        "text_sha": sha(text),
    }
    payload.update(extra or {})
    return payload


# ── OWASP Cheat Sheet Series ───────────────────────────────────────────────────
_CS_LICENSE = "CC-BY-SA-4.0"
_CS_ATTR = "OWASP Cheat Sheet Series, CC BY-SA 4.0"
_CWE_IN_TEXT = re.compile(r"\bCWE-(\d{1,5})\b", re.I)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _sections(md: str) -> Iterator[tuple[str, str]]:
    """Yield (h2 title, body) pairs; text before the first H2 is the 'Introduction'."""
    cur_title, cur_lines, in_code = "Introduction", [], False
    for line in md.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("## "):
            if "".join(cur_lines).strip():
                yield cur_title, "\n".join(cur_lines)
            cur_title, cur_lines = line[3:].strip(), []
        else:
            cur_lines.append(line)
    if "".join(cur_lines).strip():
        yield cur_title, "\n".join(cur_lines)


def chunk_cheatsheets(repo: Path, version: str, batch: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((repo / "cheatsheets").glob("*.md")):
        md = f.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"#\s+(.+)", md)
        doc_title = m.group(1).strip() if m else f.stem.replace("_", " ")
        html_name = f.stem + ".html"
        for sec_title, body in _sections(md):
            body = re.sub(r"^# .+\n", "", body).strip()
            parts = _merge_small(split_text(body))
            for i, part in enumerate(parts):
                if len(part) < 40:
                    continue
                suffix = f"-{i + 1}" if len(parts) > 1 else ""
                doc_id = f"owasp-cs:{f.stem}#{_slug(sec_title)}{suffix}"
                cwes = sorted({f"CWE-{n}" for n in _CWE_IN_TEXT.findall(part)}, key=lambda s: int(s[4:]))
                out.append(make_chunk(
                    kind="cheatsheet", doc_id=doc_id, parent_id=f"owasp-cs:{f.stem}",
                    title=f"{doc_title} — {sec_title}", body=part, source="owasp-cheatsheet",
                    source_url=f"https://cheatsheetseries.owasp.org/cheatsheets/{html_name}#{_slug(sec_title)}",
                    license_=_CS_LICENSE, attribution=_CS_ATTR, version=version, batch=batch,
                    section=sec_title, tags=[_slug(doc_title.replace("Cheat Sheet", ""))],
                    extra={"cwe_refs": cwes, "capec_entries": [], "mitre_techniques": []}))
    return out


# ── OWASP ASVS 5.0 ─────────────────────────────────────────────────────────────
_ASVS_ROW = re.compile(r"^\|\s*\*\*(\d+\.\d+\.\d+)\*\*\s*\|\s*(.+?)\s*\|\s*([123])\s*\|\s*$")


def chunk_asvs(repo: Path, version: str, batch: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted((repo / "5.0" / "en").glob("0x[12]*.md")):
        chapter, section = "", ""
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# "):
                chapter = line[2:].strip()
            elif line.startswith("## "):
                section = line[3:].strip()
            m = _ASVS_ROW.match(line)
            if not m:
                continue
            rid, desc, level = m.groups()
            desc = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", desc)
            title = f"ASVS 5.0.0 requirement {rid} (level {level})"
            body = f"{desc}\n\nChapter: {chapter}. Section: {section}."
            doc_id = f"owasp-asvs:v5.0.0-{rid}"
            out.append(make_chunk(
                kind="requirement", doc_id=doc_id, parent_id=f"owasp-asvs:{rid.rsplit('.', 1)[0]}",
                title=title, body=body, source="owasp-asvs", license_="CC-BY-SA-4.0",
                source_url="https://github.com/OWASP/ASVS/tree/master/5.0/en",
                attribution="OWASP Application Security Verification Standard 5.0, CC BY-SA 4.0",
                version=version, batch=batch, section=section, tags=[f"asvs-l{level}"],
                extra={"requirement_id": rid, "asvs_level": int(level),
                       "cwe_refs": [], "capec_entries": [], "mitre_techniques": []}))
    return out


# ── MITRE (CWE / CAPEC / ATT&CK) ──────────────────────────────────────────────
_MITRE_LICENSE = "MITRE-ToU"


def _mitre_attr(what: str) -> str:
    return f"{what} © The MITRE Corporation, used under the MITRE Terms of Use"


def _txt(el: ET.Element | None) -> str:
    return strip_markup("".join(el.itertext())) if el is not None else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    found = _children(el, name)
    return found[0] if found else None


def _pack(title: str, lines: list[str], doc_base: str, **kw: Any) -> list[dict[str, Any]]:
    """Pack text lines into chunks <= MAX_CHARS, numbering doc ids when more than one."""
    pieces: list[str] = []
    for line in lines:
        pieces.extend(split_text(line, MAX_CHARS - len(title) - 4))
    groups: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > MAX_CHARS - len(title) - 4:
            groups.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        groups.append(cur)
    out = []
    for i, g in enumerate(groups):
        suffix = f"-{i + 1}" if len(groups) > 1 else ""
        out.append(make_chunk(doc_id=f"{doc_base}{suffix}", title=title, body=g, **kw))
    return out


def chunk_cwe(xml_path: Path, batch: str) -> list[dict[str, Any]]:
    root = ET.parse(xml_path).getroot()
    version = f"CWE-{root.get('Version')}"
    out: list[dict[str, Any]] = []
    for w in root.iter():
        if _local(w.tag) != "Weakness":
            continue
        cid = f"CWE-{w.get('ID')}"
        name = w.get("Name", "")
        if w.get("Status") in {"Deprecated", "Obsolete"}:
            continue
        capecs = sorted({f"CAPEC-{_cap.get('CAPEC_ID')}" for rap in _children(w, "Related_Attack_Patterns")
                         for _cap in _children(rap, "Related_Attack_Pattern")})
        base = dict(kind="mitigation", parent_id=cid, source="cwe", license_=_MITRE_LICENSE,
                    source_url=f"https://cwe.mitre.org/data/definitions/{w.get('ID')}.html",
                    attribution=_mitre_attr("CWE"), version=version, batch=batch,
                    extra={"cwe_id": cid, "cwe_refs": [cid], "capec_entries": capecs, "mitre_techniques": []})
        pm = _child(w, "Potential_Mitigations")
        if pm is not None:
            lines = []
            for m in _children(pm, "Mitigation"):
                phase = ", ".join(_txt(p) for p in _children(m, "Phase"))
                desc, eff = _txt(_child(m, "Description")), _txt(_child(m, "Effectiveness"))
                notes = _txt(_child(m, "Effectiveness_Notes"))
                head = f"[{phase}] " if phase else ""
                tail = f" (Effectiveness: {eff}{'; ' + notes if notes else ''})" if eff else ""
                if desc:
                    lines.append(f"{head}{desc}{tail}")
            if lines:
                out.extend(_pack(f"{cid} {name} — potential mitigations", lines, f"cwe:{cid}#mitigation",
                                 section="mitigation", tags=["cwe-mitigation"], **base))
        dm = _child(w, "Detection_Methods")
        if dm is not None:
            lines = []
            for m in _children(dm, "Detection_Method"):
                meth, desc = _txt(_child(m, "Method")), _txt(_child(m, "Description"))
                eff = _txt(_child(m, "Effectiveness"))
                if desc:
                    lines.append(f"{meth}: {desc}" + (f" (Effectiveness: {eff})" if eff else ""))
            if lines:
                out.extend(_pack(f"{cid} {name} — detection methods", lines, f"cwe:{cid}#detection",
                                 section="detection", tags=["cwe-detection"], **base))
    return out


def chunk_capec(xml_path: Path, batch: str) -> list[dict[str, Any]]:
    root = ET.parse(xml_path).getroot()
    version = f"CAPEC-{root.get('Version')}"
    out: list[dict[str, Any]] = []
    for ap in root.iter():
        if _local(ap.tag) != "Attack_Pattern" or ap.get("Status") in {"Deprecated", "Obsolete"}:
            continue
        cid = f"CAPEC-{ap.get('ID')}"
        cwes = sorted({f"CWE-{rw.get('CWE_ID')}" for rws in _children(ap, "Related_Weaknesses")
                       for rw in _children(rws, "Related_Weakness")}, key=lambda s: int(s[4:]))
        ms = _child(ap, "Mitigations")
        lines = [t for t in (_txt(m) for m in (_children(ms, "Mitigation") if ms is not None else [])) if t]
        if not lines:
            continue
        out.extend(_pack(f"{cid} {ap.get('Name', '')} — mitigations", lines, f"capec:{cid}#mitigation",
                         kind="mitigation", parent_id=cid, source="capec", license_=_MITRE_LICENSE,
                         source_url=f"https://capec.mitre.org/data/definitions/{ap.get('ID')}.html",
                         attribution=_mitre_attr("CAPEC"), version=version, batch=batch,
                         section="mitigation", tags=["capec-mitigation"],
                         extra={"capec_id": cid, "cwe_refs": cwes, "capec_entries": [cid], "mitre_techniques": []}))
    return out


def chunk_attack(json_path: Path, batch: str) -> list[dict[str, Any]]:
    objs = json.loads(json_path.read_text(encoding="utf-8"))["objects"]
    live = lambda o: not o.get("revoked") and not o.get("x_mitre_deprecated")  # noqa: E731
    techniques = {o["id"]: o for o in objs if o["type"] == "attack-pattern" and live(o)}
    mitigations = {o["id"]: o for o in objs if o["type"] == "course-of-action" and live(o)}
    # x_mitre_attack_spec_version is the STIX spec (3.x), not the ATT&CK release (e.g. 19.2).
    version = "ATT&CK-" + next((o.get("x_mitre_version", "") for o in objs if o["type"] == "x-mitre-collection"), "") .strip() or "ATT&CK-enterprise"

    def ext_id(o: dict[str, Any]) -> str:
        return next((r["external_id"] for r in o.get("external_references", []) if r.get("source_name") == "mitre-attack"), "")

    out: list[dict[str, Any]] = []
    rel_count: dict[str, list[str]] = {}
    for r in objs:
        if r["type"] == "relationship" and r.get("relationship_type") == "mitigates" and live(r):
            if r["source_ref"] in mitigations and r["target_ref"] in techniques:
                rel_count.setdefault(r["source_ref"], []).append(r["target_ref"])
    for mid, m in mitigations.items():
        m_ext = ext_id(m)
        url = f"https://attack.mitre.org/mitigations/{m_ext}/"
        base = dict(kind="mitigation", source="attack", license_=_MITRE_LICENSE, attribution=_mitre_attr("ATT&CK"),
                    version=version, batch=batch, source_url=url)
        techs = [ext_id(techniques[t]) for t in rel_count.get(mid, [])]
        out.extend(_pack(f"ATT&CK mitigation {m_ext} {m.get('name', '')}", [strip_markup(m.get("description", ""))],
                         f"attack:{m_ext}#overview", parent_id=m_ext, section="mitigation", tags=["attack-mitigation"],
                         extra={"mitigation_id": m_ext, "mitre_techniques": sorted(set(techs)), "cwe_refs": [], "capec_entries": []},
                         **base))
    for r in objs:
        if r["type"] != "relationship" or r.get("relationship_type") != "mitigates" or not live(r):
            continue
        m, t = mitigations.get(r["source_ref"]), techniques.get(r["target_ref"])
        if not m or not t:
            continue
        t_ext, m_ext = ext_id(t), ext_id(m)
        desc = strip_markup(r.get("description", ""))
        if len(desc) < 30:
            continue
        out.extend(_pack(f"ATT&CK {t_ext} {t.get('name', '')} — mitigation {m_ext} {m.get('name', '')}", [desc],
                         f"attack:{t_ext}#{m_ext}", parent_id=t_ext, section="mitigation", tags=["attack-mitigation"],
                         extra={"technique_id": t_ext, "mitigation_id": m_ext, "mitre_techniques": [t_ext],
                                "cwe_refs": [], "capec_entries": []},
                         kind="mitigation", source="attack", license_=_MITRE_LICENSE, attribution=_mitre_attr("ATT&CK"),
                         version=version, batch=batch, source_url=f"https://attack.mitre.org/techniques/{t_ext.replace('.', '/')}/"))
    return out


def chunk_cwe_top25(xml_path: Path, batch: str) -> list[dict[str, Any]]:
    """The yearly "CWE Top 25" lists, from the MITRE views (members are in rank order). The year
    is in every chunk title so the model cannot attach another year's list to a question."""
    root = ET.parse(xml_path).getroot()
    version = f"CWE-{root.get('Version')}"
    names = {w.get("ID"): w.get("Name", "") for w in root.iter() if _local(w.tag) == "Weakness"}
    out: list[dict[str, Any]] = []
    for v in root.iter():
        if _local(v.tag) != "View":
            continue
        m = re.search(r"(\d{4}) CWE(?:/SANS)? Top 25", v.get("Name", ""))
        if not m or v.get("Status") == "Obsolete":
            continue
        year = m.group(1)
        members = [h.get("CWE_ID") for mem in _children(v, "Members") for h in _children(mem, "Has_Member") if h.get("CWE_ID")]
        if not members:
            continue
        lines = [f"{i}. CWE-{cid} {names.get(cid, '')}".rstrip() for i, cid in enumerate(members, 1)]
        out.extend(_pack(f"{year} CWE Top 25 Most Dangerous Software Weaknesses (MITRE CWE view {v.get('ID')}, rank order)", lines,
                         f"cwe-top25:{year}", kind="taxonomy", parent_id=f"cwe-top25:{year}", source="cwe", license_=_MITRE_LICENSE,
                         source_url=f"https://cwe.mitre.org/data/definitions/{v.get('ID')}.html", attribution=_mitre_attr("CWE"),
                         version=version, batch=batch, section="top25", tags=["cwe-top25", year],
                         extra={"cwe_refs": [f"CWE-{c}" for c in members], "capec_entries": [], "mitre_techniques": [], "edition": year}))
    return out


_TOP10_FILE = re.compile(r"^(A\d{2})_(\d{4})[-_](.+)\.md$")


def chunk_owasp_top10(repo: Path, version: str, batch: str, editions: tuple[str, ...] = ("2021", "2025")) -> list[dict[str, Any]]:
    """OWASP Top 10 lists + category texts for the given editions (CC-BY-SA-4.0)."""
    out: list[dict[str, Any]] = []
    for ed in editions:
        docs = repo / ed / "docs" / "en"
        idx = docs / "index.md"
        if idx.exists():
            items = re.findall(r"^\d+\.\s*\[([^\]]+)\]", idx.read_text(encoding="utf-8"), flags=re.M)
            if items:
                out.extend(_pack(f"OWASP Top 10:{ed} — the ten categories (edition {ed})", [f"{i}. {t}" for i, t in enumerate(items, 1)],
                                 f"owasp-top10:{ed}#list", kind="taxonomy", parent_id=f"owasp-top10:{ed}", source="owasp-top10",
                                 license_="CC-BY-SA-4.0", source_url=f"https://owasp.org/Top10/{ed}/",
                                 attribution=f"OWASP Top 10:{ed}, CC BY-SA 4.0", version=version, batch=batch, section="list",
                                 tags=["owasp-top10", ed], extra={"cwe_refs": [], "capec_entries": [], "mitre_techniques": [], "edition": ed}))
        for f in sorted(docs.glob("A*.md")):
            m = _TOP10_FILE.match(f.name)
            if not m:
                continue
            code, _, slug = m.groups()
            md = f.read_text(encoding="utf-8", errors="replace")
            h1 = re.match(r"#\s+(.+)", md)
            cat = strip_relative_links((h1.group(1).strip() if h1 else f"{code} {slug.replace('_', ' ')}").lstrip("# ")).strip()
            for sec_title, body in _sections(md):
                body = re.sub(r"^# .+\n", "", body).strip()
                for i, part in enumerate(_merge_small(split_text(body))):
                    if len(part) < 40:
                        continue
                    cwes = sorted({f"CWE-{n}" for n in _CWE_IN_TEXT.findall(part)}, key=lambda x: int(x[4:]))
                    out.append(make_chunk(
                        kind="taxonomy", doc_id=f"owasp-top10:{ed}:{code}#{_slug(sec_title)}-{i + 1}", parent_id=f"owasp-top10:{ed}:{code}",
                        title=f"OWASP Top 10:{ed} {cat} — {sec_title}", body=part, source="owasp-top10",
                        source_url=f"https://owasp.org/Top10/{ed}/{f.stem}/", license_="CC-BY-SA-4.0",
                        attribution=f"OWASP Top 10:{ed}, CC BY-SA 4.0", version=version, batch=batch, section=sec_title,
                        tags=["owasp-top10", ed, code], extra={"cwe_refs": cwes[:20], "capec_entries": [], "mitre_techniques": [], "edition": ed}))
    return out


def chunk_ghsa(repo: Path, cves: set[str], version: str, batch: str, max_detail_chunks: int = 4) -> list[dict[str, Any]]:
    """Remediation text from the GitHub Advisory Database (CC-BY-4.0), only for the given CVEs (the KB's KEV set):
    what the advisory says to do, plus the vulnerable/patched versions per package. Stored as kind `mitigation`
    (already excluded from entity searches); the CVE ids live in `cve_refs`, never in `cve_id`."""
    out: list[dict[str, Any]] = []
    for f in sorted((repo / "advisories" / "github-reviewed").rglob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        ids = [a for a in d.get("aliases", []) if a.startswith("CVE-")]
        wanted = [c for c in ids if c in cves]
        if not wanted:
            continue
        gid, summary = d["id"], (d.get("summary") or "").strip()
        title_base = f"{gid} ({', '.join(ids)}) — {summary}"
        cwes = [str(x) for x in (d.get("database_specific") or {}).get("cwe_ids", []) if str(x).startswith("CWE-")]
        base = dict(kind="mitigation", parent_id=gid, source="ghsa", license_="CC-BY-4.0",
                    source_url=f"https://github.com/advisories/{gid}", attribution="GitHub Advisory Database, CC BY 4.0",
                    version=version, batch=batch)
        extra = {"cve_refs": ids, "cwe_refs": cwes, "capec_entries": [], "mitre_techniques": [], "ghsa_id": gid,
                 "published": d.get("published"), "modified": d.get("modified")}
        lines, seen = [], set()
        for aff in d.get("affected", []):
            pkg = aff.get("package") or {}
            for rng in aff.get("ranges", []):
                introduced = next((e["introduced"] for e in rng.get("events", []) if "introduced" in e), None)
                fixed = next((e["fixed"] for e in rng.get("events", []) if "fixed" in e), None)
                line = (f"{pkg.get('name')} ({pkg.get('ecosystem')}): vulnerable from {introduced or 'unknown'}"
                        + (f", fixed in {fixed}" if fixed else ", no fixed version listed"))
                if line not in seen:
                    seen.add(line)
                    lines.append(line)
        if lines:
            out.extend(_pack(f"{title_base} — patched versions", lines[:12], f"ghsa:{gid}#patched", section="patched-versions",
                             tags=["ghsa", "patched-versions"], extra=extra, **base))
        body = strip_relative_links(re.sub(r"^#+\s*(?:Summary|Impact|Patches|Workarounds|References|Details)\s*$", "", d.get("details") or "", flags=re.M | re.I)).strip()
        for i, part in enumerate(_merge_small(split_text(body))[:max_detail_chunks]):
            if len(part) >= 60:
                out.append(make_chunk(doc_id=f"ghsa:{gid}#details-{i + 1}", title=f"{title_base} — advisory text", body=part,
                                      section="advisory", tags=["ghsa", "advisory"], extra=extra, **base))
    return out


def dedup(chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Drop duplicate doc_ids (keep first) and identical texts; report counts."""
    seen_id: set[str] = set()
    seen_text: set[str] = set()
    out, dup_id, dup_text = [], 0, 0
    for c in chunks:
        if c["doc_id"] in seen_id:
            dup_id += 1
            continue
        if c["text_sha"] in seen_text:
            dup_text += 1
            continue
        seen_id.add(c["doc_id"])
        seen_text.add(c["text_sha"])
        out.append(c)
    return out, {"dup_doc_id": dup_id, "dup_text": dup_text}
