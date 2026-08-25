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


class _CapturePost:
    def __init__(self, responder):
        self.responder = responder
        self.payloads = []

    def __call__(self, url, *, json=None, timeout=None):
        self.payloads.append(json)
        return _Response(self.responder(json))


def _valid_motion(index: int = 0) -> str:
    return (
        f"advances at a steady pace toward camera through beat {index + 1}, "
        "shifts weight through each step, then settles into a clean final hold"
    )


def _valid_scene() -> str:
    return (
        "Rain catches the practical lighting while wet stone reflects muted cyan "
        "and fabric wrinkles keep the texture tactile"
    )


def _valid_env() -> str:
    return (
        "Foreground rain drifts gently across stable wet stone, creating natural "
        "parallax without competing with the subject"
    )


def _project_for_planning():
    from diony_contracts.models import Brief

    return expand_idea(
        Brief(
            idea=(
                "A courier with a weathered canvas satchel delivers a letter that "
                "rewrites the city map during a rain-soaked blue hour."
            ),
            customer_kind=CustomerKind.FEATURE,
            target_minutes=0.5,
        )
    )


def test_brain_payload_is_wire_correct(monkeypatch) -> None:
    """Blueprint 04 sampling, wire-level reasoning_effort, sized token budget."""
    shot, bible = _shot()
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    capture = _CapturePost(lambda payload: "not json")
    monkeypatch.setattr(brain.httpx, "post", capture)

    brain.expand_on_site(shot, bible)

    assert len(capture.payloads) == 1
    payload = capture.payloads[0]
    assert payload["model"] == "DeepSeek-V4-Flash-0731"
    assert payload["temperature"] == 1.0
    assert payload["top_p"] == 0.95
    assert payload["reasoning_effort"] == "high"
    assert "extra_body" not in payload
    assert payload["max_tokens"] >= 1024


def test_plan_production_batches_all_shots_with_bible_and_critique(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    import json as json_module

    project = _project_for_planning()
    pending = list(project.shots)
    assert len(pending) == 10
    original_grammar = project.bible.visual_grammar

    def responder(payload):
        system = payload["messages"][0]["content"]
        user = payload["messages"][1]["content"]
        if system == brain.BIBLE_SYSTEM:
            return json_module.dumps(
                {
                    "logline": "A courier's letter quietly redraws the city he serves.",
                    "visual_grammar": (
                        "Long 35mm singles that move only when the courier decides to. "
                        "Sodium practicals against blue-hour rain; texture over gloss. "
                        "Cuts land on completed gestures, never on anticipation."
                    ),
                }
            )
        rows = json_module.loads(user[user.index("Shots: ") + len("Shots: ") :])
        if system == brain.CRITIC_SYSTEM:
            return json_module.dumps({"shots": [{"id": row["id"], "verdict": "pass"} for row in rows]})
        return json_module.dumps(
            {
                "shots": [
                    {
                        "id": row["id"],
                        "subject": row["subject"],
                        "motion": _valid_motion(index),
                        "scene": _valid_scene(),
                        "environment_dynamics": _valid_env(),
                    }
                    for index, row in enumerate(rows)
                ]
            }
        )

    activity: list[tuple[str, str]] = []
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(brain, "record_activity", lambda src, text, **kw: activity.append((src, text)))
    capture = _CapturePost(responder)
    monkeypatch.setattr(brain.httpx, "post", capture)

    brain.plan_production(project, pending)

    # 1 bible + ceil(10/8)=2 draft + 2 critique calls; never one call per shot.
    assert len(capture.payloads) == 5
    assert all("shifts weight" in shot.prompt_plan.motion for shot in pending)
    assert project.bible.visual_grammar != original_grammar
    assert "sodium practicals" in project.bible.visual_grammar.casefold()
    assert any("refined 10/10 shots in 2 batches" in text for _, text in activity)


def test_plan_production_subject_refinement_requires_every_anchor(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    import json as json_module

    project = _project_for_planning()
    pending = list(project.shots)
    keeper, dropper = pending[0], pending[1]
    keeper_subject = keeper.prompt_plan.subject + " under a dripping brass awning"

    def responder(payload):
        system = payload["messages"][0]["content"]
        user = payload["messages"][1]["content"]
        if system == brain.BIBLE_SYSTEM:
            return "not json"
        rows = json_module.loads(user[user.index("Shots: ") + len("Shots: ") :])
        if system == brain.CRITIC_SYSTEM:
            return json_module.dumps({"shots": [{"id": row["id"], "verdict": "pass"} for row in rows]})
        out = []
        for index, row in enumerate(rows):
            subject = row["subject"]
            if row["id"] == keeper.id:
                subject = keeper_subject
            elif row["id"] == dropper.id:
                subject = "A completely different actor with no anchors"
            out.append(
                {
                    "id": row["id"],
                    "subject": subject,
                    "motion": _valid_motion(index),
                    "scene": _valid_scene(),
                    "environment_dynamics": _valid_env(),
                }
            )
        return json_module.dumps({"shots": out})

    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(brain, "record_activity", lambda *args, **kw: None)
    monkeypatch.setattr(brain.httpx, "post", _CapturePost(responder))
    dropper_subject_before = dropper.prompt_plan.subject

    brain.plan_production(project, pending)

    assert keeper.prompt_plan.subject == keeper_subject
    assert dropper.prompt_plan.subject == dropper_subject_before
    assert "shifts weight" in dropper.prompt_plan.motion


def test_plan_production_critique_revision_is_applied_through_validators(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    import json as json_module

    project = _project_for_planning()
    pending = list(project.shots)
    revised_motion = (
        "circles slowly to frame left, weight rolling heel to toe, "
        "then holds as the shoulders settle"
    )

    def responder(payload):
        system = payload["messages"][0]["content"]
        user = payload["messages"][1]["content"]
        if system == brain.BIBLE_SYSTEM:
            return "not json"
        rows = json_module.loads(user[user.index("Shots: ") + len("Shots: ") :])
        if system == brain.CRITIC_SYSTEM:
            return json_module.dumps(
                {
                    "shots": [
                        {"id": rows[0]["id"], "verdict": "revise", "motion": revised_motion},
                        *[{"id": row["id"], "verdict": "pass"} for row in rows[1:]],
                    ]
                }
            )
        return json_module.dumps(
            {
                "shots": [
                    {
                        "id": row["id"],
                        "subject": row["subject"],
                        "motion": _valid_motion(index),
                        "scene": _valid_scene(),
                        "environment_dynamics": _valid_env(),
                    }
                    for index, row in enumerate(rows)
                ]
            }
        )

    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(brain, "record_activity", lambda *args, **kw: None)
    monkeypatch.setattr(brain.httpx, "post", _CapturePost(responder))

    brain.plan_production(project, pending)

    assert pending[0].prompt_plan.motion == revised_motion


def test_plan_production_offline_brain_is_visible_never_silent(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    project = _project_for_planning()
    pending = list(project.shots)
    activity: list[tuple[str, str, str]] = []
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: False)
    monkeypatch.setattr(
        brain,
        "record_activity",
        lambda src, text, level="info", **kw: activity.append((src, text, level)),
    )
    posts = _CapturePost(lambda payload: "")
    monkeypatch.setattr(brain.httpx, "post", posts)

    brain.plan_production(project, pending)

    assert posts.payloads == []
    assert any("brain is offline" in text and level == "warn" for _, text, level in activity)
    assert all(shot.prompt for shot in pending)


def _failed_take(project, shot):
    from diony_contracts.enums import QCVerdict
    from diony_contracts.models import QCReport, Take

    take = Take(id=f"{shot.id}-t1", shot_id=shot.id, attempt=1, engine=shot.engine, seed=shot.seed)
    take.qc = QCReport(
        id=f"qc-{take.id}",
        take_id=take.id,
        shot_id=shot.id,
        verdict=QCVerdict.FAIL,
        identity_ok=True,
        morph_ok=False,
        flicker_ok=False,
        duration_ok=True,
        legal_ok=True,
        temporal_residual_p95=0.31,
        flicker_luma_p95=0.19,
        notes=(
            "Measured temporal coherence failed (residual p95 0.3100). "
            "Measured flicker also above threshold."
        ),
    )
    project.takes = [take]
    return take


def test_direct_retake_applies_a_multi_symptom_directive(monkeypatch) -> None:
    import json as json_module

    project = _project_for_planning()
    shot = project.shots[0]
    take = _failed_take(project, shot)
    directive = {
        "motion": _valid_motion(3),
        "scene": _valid_scene() + " under steady practicals",
        "add_negative_terms": ["exposure pumping", "melting geometry"],
        "rationale": "Coherence and flicker both failed; stabilised light and grounded motion.",
    }

    def responder(payload):
        assert payload["messages"][0]["content"] == brain.DIRECTOR_SYSTEM
        user = payload["messages"][1]["content"]
        assert "temporal_residual_p95" in user
        assert "0.31" in user
        return json_module.dumps(directive)

    activity = []
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(brain, "record_activity", lambda src, text, **kw: activity.append(text))
    monkeypatch.setattr(brain.httpx, "post", _CapturePost(responder))
    camera_before = shot.prompt_plan.camera

    assert brain.direct_retake(project, shot, take) is True

    assert shot.prompt_plan.motion == directive["motion"]
    assert "steady practicals" in shot.prompt_plan.scene
    assert "exposure pumping" in shot.negative_prompt
    assert "melting geometry" in shot.negative_prompt
    assert shot.prompt_plan.camera == camera_before
    assert any("retake directive applied" in text for text in activity)


def test_direct_retake_falls_back_on_invalid_direction(monkeypatch) -> None:
    import json as json_module

    project = _project_for_planning()
    shot = project.shots[0]
    take = _failed_take(project, shot)
    plan_before = shot.prompt_plan.model_copy(deep=True)
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(brain, "record_activity", lambda *args, **kw: None)
    # Motion without pace/direction/mechanics cues fails the validators.
    monkeypatch.setattr(
        brain.httpx,
        "post",
        _CapturePost(lambda payload: json_module.dumps({"motion": "dancing beautifully"})),
    )

    assert brain.direct_retake(project, shot, take) is False
    assert shot.prompt_plan == plan_before


def test_plan_production_dry_run_desktop_is_quiet(monkeypatch) -> None:
    """The packaged desktop dry-run must not log brain warnings on every Produce."""
    monkeypatch.setenv("DIONY_DRY_RUN", "1")
    project = _project_for_planning()
    pending = list(project.shots)
    activity = []
    posts = _CapturePost(lambda payload: "")
    monkeypatch.setattr(brain, "record_activity", lambda *a, **kw: activity.append(a))
    monkeypatch.setattr(brain.httpx, "post", posts)

    brain.plan_production(project, pending)

    assert posts.payloads == []
    assert activity == []
    assert all(shot.prompt for shot in pending)


def test_plan_production_budget_keeps_the_roll_responsive(monkeypatch) -> None:
    monkeypatch.delenv("DIONY_DRY_RUN", raising=False)
    monkeypatch.setenv("DIONY_BRAIN_PLANNING_BUDGET", "30")
    project = _project_for_planning()
    pending = list(project.shots)
    clock = iter([0.0] + [1000.0] * 50)
    activity = []
    monkeypatch.setattr(brain, "inference_live", lambda: True)
    monkeypatch.setattr(brain, "brain_up", lambda: True)
    monkeypatch.setattr(brain.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        brain,
        "record_activity",
        lambda src, text, level="info", **kw: activity.append((text, level)),
    )
    monkeypatch.setattr(brain.httpx, "post", _CapturePost(lambda payload: "not json"))

    brain.plan_production(project, pending)

    assert any("planning budget" in text and level == "warn" for text, level in activity)
    assert any("refined 0/" in text for text, _ in activity)
    assert all(shot.prompt for shot in pending)
