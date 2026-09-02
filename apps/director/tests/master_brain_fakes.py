"""Blueprint 27 test helpers — a stand-alone module (NOT conftest: in multi-
package pytest runs `from conftest import` resolves to whichever package's
conftest loaded first). The director tests dir is on sys.path."""

from __future__ import annotations


def master_brain_body(**overrides) -> dict:
    """A valid master-prompt reply, as the brain returns it under the
    MASTER AI VIDEO PROMPT TEMPLATE. Tests whose fake brain serves a
    screenplay must ALSO serve this when the system prompt is the master
    template — the real brain answers both stages."""
    body = {
        "visual_style": "cinematic realism",
        "primary_subject": "a lighthouse keeper in a salt-stained peacoat",
        "primary_action": "crosses the lamp room and opens the iron door",
        "setting": "a storm-lit lighthouse lamp room at night",
        "beats": [
            {"stage": "initial_state", "text": "the keeper stands at the lamp, back to the door"},
            {"stage": "development", "text": "three knocks; he turns and crosses the room"},
            {"stage": "payoff", "text": "he hauls the iron door open into the storm"},
            {"stage": "final_state", "text": "he stands in the doorway, rain blowing past him"},
        ],
        "scene_depth": {
            "background": "the great lamp turning, storm beyond the glass",
            "middle_ground": "the iron door and a coiled rope",
            "foreground": "the keeper, centered",
        },
        "spatial": "keeper moves left to right toward the door",
        "camera": "holds a locked wide frame at chest height",
        "performance": "shoulders set, a held breath before the door",
        "micro_expressions": "narrowed eyes",
        "physics": "peacoat and rain driven by the wind through the open door",
        "body_mechanics": "weight shifts forward before each step",
        "lighting": {"source": "the turning lamp", "quality": "hard, sweeping", "shadows": "long rotating shadows"},
        "mood": "tense",
        "continuity_anchors": ["same peacoat", "same lamp room", "door on the right"],
        "ending": "the keeper in the open doorway, rain blowing past him",
        "constraints": ["do not introduce additional people", "do not change the wardrobe", "avoid distorted anatomy"],
        "estimated_duration_s": 8,
        "prompt": (
            "Create a cinematic realism video of a lighthouse keeper in a salt-stained "
            "peacoat crossing the lamp room and opening the iron door in a storm-lit "
            "lighthouse at night. The scene begins with the keeper at the lamp, back to "
            "the door. At first, three knocks. Then he turns and crosses the room. At the "
            "main payoff, he hauls the iron door open into the storm. The camera holds a "
            "locked wide frame at chest height. Lighting comes from the turning lamp, "
            "producing hard sweeping light and long rotating shadows. Maintain strict "
            "continuity throughout: same peacoat, same lamp room, door on the right "
            "remain unchanged. The clip ends with the keeper in the open doorway, rain "
            "blowing past him. Constraints: do not introduce additional people; do not "
            "change the wardrobe; avoid distorted anatomy."
        ),
    }
    body.update(overrides)
    return body


def is_master_call(json_payload: dict | None) -> bool:
    """Whether a chat-completions payload is the master-prompt stage."""
    try:
        return str(json_payload["messages"][0]["content"]).startswith("MASTER AI VIDEO PROMPT TEMPLATE")
    except (KeyError, IndexError, TypeError):
        return False


def fast_master_derive(idea, **kwargs):
    """A stand-in for master_prompt.derive that never touches a brain —
    for tests that isolate the screenplay stage."""
    from diony_director import master_prompt as master_mod

    return master_mod.optimize(
        master_mod._template(
            idea,
            int(kwargs.get("target_duration_s", 60)),
            bool(kwargs.get("reference_images")),
            kwargs.get("authored"),
        )
    )
