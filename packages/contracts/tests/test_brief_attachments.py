from diony_contracts.enums import CustomerKind
from diony_contracts.models import Brief


def test_brief_requires_idea_or_will_require_attachments() -> None:
    try:
        Brief(idea="short", customer_kind=CustomerKind.FEATURE)
        raise AssertionError("expected failure")
    except Exception:
        pass


def test_brief_allows_short_idea_when_attachments_present() -> None:
    brief = Brief(
        idea="look",
        customer_kind=CustomerKind.FEATURE,
        attachment_ids=["asset-still-01"],
    )
    assert brief.attachment_ids == ["asset-still-01"]
