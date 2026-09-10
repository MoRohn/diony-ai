"""The Producer console is the Gallery of Director films plus Studio governance."""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "diony_producer" / "static"


def test_the_legacy_bays_are_not_shipped() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()

    for legacy in (
        'data-bay="idea"',
        'id="bay-idea"',
        'id="bay-production"',
        'id="site-line"',
        'id="start-over"',
        'id="join-workspace"',
        'id="director-films"',
        'id="production-stop-dialog"',
    ):
        assert legacy not in html, legacy
    for endpoint in (
        "/api/project",
        "/api/run",
        "/api/studio/roll",
        "/api/gallery/joins",
        "/api/farm",
        "/api/site",
        "/api/catalog",
        "/api/gates/",
    ):
        assert endpoint not in js, endpoint
    assert 'data-active-bay="gallery"' in html


def test_the_gallery_is_the_finished_clips_inventory() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'id="bay-gallery"' in html
    assert ">Finished clips<" in html
    assert 'id="gallery-grid"' in html and 'id="gallery-empty"' in html
    assert 'id="gallery-select-mode"' in html and 'id="gallery-selection-count"' in html
    for action in ("join-selected", "download-selected", "delete-selected"):
        assert f'id="{action}"' in html, action
    assert 'id="gallery-viewer"' in html and 'id="gallery-viewer-video"' in html
    assert 'id="gallery-details"' in html and 'id="gallery-copy-feedback"' in html
    assert "No clips yet." in html
    assert 'fetch("/api/gallery"' in js
    assert '"/api/gallery/stitch"' in js
    assert "item.remove_url" in js and "item.details_url" in js and "item.thumbnail_url" in js
    assert "function renderGallery" in js and "function openGalleryViewer" in js
    assert "function renderGalleryDetails" in js and "function copyGalleryPrompt" in js
    assert "function armConfirm" in js and "function askInline" in js
    assert "window.confirm" not in js and "window.prompt" not in js
    assert 'fetch("/api/engine")' in js
    assert "director-films" not in html


def test_the_static_stamp_is_pinned() -> None:
    html = (STATIC / "index.html").read_text()
    assert 'href="/static/app.css?v=p7"' in html
    assert 'src="/static/app.js?v=p7"' in html


def test_producer_uses_the_diony_chorus_design_system() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    assert "family=Archivo:wdth,wght@125,850" in html
    assert "family=Inter" in html
    assert 'data-product-surface="producer"' in html
    assert 'class="diony-spirit' in html
    assert 'class="brand diony-brand"' in html
    assert 'class="diony-mark"' in html
    assert '<span class="diony-word">Diony</span>' in html
    assert 'fill="currentColor"' in html
    assert "diony-ai-optical-mark" not in html
    assert 'src="/static/dionysus-sketch.png?v=run31"' in html
    assert 'href="http://director.diony.local" id="director-link"' in html
    assert 'id="studio-surface-link"' in html
    sketch = (STATIC / "dionysus-sketch.png").read_bytes()
    assert sketch.startswith(b"\x89PNG\r\n\x1a\n")
    assert "--charcoal: #0d0d0d" in css.lower()
    assert "--light: #f6f6f6" in css.lower()
    assert "--signal: #ff402f" in css.lower()
    assert "--brand-gradient" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css
    mast_brand_start = css.index(".brand.diony-brand {")
    mast_brand_rule = css[mast_brand_start : css.index(".diony-mark", mast_brand_start)]
    assert "background: transparent;" in mast_brand_rule
    assert "overflow: visible;" in mast_brand_rule
    assert "clip-path" not in mast_brand_rule
    # the Team button is never hidden: governance is a feature, not a legacy bay
    assert "#studio-control,\n" not in css
    assert "body.cloud-engine" not in css


def test_studio_invitation_uses_a_distinct_resilient_primary_action() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'class="word invite-send invite-send-action"' in html
    assert "data-invite-action-label>Send invitation</strong>" in html
    assert 'class="invite-send-arrow" aria-hidden="true">→</span>' in html
    assert ".invite-send-action {" in css
    assert ".invite-send-action:hover:not(:disabled)" in css
    assert ".invite-send-action:focus-visible" in css
    assert ".invite-send-action:disabled" in css
    assert 'submit.querySelector("[data-invite-action-label]")' in js
    assert 'submit.textContent = "Sending…"' not in js


def test_producer_contains_complete_studio_invitation_and_accept_surfaces() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()
    assert 'id="studio-control"' in html
    assert 'id="studio-drawer"' in html
    assert 'id="invite-maker"' in html
    assert 'id="invite-email"' in html
    assert 'id="invite-preview"' in html
    assert "Send invitation" in html
    assert 'id="cost-center-form"' in html
    assert 'id="cost-center-name"' in html
    assert 'id="cost-center-member"' in html
    assert 'id="cost-center-list"' in html
    assert "Create &amp; assign Cost Center" in html
    assert "member-admin-only" in html
    assert "cost-admin-only" in html
    assert 'id="join-studio"' in html
    assert 'class="join-brand"' in html
    assert 'href="http://studio.diony.local"' in html
    assert "wizard" + ".diony.local" not in html
    assert "The Studio checks this invitation. Diony cloud does not." in html
    assert ".join-brand .diony-mark" in css
    assert "WHAT DIONY AI IS" in html
    assert "cloud video engines" in html
    assert "Accept invitation" in html
    assert "Enter Producer" in html
    assert "/api/v1/studio/invitations" in js
    assert "/api/v1/cost-centers" in js
    assert "/cost-centers`" in js
    assert "assigned_cost_center_ids" in js
    assert "governanceCanManageCosts" in js
    assert "/api/studio-entry/discover" in js
    assert "/api/studio-entry/verification/request" in js
    assert "/api/studio-entry/join" in js
