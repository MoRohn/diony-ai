"""Character sheets: priced still ops through the Flow image lane, registered into the bible."""

from diony_contracts.director import CloudGenerationMode, DirectorOpType
from diony_director import artifacts, packagebuilder, screenwriter


def _package():
    draft = screenwriter.write_script(
        "a getaway driver decides to leave town from a neon diner", runtime_target_s=60
    )
    return packagebuilder.build_package("s-art", "a getaway driver", draft)


def test_sheet_ops_cover_the_angle_board_per_character() -> None:
    package = _package()
    ops = artifacts.plan_sheet_ops("s-art", package)
    per_char = {}
    for op in ops:
        bible = op.artifact_refs["bible"]
        per_char.setdefault(bible["char_id"], []).append(bible["slot"])
    assert set(per_char) == {c.char_id for c in package.characters}
    for slots in per_char.values():
        assert slots == list(artifacts.SHEET_SLOTS)


def test_sheet_ops_are_priced_image_ops_with_bible_prompts() -> None:
    package = _package()
    ops = artifacts.plan_sheet_ops("s-art", package)
    for op in ops:
        assert op.op is DirectorOpType.REIMAGE
        assert op.mode is CloudGenerationMode.IMAGE
        assert op.model == "nano-banana-2"
        assert op.estimate is not None and op.estimate.total_usd_micros > 0
        char = package.character(op.artifact_refs["bible"]["char_id"])
        assert char.name in op.prompt
        assert "photograph" in op.prompt or "portrait" in op.prompt


def test_registering_a_still_updates_the_bible_and_ref_packs(tmp_path) -> None:
    package = _package()
    ops = artifacts.plan_sheet_ops("s-art", package)
    front = next(o for o in ops if o.artifact_refs["bible"]["slot"] == "front")
    still = tmp_path / "front.png"
    still.write_bytes(b"png" * 400)
    artifacts.register_still(
        package, front, path=still, media_generation_id="user:img-front"
    )
    char = package.character(front.artifact_refs["bible"]["char_id"])
    assert char.hero_stills["front"] == str(still)
    assert str(still) in char.grok_ref_pack
    assert char.flow_media_ids["front"] == "user:img-front"


def test_later_slots_chain_earlier_stills_as_references(tmp_path) -> None:
    package = _package()
    ops = artifacts.plan_sheet_ops("s-art", package)
    char_id = package.characters[0].char_id
    front = next(
        o for o in ops
        if o.artifact_refs["bible"] == {"char_id": char_id, "slot": "front"}
    )
    still = tmp_path / "front.png"
    still.write_bytes(b"png" * 400)
    artifacts.register_still(package, front, path=still, media_generation_id="user:img-front")

    profile_op = next(
        o for o in ops
        if o.artifact_refs["bible"] == {"char_id": char_id, "slot": "profile"}
    )
    refs = artifacts.references_for(package, profile_op)
    assert "user:img-front" in refs
