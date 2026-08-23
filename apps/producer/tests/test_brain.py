import json

from diony_contracts.enums import CustomerKind
from diony_contracts.models import Brief
from diony_contracts.studio import expand_idea
from diony_producer import brain


class _Response:
    def __init__(self, content: str):
        self._content = content
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _shot():
    project = expand_idea(
        Brief(
            idea=(
                "A courier with a weathered canvas satchel delivers a letter that "
                "rewrites the city map during a rain-soaked blue hour."
            ),
            customer_kind=CustomerKind.FEATURE,
            target_minutes=0.5,
        )
    )
    return project.shots[0], project.bible


def test_on_site_refinement_can_enrich_motion_and_scene_without_rewriting_locks(monkeypatch) -> None:
    shot, bible = _shot()
    assert shot.prompt_plan is not None
    original_subject = shot.prompt_plan.subject
    original_camera = shot.prompt_plan.camera
    response = json.dumps(
        {
            "subject": "A completely different actor",
            "motion": (
                "moves steadily from left to right, shifts weight through each step, "
                "then settles into a clean final hold"
            ),
            "camera": "drone shot that crashes into an extreme close-up",
            "scene": (
                "Rain catches the blue-hour practical lighting, wet stone reflects muted "
                "cyan, and fabric wrinkles keep the image tactile"
            ),
            "environment_dynamics": (
                "Foreground rain drifts gently across stable wet stone while distant practicals "
                "hold their position and create natural parallax"
            ),
        }
    )
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(brain.httpx, "post", lambda *args, **kwargs: _Response(response))

    refined = brain.expand_on_site(shot, bible)

    assert refined == shot.prompt
    assert shot.prompt_plan.subject == original_subject
    assert shot.prompt_plan.camera == original_camera
    assert "shifts weight" in shot.prompt_plan.motion
    assert "wet stone" in shot.prompt_plan.scene
    assert 80 <= shot.prompt_plan.word_count <= 140


def test_on_site_refinement_rejects_unstructured_or_weak_output(monkeypatch) -> None:
    shot, bible = _shot()
    original_prompt = shot.prompt
    original_plan = shot.prompt_plan.model_copy(deep=True)
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(
        brain.httpx,
        "post",
        lambda *args, **kwargs: _Response(
            json.dumps(
                {
                    "subject": "someone else",
                    "motion": "dancing",
                    "camera": "static and zooming",
                    "scene": "somewhere",
                }
            )
        ),
    )

    refined = brain.expand_on_site(shot, bible)

    assert refined == original_prompt
    assert shot.prompt_plan == original_plan


def test_on_site_refinement_falls_back_to_the_compiled_contract_on_bad_json(monkeypatch) -> None:
    shot, bible = _shot()
    original_prompt = shot.prompt
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(
        brain.httpx,
        "post",
        lambda *args, **kwargs: _Response("Here is a beautiful cinematic prompt."),
    )

    assert brain.expand_on_site(shot, bible) == original_prompt
    assert shot.prompt == original_prompt
