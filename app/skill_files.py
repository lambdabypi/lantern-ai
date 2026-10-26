"""Lantern's skills: each capability is a reusable skill with its own SKILL.md, and the user turns skills on or off.

Each app/skills/<id>/SKILL.md has agentskills.io-style front matter (`name`, `description`) plus `metadata`: a title,
the agent tools it owns, what it uses, what it sends out of the app, and whether it can be turned off. The body says
how the skill works and the rules it keeps, for people and for agents reading the repo.

Turning a skill off (profile.md `skills_off`, from the Skills page) really turns it off for that user: its agent
tools answer that it's off, its endpoints refuse, its buttons are hidden and its reminders stop. The recall watch
itself is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import yaml

from app.memory.store import MemoryStore

SKILLS_DIR = Path(__file__).parent / "skills"


class SkillOff(RuntimeError):
    """A turned-off skill was used. The message is safe to show to the user."""


@dataclass(frozen=True)
class Skill:
    id: str
    title: str
    description: str
    tools: tuple[str, ...] = ()
    uses: tuple[str, ...] = ()
    sends: str = ""
    required: bool = False
    blurb: str = ""         # what it does, for people ("Checks something secondhand for recalls before you buy it.")
    order: int = 99
    text: str = field(default="", repr=False)   # the whole SKILL.md

    def public(self, on: bool) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "description": self.blurb or self.description, "uses": list(self.uses),
                "sends": self.sends, "required": self.required, "on": on}


def load_skills(folder: Path = SKILLS_DIR) -> dict[str, Skill]:
    skills = {}
    for path in sorted(folder.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        _, front, _ = text.split("---", 2)
        meta = yaml.safe_load(front) or {}
        extra = meta.get("metadata") or {}
        if meta.get("name") != path.parent.name:
            raise ValueError(f"{path}: name must match its folder")
        skills[meta["name"]] = Skill(
            id=meta["name"], title=str(extra.get("title") or meta["name"]), description=str(meta.get("description") or ""),
            tools=tuple(extra.get("tools") or ()), uses=tuple(extra.get("uses") or ()), sends=str(extra.get("sends") or ""),
            required=bool(extra.get("required")), blurb=str(extra.get("blurb") or ""), order=int(extra.get("order") or 99),
            text=text)
    return dict(sorted(skills.items(), key=lambda kv: kv[1].order))


class Skills:
    def __init__(self, store: MemoryStore, skills: dict[str, Skill] | None = None):
        self.store = store
        self.all = skills if skills is not None else load_skills()
        self.by_tool = {t: s.id for s in self.all.values() for t in s.tools}

    def off(self, user_id: str) -> set[str]:
        prof = self.store.get(user_id, "profile")
        return {s for s in ((prof.meta.get("skills_off") if prof else None) or []) if s in self.all
                and not self.all[s].required}

    def on(self, user_id: str, skill_id: str) -> bool:
        return skill_id not in self.off(user_id)

    def require(self, user_id: str, skill_id: str) -> None:
        if not self.on(user_id, skill_id):
            raise SkillOff(f"\"{self.all[skill_id].title}\" is turned off. Turn it on in Skills (in the menu).")

    def set(self, user_id: str, skill_id: str, on: bool) -> list[dict[str, Any]]:
        if skill_id not in self.all:
            raise KeyError(skill_id)
        if self.all[skill_id].required and not on:
            raise SkillOff(f"\"{self.all[skill_id].title}\" is what Lantern is for, so it can't be turned off.")
        off = self.off(user_id)
        off = off - {skill_id} if on else off | {skill_id}
        self.store.upsert(user_id, "profile", None, {"skills_off": sorted(off)}, source="user")
        return self.listing(user_id)

    def listing(self, user_id: str) -> list[dict[str, Any]]:
        off = self.off(user_id)
        return [s.public(s.id not in off) for s in self.all.values()]

    def guard_tool(self, tool: Any) -> Any:
        """The same agent tool, answering "turned off" for users who turned its skill off."""
        skill_id = self.by_tool.get(tool.name)
        if skill_id is None:
            return tool
        handler: Callable = tool.handler

        def guarded(args: dict[str, Any], ctx: Any) -> Any:
            try:
                self.require(ctx.user_id, skill_id)
            except SkillOff as e:
                return {"error": f"The user turned this skill off. {e} Tell them; don't do it another way."}
            return handler(args, ctx)

        return replace(tool, handler=guarded)
