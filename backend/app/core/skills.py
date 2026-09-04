"""
Aegis — Chat Skills

Lightweight, instructions-only equivalent of Claude's Agent Skills, scoped to
Chat Mode only. Chat Mode is a single conversational completion with no tool
loop (see app/prompts/chat.py: "You are a conversationalist ONLY and CANNOT
execute any tools") — so there's no mechanism for a skill to run code the way
Claude's bash-driven Skills do, nor should there be: this agent already has
real MCP tools with real access on the user's machine in Agent Mode, so
letting arbitrary downloaded skill folders execute code would be a genuine
local attack surface. Skills here are pure prompt guidance.

A skill is a folder under the skills directory containing SKILL.md with YAML
frontmatter (name, description) plus a Markdown body. Every chat turn, all
skills' name+description pairs are cheap enough to include in the system
prompt unconditionally (mirrors Claude's "Level 1" metadata-always-loaded).
The user's message is then matched against each description by keyword
overlap — no extra LLM call — and only matched skills' full bodies get
injected into that turn's prompt (mirrors "Level 2: loaded when triggered").
"""

import os
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_data_dir = os.environ.get("AEGIS_DATA_DIR")
SKILLS_DIR = Path(_data_dir) / "skills" if _data_dir else BASE_DIR / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_STOPWORDS = {
    "the", "a", "an", "is", "are", "and", "or", "to", "of", "in", "for",
    "on", "with", "this", "that", "you", "your", "i", "me", "my", "it",
    "be", "as", "at", "by", "from", "when", "use", "using", "can", "will",
}


@dataclass
class Skill:
    name: str
    description: str
    body: str
    folder: str


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_fm, body = m.group(1), m.group(2)
    fm = {}
    for line in raw_fm.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip().lower()] = val.strip().strip('"').strip("'")
    return fm, body.strip()


def load_skills() -> List[Skill]:
    """Scans SKILLS_DIR for `*/SKILL.md` folders. A malformed skill is skipped
    with a warning rather than aborting the whole load."""
    skills: List[Skill] = []
    if not SKILLS_DIR.exists():
        return skills
    for folder in sorted(SKILLS_DIR.iterdir()):
        skill_file = folder / "SKILL.md"
        if not folder.is_dir() or not skill_file.exists():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(text)
            name = fm.get("name") or folder.name
            description = fm.get("description", "")
            if not description:
                logger.warning(f"Skipping skill '{folder.name}': SKILL.md has no description.")
                continue
            skills.append(Skill(name=name, description=description, body=body, folder=folder.name))
        except Exception as e:
            logger.warning(f"Skipping skill '{folder.name}': {e}")
    return skills


def _keywords(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def build_skills_metadata_block(skills: List[Skill]) -> str:
    """Level 1 — always-on, name+description only."""
    if not skills:
        return ""
    lines = ["AVAILABLE SKILLS (guidance you can draw on when relevant):"]
    for s in skills:
        lines.append(f"- {s.name}: {s.description}")
    return "\n".join(lines)


def match_skills(message: str, skills: List[Skill], max_matches: int = 2) -> List[Skill]:
    """Level 2 trigger — keyword overlap between the user's message and each
    skill's description. No LLM call, deterministic, cheap enough every turn."""
    msg_kw = _keywords(message)
    if not msg_kw:
        return []
    scored = []
    for s in skills:
        overlap = len(msg_kw & _keywords(s.description))
        if overlap > 0:
            scored.append((overlap, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:max_matches]]


def build_triggered_skills_block(matched: List[Skill]) -> str:
    """Level 2 body — full instructions for matched skills only."""
    if not matched:
        return ""
    parts = ["RELEVANT SKILL GUIDANCE — follow these instructions where they apply:"]
    for s in matched:
        parts.append(f"\n--- Skill: {s.name} ---\n{s.body}")
    return "\n".join(parts)
