"""Persistent memory of the candidate's skill answers (skills_memory.json).

Run `python skill_memory.py` to list remembered skills.
Run `python skill_memory.py --forget "Docker"` to remove one.
"""
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

MEMORY_FILE = Path(__file__).resolve().parent / "skills_memory.json"
_MEMORY_PREFIX = "(from memory)"


def _key(skill: str) -> str:
    """Normalize names: 'Node.js' == 'nodejs' == 'NodeJS'. Keeps + and # (C++, C#)."""
    return re.sub(r"[^a-z0-9+#]", "", skill.lower())


def load_memory() -> dict:
    if not MEMORY_FILE.exists():
        return {"version": 1, "skills": {}}
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        data.setdefault("skills", {})
        return data
    except json.JSONDecodeError:
        backup = MEMORY_FILE.with_suffix(".corrupt.json")
        MEMORY_FILE.replace(backup)
        print(f"WARNING: {MEMORY_FILE.name} was corrupt, moved to {backup.name}")
        return {"version": 1, "skills": {}}


def save_memory(data: dict) -> None:
    """Atomic write so a crash never leaves a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=MEMORY_FILE.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, MEMORY_FILE)


def format_for_prompt(data: dict | None = None, max_evidence: int = 120) -> str:
    """Compact text summary to inject into the agent prompt."""
    data = data or load_memory()
    have, dont = [], []
    for entry in data["skills"].values():
        if entry["status"] == "have":
            ev = entry.get("evidence", "").strip()[:max_evidence]
            have.append(f"{entry['name']} ({ev})" if ev else entry["name"])
        else:
            dont.append(entry["name"])
    if not have and not dont:
        return "No remembered skills yet."
    text = (f"HAVE: {' | '.join(have) or 'none'}\n"
            f"DON'T HAVE: {', '.join(dont) or 'none'}")
    # CrewAI treats {braces} as template variables, so strip them
    return text.replace("{", "(").replace("}", ")")


def record_verification(verification) -> tuple[int, int]:
    """Save confirmed/denied skills from a SkillVerification. Returns (added, updated)."""
    data = load_memory()
    added = updated = 0
    today = date.today().isoformat()

    for item in verification.skills:
        if item.status == "confirmed_by_user":
            status = "have"
        elif item.status == "denied_by_user":
            status = "dont_have"
        else:
            continue  # 'in_resume' -> the resume is already the source of truth

        evidence = item.evidence.strip()
        if evidence.startswith(_MEMORY_PREFIX):
            evidence = evidence[len(_MEMORY_PREFIX):].strip()

        k = _key(item.skill)
        if not k:
            continue
        old = data["skills"].get(k)
        new = {"name": item.skill, "status": status, "evidence": evidence, "updated": today}
        if old is None:
            added += 1
        elif (old["status"], old.get("evidence")) != (status, evidence):
            updated += 1
        else:
            continue
        data["skills"][k] = new

    if added or updated:
        save_memory(data)
    return added, updated


def forget(skill: str) -> bool:
    data = load_memory()
    if data["skills"].pop(_key(skill), None) is None:
        return False
    save_memory(data)
    return True


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--forget":
        print("Removed." if forget(sys.argv[2]) else "Skill not found.")
    else:
        print(format_for_prompt())