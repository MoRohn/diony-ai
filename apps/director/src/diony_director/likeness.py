"""A real person is described, never named — the likeness guard.

2026-09-05: the user's idea named nobody; the reference still was a truck
poster, and the brain, which SEES the still, wrote the actor's name into the
master prompt and every one of the 26 clip prompts ("Create a video of
<actor> locked in a full split…", "<actor>'s face"). Google Flow's
prominent-people filter refuses such a prompt before render (Blueprint 30's
``PROMINENT_PERSON``), Grok's likeness policy is no kinder, and a name is not
a visual instruction anyway — the generator needs the build, the sunglasses,
the vest, the pose.

Three layers, one law:

1. The brain is TOLD (master template and screenwriter alike): never write a
   real, identifiable person's name, nickname, or identifying title, whether
   it came from the idea or was recognized in a still; describe them as a
   character instead; and RETURN who it recognized (``real_people``) so the
   system can hold it to that.
2. The system ENFORCES: every listed person's name, surname, nickname, and
   possessive is replaced by that person's descriptor in every text field of
   the artifact — deterministic, word-bounded, case-aware. A speaker label
   or character name becomes the person's short label.
3. The system AUDITS: capitalized phrases the brain did not list are put to
   the brain once more, text only ("which of these name a real person?"), so
   a prose slip the brain did not confess is still caught. Best effort: an
   audit that fails is a warning, never a failed derivation.

Only people are touched. Brands (Volvo FM), places, on-screen titles
(PRECISION), and the user's own invented characters (Mara, ELIAS) are left
exactly as written — the brain decides who is real, the code never guesses.

2026-09-07, the fourth layer — NAMES BESIDE A PICTURE (s-9c2f04d993): a
satirical campaign ad with an entirely fictional candidate, "Grant
Merriweather", and an AI-drawn poster of him as the reference still. Nobody
real anywhere; real_people was rightly empty. Google Flow still refused
clip 1 three times with PROMINENT_PERSON. Five live probes on omni-flash
isolated it: the full prompt WITHOUT the poster rendered; the poster with a
neutral prompt rendered; the prompt naming him WITH the poster was refused;
the same prompt with "the candidate" in place of the name, with the poster,
rendered; and a spoken line saying his name beside the poster rendered too.
So Google's filter fires whenever the PROSE names a person — real or
invented — next to a reference picture of a face; the name alone and the
picture alone are fine, and quoted speech is not read as a name. The brain
therefore also confesses ``characters`` (every named person it wrote, same
shape), and the last gate before a Flow submit (``scrub_pictured``) describes
every person, real or invented, in a prompt that rides beside a picture —
outside quoted speech, which stays verbatim. A Flow prompt with no picture
keeps its character names.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

# Surname particles: "Van Damme", "De Niro", "von Trier", "Del Toro" — the
# particle rides with the surname, so the surname-alone alias is the whole
# group, never a bare "Damme".
_PARTICLES = {
    "van",
    "von",
    "de",
    "di",
    "da",
    "del",
    "della",
    "der",
    "den",
    "du",
    "la",
    "le",
    "los",
    "las",
    "dos",
    "das",
    "bin",
    "ibn",
    "al",
    "el",
    "st",
    "st.",
    "saint",
    "mc",
    "mac",
    "o",
    "ter",
    "ten",
    "af",
    "av",
}
_ARTICLES = ("the ", "a ", "an ")
_PREPOSITIONS = {
    "in",
    "with",
    "of",
    "at",
    "on",
    "wearing",
    "who",
    "whose",
    "from",
    "under",
    "behind",
}

# The contract text both brains receive — one law, written once.
BRAIN_LAW = (
    "REAL PEOPLE — LIKENESS LAW\n"
    "Never write the name, nickname, initials, or identifying title of a real, "
    "identifiable person (actor, athlete, musician, politician, presenter, any "
    "public figure, living or dead) anywhere in your output — not from the "
    "idea, and NOT from a reference image you recognize someone in. Never "
    "write 'looks like <person>' or 'the star of <film>'. Describe that person "
    "as a character instead: build, apparent age, hair, skin, wardrobe, pose, "
    "expression — what the camera sees. Invented characters and the user's own "
    "fictional names are fine. Brands, places, products, and on-screen titles "
    "are not people. Return real_people: a list, one entry per real person you "
    "recognized in the idea or the images, {name (who — the full name as commonly "
    "written), aliases (list: nicknames or other forms you might have used), "
    "descriptor (the definite noun phrase you wrote INSTEAD of the name, ≤ 10 "
    "words, e.g. 'the broad-shouldered man in sunglasses and a leather vest'), "
    "label (a 1–3 word character label, e.g. 'The Splitter')}; an empty list "
    "when none. The system verifies: any name that still appears is replaced "
    "by its descriptor.\n"
    "NAMED CHARACTERS\n"
    "Also return characters: a list, one entry per invented or fictional person "
    "you named anywhere in your output (from the idea or your own invention), "
    "in the same shape {name, aliases, descriptor, label}, where descriptor is "
    "the definite visual noun phrase that can replace the name in any sentence "
    "(e.g. 'the silver-haired candidate in the pale blue Oxford'); an empty list "
    "when you named no one. Video providers refuse a prompt that names ANY "
    "person beside a reference picture, so the system replaces those names with "
    "their descriptors when a picture rides along."
)

# One entry of real_people, normalized.
Person = dict[str, Any]


def audit_enabled() -> bool:
    return os.environ.get("DIONY_LIKENESS_AUDIT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


# --------------------------------------------------------------------------
# People and their aliases
# --------------------------------------------------------------------------


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split()).strip(" .;,")


def _surname_group(tokens: list[str]) -> str:
    """'Jean-Claude Van Damme' → 'Van Damme'; 'Dwayne Johnson' → 'Johnson'."""
    if not tokens:
        return ""
    end = len(tokens) - 1
    start = end
    while start > 0 and tokens[start - 1].lower().strip(".") in _PARTICLES:
        start -= 1
    return " ".join(tokens[start : end + 1])


def default_label(descriptor: str) -> str:
    """'the broad-shouldered man in sunglasses' → 'The Broad-Shouldered Man':
    the head phrase before the first preposition, at most three words."""
    text = _clean(descriptor)
    lowered = text.lower()
    for article in _ARTICLES:
        if lowered.startswith(article):
            text = text[len(article) :]
            break
    head: list[str] = []
    for word in text.split():
        if word.lower() in _PREPOSITIONS or len(head) == 3:
            break
        head.append(word)
    if not head:
        return "The Person"
    return "The " + " ".join(
        "-".join(part[:1].upper() + part[1:] for part in w.split("-")) for w in head
    )


def normalize_people(raw: Any, *, has_reference: bool = False) -> list[Person]:
    """The brain's real_people (or an audit reply), as the guard uses it:
    name, every alias the code can derive, a descriptor that never contains
    the name, and a label. Entries without a name are dropped."""
    people: list[Person] = []
    if isinstance(raw, dict):
        raw = raw.get("people") or raw.get("real_people") or []
    if isinstance(raw, str):
        raw = [{"name": raw}]
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            continue
        name = _clean(entry.get("name"))
        if not name or len(name) < 2:
            continue
        descriptor = _clean(entry.get("descriptor"))
        if descriptor and descriptor[:1].isupper() and descriptor[1:2].islower():
            descriptor = descriptor[:1].lower() + descriptor[1:]
        aliases_in = entry.get("aliases") or []
        if isinstance(aliases_in, str):
            aliases_in = re.split(r"[;,\n]", aliases_in)
        aliases = [_clean(a) for a in aliases_in if _clean(a)]
        person: Person = {
            "name": name,
            "aliases": aliases,
            "descriptor": descriptor,
            "label": _clean(entry.get("label")),
        }
        person["aliases"] = _all_aliases(person)
        if not descriptor or _pattern(person["aliases"]).search(descriptor):
            person["descriptor"] = (
                "the person from the reference image" if has_reference else "the person"
            )
        if not person["label"] or _pattern(person["aliases"]).search(person["label"]):
            person["label"] = default_label(person["descriptor"])
        # the same person listed twice (master + screenwriter, or twice by
        # one brain) is one entry with the union of aliases
        for known in people:
            if known["name"].lower() == name.lower() or _pattern(known["aliases"]).fullmatch(name):
                for alias in person["aliases"]:
                    if alias not in known["aliases"]:
                        known["aliases"].append(alias)
                break
        else:
            people.append(person)
    return people


def _all_aliases(person: Person) -> list[str]:
    """Full name, surname group, a distinctive first name, and every alias the
    brain gave — longest first so the widest match wins."""
    name = person["name"]
    out: list[str] = [name]
    tokens = [t for t in re.split(r"\s+", name) if t]
    if len(tokens) >= 2:
        surname = _surname_group(tokens)
        if surname and surname != name:
            out.append(surname)
        first = tokens[0]
        # "Jean-Claude" and "Dwayne" stand alone; "Will" does not — it is a
        # verb, and the guard must never touch a sentence it was not aimed at
        if "-" in first or len(first.strip(".")) >= 5:
            out.append(first)
    for alias in person.get("aliases") or []:
        if alias and alias not in out:
            out.append(alias)
    seen: set[str] = set()
    unique: list[str] = []
    for alias in sorted(out, key=lambda a: (-len(a), a)):
        key = alias.lower()
        if key not in seen:
            seen.add(key)
            unique.append(alias)
    return unique


def _alias_regex(alias: str) -> str:
    tokens = [re.escape(t) for t in re.split(r"[\s\-]+", alias) if t]
    if len(tokens) >= 2:
        # a multi-token name is distinctive: any case, any joiner
        return r"(?i:" + r"[\s\-]+".join(tokens) + r")"
    # a single token matches as written, or in the ALL CAPS of a cue line
    token = tokens[0] if tokens else re.escape(alias)
    upper = re.escape(alias.upper())
    return r"(?:" + token + r"|" + upper + r")"


def _pattern(aliases: list[str]) -> re.Pattern[str]:
    if not aliases:
        return re.compile(r"(?!x)x")
    alternatives = "|".join(_alias_regex(a) for a in aliases)
    return re.compile(r"(?<![\w'’-])(?P<name>" + alternatives + r")(?P<poss>['’]s)?(?![\w-])")


def _at_sentence_start(text: str, index: int) -> bool:
    before = text[:index].rstrip()
    if not before:
        return True
    # a colon or semicolon opens a list item, not a sentence
    return before[-1] in '.!?"“(\n'


# --------------------------------------------------------------------------
# Scrub
# --------------------------------------------------------------------------


def scrub(text: str, people: list[Person], *, sentences: bool = True) -> tuple[str, int]:
    """Every mention of every person → that person's descriptor; the count of
    replacements made. Possessives keep their 's; in running prose
    (`sentences`) a mention that opens a sentence is capitalized — a fragment
    (an anchor, a subject, a constraint) is joined into someone else's
    sentence and stays lowercase; an ALL CAPS mention (a cue line) becomes
    the person's label in caps. Idempotent: descriptors never match."""
    if not text or not people:
        return text, 0
    hits = 0
    out = text
    for person in people:
        pattern = _pattern(person.get("aliases") or [person["name"]])
        descriptor = person.get("descriptor") or "the person"
        label = person.get("label") or default_label(descriptor)

        def _sub(match: re.Match[str]) -> str:
            nonlocal hits
            hits += 1
            mention = match.group("name")
            if mention.isupper() and len(mention) > 1:
                replacement = label.upper()
            elif sentences and _at_sentence_start(match.string, match.start()):
                replacement = descriptor[:1].upper() + descriptor[1:]
            else:
                replacement = descriptor
            if match.group("poss"):
                replacement += "'" if replacement.endswith("s") else "'s"
            return replacement

        out = pattern.sub(_sub, out)
    return out, hits


def scrub_label(text: str, people: list[Person]) -> tuple[str, bool]:
    """A character name or speaker: when it IS a real person (or contains
    one), it becomes the person's label — 'The Splitter', never a sentence."""
    if not text or not people:
        return text, False
    for person in people:
        pattern = _pattern(person.get("aliases") or [person["name"]])
        if pattern.search(text):
            label = person.get("label") or default_label(person.get("descriptor") or "")
            return (label.upper() if text.isupper() else label), True
    return text, False


# Quoted speech: a spoken line that says a name beside a picture rendered
# on the live probe (2026-09-07); it is the user's own words and stays.
_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”')


def scrub_pictured(text: str, people: list[Person]) -> tuple[str, int]:
    """The pictured-prompt guard: every person — real or an invented
    character — described, never named, in the prose of a prompt that rides
    beside a reference picture. Quoted speech is left verbatim."""
    if not text or not people:
        return text, 0
    hits = 0
    out: list[str] = []
    cursor = 0
    for match in _QUOTED.finditer(text):
        prose, n = scrub(text[cursor : match.start()], people)
        out.append(prose)
        out.append(match.group(0))
        hits += n
        cursor = match.end()
    prose, n = scrub(text[cursor:], people)
    out.append(prose)
    hits += n
    return "".join(out), hits


def pictured_note(hits: int, people: list[Person]) -> str:
    """The rationale line the card shows — descriptors, never names."""
    described = "; ".join(p.get("descriptor") or p.get("label") or "a person" for p in people)
    return (
        f"names beside a picture: Google Flow refuses a prompt that names anyone next to a "
        f"reference picture, so {hits} mention{'s' if hits != 1 else ''} became the "
        f"description ({described}); spoken lines stay verbatim"
    )


def mentions(text: str, people: list[Person]) -> bool:
    return any(_pattern(p.get("aliases") or [p["name"]]).search(text or "") for p in people)


# --------------------------------------------------------------------------
# Candidates and the audit
# --------------------------------------------------------------------------

_CAP_TOKEN = r"[A-Z][a-zA-Z'’]+(?:-[A-Za-z][a-zA-Z'’]+)*"
_CANDIDATE = re.compile(
    r"(?<![\w'’-])"
    + _CAP_TOKEN
    + r"(?:\s+(?:(?:van|von|de|di|da|del|della|der|du|la|le|mc|mac|st\.?)\s+)?"
    + _CAP_TOKEN
    + r")*"
)
# Words a prompt capitalizes mid-sentence for reasons of its own.
_NOT_A_NAME = {
    "I",
    "AI",
    "Constraints",
    "Lighting",
    "Camera",
    "Then",
    "At",
    "In",
    "The",
    "Use",
    "Preserve",
    "Maintain",
    "Pace",
    "Create",
    "Keep",
    "Avoid",
    "Do",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
}


def candidates(
    texts: Iterable[str], people: list[Person] | None = None, *, cap: int = 40
) -> list[str]:
    """Capitalized phrases that could name someone and are not yet covered by
    a known person. Multi-word phrases anywhere; a single capitalized word only
    mid-sentence (every sentence starts with one). ALL CAPS words are on-screen
    titles, never candidates."""
    known = people or []
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in _CANDIDATE.finditer(text):
            phrase = match.group(0).strip()
            words = phrase.split()
            if phrase.isupper() or phrase in _NOT_A_NAME:
                continue
            if len(words) == 1:
                if _at_sentence_start(text, match.start()) or len(phrase) < 3:
                    continue
            if mentions(phrase, known):
                continue
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(phrase)
            if len(found) >= cap:
                return found
    return found


AUDIT_SYSTEM = (
    "LIKENESS AUDIT\n"
    "You are a compliance reader for AI video prompts. The prompt below must "
    "not name any real, identifiable person. Decide which CANDIDATE PHRASES "
    "name, nickname, or unmistakably identify a real person (actor, athlete, "
    "musician, politician, presenter, any public figure, living or dead). "
    "Invented character names, brands, vehicles, places, products, and "
    "on-screen titles are NOT people. Return ONLY a JSON object "
    '{"people": [...]} with one entry per real person: {"name": the full name '
    'as commonly written, "aliases": every candidate phrase and nickname that '
    'refers to them, "descriptor": a definite noun phrase of at most 10 words '
    "describing them as they appear in THIS prompt — build, apparent age, hair, "
    "wardrobe — usable in place of the name in any sentence and never hinting "
    'at who they are, "label": a 1–3 word character label}, and "characters": '
    "one entry in the same shape per candidate phrase that names an INVENTED "
    "or fictional person (a character of the story, not a real one). "
    '{"people": [], "characters": []} when no candidate names anyone.'
)


def audit(prompt_text: str, phrases: list[str], *, has_reference: bool = False) -> list[Person]:
    """Ask the brain, text only, which candidate phrases name a real person.
    Raises on transport or contract failure — the caller turns that into a
    warning."""
    return audit_all(prompt_text, phrases, has_reference=has_reference)[0]


def audit_all(
    prompt_text: str, phrases: list[str], *, has_reference: bool = False
) -> tuple[list[Person], list[Person]]:
    """One text-only brain call: (real people, invented characters) among the
    candidate phrases. A phrase the brain lists under both is a real person."""
    from diony_director import screenwriter
    from diony_director.providers import base as provider_base

    user = (
        "PROMPT TEXT:\n"
        + " ".join(prompt_text.split())[:6000]
        + "\n\nCANDIDATE PHRASES:\n- "
        + "\n- ".join(phrases)
    )
    response = provider_base.post_with_retries(
        screenwriter.chat_completions_url(),
        what="likeness audit",
        json={
            "model": screenwriter.brain_model(),
            "messages": [
                {"role": "system", "content": AUDIT_SYSTEM},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        },
        headers=screenwriter._brain_headers(),
        timeout=screenwriter.brain_timeout_s(),
    )
    if response.status_code >= 400:
        raise screenwriter.brain_rejection(response, "likeness audit")
    body = screenwriter._json_object(response.json()["choices"][0]["message"]["content"])
    people = normalize_people(body.get("people") or [], has_reference=has_reference)
    # a phrase that CONTAINS a confirmed person is that person too:
    # "Jean-Claude Van Damme" when the audit answered "Van Damme"
    for person in people:
        for phrase in phrases:
            if mentions(phrase, [person]) and phrase not in person["aliases"]:
                person["aliases"].append(phrase)
        person["aliases"] = _all_aliases(person)
    characters = without(
        normalize_people(body.get("characters") or [], has_reference=has_reference), people
    )
    return people, characters


def without(characters: list[Person], people: list[Person]) -> list[Person]:
    """The characters that are not (and do not contain) a real person — a
    real person cast under a character's name is the real person's entry."""
    return [c for c in characters if not mentions(c["name"], people)]


def people_from_profiles(profiles: Iterable[Any]) -> list[Person]:
    """A screenplay's cast, as the pictured-prompt guard needs it: each
    CharacterProfile's name → its role as the descriptor (the visual head
    of its age/body notes when the role is empty or names the character)."""
    raw: list[dict] = []
    for profile in profiles or []:
        name = _clean(getattr(profile, "name", ""))
        if not name:
            continue
        descriptor = _clean(getattr(profile, "role", ""))
        notes = _clean(getattr(profile, "age_body_notes", ""))
        if not descriptor or name.lower() in descriptor.lower():
            descriptor = notes.split(";")[0].split(",")[0] if notes else ""
        if descriptor:
            # a definite phrase, whatever the profile wrote: "a weathered
            # farmer" → "the weathered farmer"
            lowered = descriptor.lower()
            for article in _ARTICLES:
                if lowered.startswith(article):
                    descriptor = descriptor[len(article) :]
                    break
            descriptor = "the " + descriptor[:1].lower() + descriptor[1:]
        raw.append({"name": name, "descriptor": descriptor})
    return normalize_people(raw)


def merge(*groups: Iterable[Person] | None) -> list[Person]:
    """The union of several people lists, one entry per person."""
    raw: list[dict] = []
    for group in groups:
        for person in group or []:
            raw.append(dict(person))
    return normalize_people(raw)


# --------------------------------------------------------------------------
# Enforcement on the artifacts
# --------------------------------------------------------------------------

# Running prose: a mention opening a sentence is capitalized.
_MASTER_PROSE_FIELDS = ("narration", "prompt")
# Fragments the assembler joins into its own sentences: never capitalized.
_MASTER_FRAGMENT_FIELDS = (
    "primary_subject",
    "primary_action",
    "setting",
    "spatial",
    "camera",
    "performance",
    "micro_expressions",
    "physics",
    "body_mechanics",
    "mood",
    "ending",
)
_MASTER_TEXT_FIELDS = _MASTER_PROSE_FIELDS + _MASTER_FRAGMENT_FIELDS
_MASTER_LIST_FIELDS = ("continuity_anchors", "constraints")
_MASTER_DICT_FIELDS = ("scene_depth", "lighting")
_CLIP_FIELDS = ("prompt", "opening", "narration")  # all sentences
_LOOK_FIELDS = ("medium", "render_language", "palette", "lens_grade", "lighting_system")


def enforce_master(master: Any, people: list[Person]) -> int:
    """Every text field of a MasterPrompt (beats, depth, clips, look included)
    → described, never named. Records the people on the artifact; returns
    the number of mentions replaced."""
    if not people:
        return 0
    hits = 0
    for field in _MASTER_TEXT_FIELDS:
        value, n = scrub(
            getattr(master, field, "") or "", people, sentences=field in _MASTER_PROSE_FIELDS
        )
        if n:
            setattr(master, field, value)
            hits += n
    for field in _MASTER_LIST_FIELDS:
        items = list(getattr(master, field, []) or [])
        changed = False
        for i, item in enumerate(items):
            value, n = scrub(str(item), people, sentences=False)
            if n:
                items[i] = value
                changed = True
                hits += n
        if changed:
            setattr(master, field, items)
    for field in _MASTER_DICT_FIELDS:
        mapping = dict(getattr(master, field, {}) or {})
        changed = False
        for key, item in mapping.items():
            value, n = scrub(str(item), people, sentences=False)
            if n:
                mapping[key] = value
                changed = True
                hits += n
        if changed:
            setattr(master, field, mapping)
    for beat in master.beats or []:
        value, n = scrub(str(beat.get("text", "")), people, sentences=False)
        if n:
            beat["text"] = value
            hits += n
    for clip in master.clips or []:
        for field in _CLIP_FIELDS:
            value, n = scrub(str(clip.get(field, "") or ""), people)
            if n:
                clip[field] = value
                hits += n
    look = getattr(master, "look", None)
    if look is not None:
        for field in _LOOK_FIELDS:
            value, n = scrub(getattr(look, field, "") or "", people, sentences=False)
            if n:
                setattr(look, field, value)
                hits += n
        negatives = list(look.negatives or [])
        changed = False
        for i, item in enumerate(negatives):
            value, n = scrub(str(item), people, sentences=False)
            if n:
                negatives[i] = value
                changed = True
                hits += n
        if changed:
            look.negatives = negatives
    master.real_people = [dict(p) for p in people]
    master.word_count = len((master.prompt or "").split())
    return hits


def master_texts(master: Any) -> list[str]:
    """Every text the audit reads — the fields a generator or a screenwriter
    will see."""
    texts = [getattr(master, f, "") or "" for f in _MASTER_TEXT_FIELDS]
    texts += [str(x) for f in _MASTER_LIST_FIELDS for x in (getattr(master, f, []) or [])]
    texts += [str(v) for f in _MASTER_DICT_FIELDS for v in (getattr(master, f, {}) or {}).values()]
    texts += [str(b.get("text", "")) for b in (master.beats or [])]
    texts += [str(c.get(f, "") or "") for c in (master.clips or []) for f in _CLIP_FIELDS]
    return [t for t in texts if t]


def enforce_script_body(body: dict, people: list[Person]) -> int:
    """The screenwriter's reply, before it becomes a Script: a character whose
    name is a real person is renamed to the label (and every speaker with
    it); every other text field is described, never named."""
    if not people or not isinstance(body, dict):
        return 0
    hits = 0
    renamed: dict[str, str] = {}
    for char in body.get("characters") or []:
        if not isinstance(char, dict):
            continue
        name = str(char.get("name", "") or "")
        label, changed = scrub_label(name, people)
        if changed:
            renamed[name] = label
            renamed[name.upper()] = label.upper()
            char["name"] = label
            hits += 1
        for key in ("role", "age_body_notes", "wardrobe", "casting_card"):
            if isinstance(char.get(key), str):
                value, n = scrub(char[key], people, sentences=False)
                if n:
                    char[key] = value
                    hits += n
            elif isinstance(char.get(key), dict):
                for sub, item in list(char[key].items()):
                    value, n = scrub(str(item), people, sentences=False)
                    if n:
                        char[key][sub] = value
                        hits += n
    for key in ("logline",):
        if isinstance(body.get(key), str):
            value, n = scrub(body[key], people)
            if n:
                body[key] = value
                hits += n
    for beat in body.get("beats") or []:
        if isinstance(beat, dict) and isinstance(beat.get("beat"), str):
            value, n = scrub(beat["beat"], people, sentences=False)
            if n:
                beat["beat"] = value
                hits += n
    for setting in body.get("settings") or []:
        if not isinstance(setting, dict):
            continue
        for key in ("name", "room_tone", "time_weather", "grade_language", "camera_kit"):
            if isinstance(setting.get(key), str):
                value, n = scrub(setting[key], people, sentences=False)
                if n:
                    setting[key] = value
                    hits += n
    for scene in body.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for key in ("heading", "emotional_job", "story_time"):
            if isinstance(scene.get(key), str):
                value, n = scrub(scene[key], people, sentences=False)
                if n:
                    scene[key] = value
                    hits += n
        for line in scene.get("lines") or []:
            if not isinstance(line, dict):
                continue
            speaker = str(line.get("speaker", "") or "")
            if speaker:
                if speaker in renamed:
                    line["speaker"] = renamed[speaker]
                    hits += 1
                else:
                    label, changed = scrub_label(speaker, people)
                    if changed:
                        line["speaker"] = label
                        hits += 1
            for key in ("text", "shot"):
                if isinstance(line.get(key), str):
                    value, n = scrub(line[key], people)
                    if n:
                        line[key] = value
                        hits += n
    return hits


def script_texts(body: dict) -> list[str]:
    texts: list[str] = []
    if not isinstance(body, dict):
        return texts
    texts.append(str(body.get("logline", "") or ""))
    for char in body.get("characters") or []:
        if isinstance(char, dict):
            texts.append(str(char.get("name", "") or ""))
            texts.append(str(char.get("age_body_notes", "") or ""))
            wardrobe = char.get("wardrobe")
            texts.append(
                wardrobe
                if isinstance(wardrobe, str)
                else " ".join(str(v) for v in (wardrobe or {}).values())
            )
    for scene in body.get("scenes") or []:
        if isinstance(scene, dict):
            texts.append(str(scene.get("heading", "") or ""))
            for line in scene.get("lines") or []:
                if isinstance(line, dict):
                    texts.append(str(line.get("speaker", "") or ""))
                    texts.append(str(line.get("text", "") or ""))
                    texts.append(str(line.get("shot", "") or ""))
    return [t for t in texts if t]


def note(hits: int, people: list[Person], *, owner: str, where: str) -> str:
    """The warning the UI shows — the descriptor, never the name."""
    described = "; ".join(p.get("descriptor") or p.get("label") or "a person" for p in people)
    return (
        f"{owner}: likeness guard — {len(people)} real person{'s' if len(people) != 1 else ''} "
        f"recognized in the {where}, described and never named ({described}); "
        f"{hits} mention{'s' if hits != 1 else ''} replaced"
    )


__all__ = [
    "AUDIT_SYSTEM",
    "BRAIN_LAW",
    "Person",
    "audit",
    "audit_all",
    "audit_enabled",
    "candidates",
    "default_label",
    "enforce_master",
    "enforce_script_body",
    "master_texts",
    "mentions",
    "merge",
    "normalize_people",
    "note",
    "people_from_profiles",
    "pictured_note",
    "script_texts",
    "scrub",
    "scrub_label",
    "scrub_pictured",
    "without",
]
