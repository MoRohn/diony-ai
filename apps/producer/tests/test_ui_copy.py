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
        'id="studio-control"',
        'id="studio-drawer"',
        'id="join-studio"',
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
    assert "function armConfirm" in js
    assert "window.confirm" not in js and "window.prompt" not in js
    assert 'fetch("/api/engine")' in js
    assert "director-films" not in html


def test_the_static_stamp_is_pinned() -> None:
    html = (STATIC / "index.html").read_text()
    assert 'href="/static/app.css?v=p9"' in html
    assert 'src="/static/app.js?v=p9"' in html


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
    assert "body.cloud-engine" not in css


def test_the_team_governance_surface_is_gone() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    assert "Team" not in html.split("<header", 1)[1].split("</header>", 1)[0]
    for endpoint in (
        "/api/v1/studio",
        "/api/studio-entry",
        "/api/v1/cost-centers",
        "/api/v1/members",
    ):
        assert endpoint not in js, endpoint
    assert "askInline" not in js and "loadStudioGovernance" not in js
