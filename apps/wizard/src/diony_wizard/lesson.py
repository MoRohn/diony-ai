"""Shared Wizard lesson types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Field:
    id: str
    label: str
    kind: str  # text, select, check, quiz, choice
    options: list[str] = field(default_factory=list)
    placeholder: str = ""
    accept: list[str] = field(default_factory=list)
    when: str = ""  # if set, only required when site/path matches
    hint: str = ""
    option_help: dict[str, str] = field(default_factory=dict)
    default: str = ""
    auto: bool = False  # recorded on Continue; not shown as a quiz

    def as_public(self) -> dict:
        data = asdict(self)
        data.pop("accept", None)
        return data


@dataclass
class Lesson:
    step_id: str
    why: str
    teach: list[str]
    fail_if: list[str]
    fields: list[Field]
    commands: list[str] = field(default_factory=list)
    diagram: str = ""
    handoff: list[str] = field(default_factory=list)

    def as_public(self) -> dict:
        data = asdict(self)
        for item in data["fields"]:
            item.pop("accept", None)
        return data


def field_applies(field: Field, site: str) -> bool:
    if not field.when:
        return True
    allowed = {part.strip() for part in field.when.split(",") if part.strip()}
    return site in allowed


def normalize(value: str) -> str:
    return " ".join(value.strip().lower().replace("×", "x").split())
