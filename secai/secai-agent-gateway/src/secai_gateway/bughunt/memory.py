"""HuntMemory — port of claude-bug-bounty agent.py memory, keyed by run workdir."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .workdir import ensure_run_workdir, session_file

MAX_OBS_CHARS = 3000
MAX_FINDINGS_LOG = 200

LogFn = Callable[[str, str], None]


@dataclass
class BughuntRunState:
    """Per-hunt deps injected into Linch ``RunOptions.deps`` / ``ToolContext.deps``."""

    run_id: str
    target: str
    workdir: Path
    memory: "HuntMemory"
    scope_note: str = ""
    scope_lock: bool = False
    cookies: str = ""
    verdict: str = ""
    on_log: LogFn | None = None
    surface: dict[str, Any] = field(default_factory=dict)


class HuntMemory:
    """Three-layer memory persisted to ``agent_session.json`` under the run workdir."""

    def __init__(self, path: Path | str) -> None:
        self.session_file = Path(path)
        self.working_memory = ""
        self.findings_log: list[dict[str, Any]] = []
        self.observation_buf: list[dict[str, Any]] = []
        self.completed_steps: list[str] = []
        self.step_count = 0
        self._load()

    def _load(self) -> None:
        if not self.session_file.is_file():
            return
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.working_memory = str(data.get("working_memory") or "")
        self.findings_log = list(data.get("findings_log") or [])
        self.observation_buf = list(data.get("observation_buf") or [])[-10:]
        self.completed_steps = list(data.get("completed_steps") or [])
        self.step_count = int(data.get("step_count") or 0)

    def save(self) -> None:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "working_memory": self.working_memory,
            "findings_log": self.findings_log[-MAX_FINDINGS_LOG:],
            "observation_buf": self.observation_buf[-10:],
            "completed_steps": self.completed_steps,
            "step_count": self.step_count,
            "saved_at": datetime.now().isoformat(),
        }
        self.session_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add_observation(self, tool: str, text: str) -> None:
        self.observation_buf.append({
            "tool": tool,
            "ts": datetime.now().isoformat(),
            "text": (text or "")[:MAX_OBS_CHARS],
        })
        if len(self.observation_buf) > 15:
            self.observation_buf = self.observation_buf[-10:]

    def add_finding(self, tool: str, severity: str, text: str) -> None:
        self.findings_log.append({
            "tool": tool,
            "severity": severity,
            "text": (text or "")[:500],
            "ts": datetime.now().isoformat(),
        })

    def record_step(self, tool: str, obs: str) -> None:
        self.add_observation(tool, obs)
        self.completed_steps.append(tool)
        self.step_count += 1
        self._classify(tool, obs)
        self.save()

    def _classify(self, tool: str, obs: str) -> None:
        low = (obs or "").lower()
        sev = "INFO"
        if any(k in low for k in ("critical", "[vuln]", "rce", "sqli confirmed")):
            sev = "HIGH"
        elif any(k in low for k in ("high", "exposed", "secret", "jwt")):
            sev = "MEDIUM"
        elif any(k in low for k in ("finding", "medium", "misconfig")):
            sev = "LOW"
        if sev != "INFO" or "finding" in low:
            self.add_finding(tool, sev, obs[:240])

    def findings_summary(self) -> str:
        if not self.findings_log:
            return "No findings yet."
        by_sev: dict[str, list[str]] = {}
        for f in self.findings_log[-50:]:
            by_sev.setdefault(str(f.get("severity") or "INFO").upper(), []).append(
                f"{f.get('tool')}: {str(f.get('text') or '')[:120]}"
            )
        lines: list[str] = []
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
            if sev in by_sev:
                lines.append(f"[{sev}] ({len(by_sev[sev])} items)")
                lines.extend(f"  • {x}" for x in by_sev[sev][:5])
        return "\n".join(lines) or "No classified findings."

    def recent_observations(self, n: int = 3) -> str:
        recents = self.observation_buf[-n:]
        if not recents:
            return "No tool outputs yet."
        return "\n\n".join(f"[{o['tool']}]\n{o['text']}" for o in recents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "working_memory": self.working_memory,
            "findings_log": self.findings_log[-MAX_FINDINGS_LOG:],
            "completed_steps": list(dict.fromkeys(self.completed_steps)),
            "step_count": self.step_count,
            "findings_summary": self.findings_summary(),
        }


def make_run_state(
    *,
    run_id: str,
    target: str,
    scope_note: str = "",
    settings: Any | None = None,
    on_log: LogFn | None = None,
    scope_lock: bool = False,
    cookies: str = "",
) -> BughuntRunState:
    workdir = ensure_run_workdir(run_id, settings=settings)
    memory = HuntMemory(session_file(workdir))
    return BughuntRunState(
        run_id=run_id,
        target=target,
        workdir=workdir,
        memory=memory,
        scope_note=scope_note or "",
        scope_lock=scope_lock,
        cookies=cookies or "",
        on_log=on_log,
    )
