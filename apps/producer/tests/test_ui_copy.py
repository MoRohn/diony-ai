from pathlib import Path
import re

STATIC = Path(__file__).resolve().parents[1] / "src" / "diony_producer" / "static"


def test_edit_page_is_not_shipped() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'data-bay="edit"' not in html
    assert 'id="bay-edit"' not in html
    assert "/api/takes/${takeId}/edit" not in js
    assert "showBay(\"edit\")" not in js
    assert "#bay-edit" not in css
    assert 'body[data-active-bay="edit"]' not in css
    assert "ROLL / CUT / FINISH" not in css


def test_producer_floors_are_idea_production_gallery() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()
    assert html.count('data-bay="') == 3
    assert 'data-bay="bible"' not in html
    assert 'data-bay="rundown"' not in html
    assert 'data-bay="dailies"' not in html
    assert 'data-bay="lock"' not in html
    assert 'data-bay="production"' in html
    assert 'data-bay="edit"' not in html
    assert 'data-bay="gallery"' in html
    assert 'id="gallery-grid"' in html
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    assert "Newsreader" not in html
    assert "Newsreader" not in css
    assert "Inter" in css
    assert "family=Inter" in html
    assert 'class="sheet"' in html
    assert "Write the picture" in html
    assert "Produce" in html
    assert 'class="clap"' in html
    assert 'id="cast"' in html
    assert 'id="site-line"' in html
    assert 'href="/static/app.css?v=wanvoice1"' in html
    assert 'src="/static/app.js?v=wanvoice1"' in html
    assert "v=run30" not in html
    assert "Unsigned" not in html
    assert "Unsigned" not in js
    assert 'id="run-refresh"' in html
    assert 'id="start-over"' in html
    assert "/api/studio/new" in js
    assert "Start over cannot interrupt an active production. Use STOP" in js
    refresh_body = js[js.index("function refreshProductionPage") : js.index("async function startOverStudio")]
    assert "/api/studio/new" not in refresh_body
    assert "window.location.replace" in refresh_body
    assert "bay=production" in refresh_body
    assert '["production", "gallery"].includes(requestedBay)' in js
    assert 'fetch("/api/run"' in js
    assert "const productionActive = Boolean(" in js
    assert 'showBay(requested || (productionActive ? "production" : "idea"))' in js
    assert 'getElementById("run-refresh")?.addEventListener("click", refreshProductionPage)' in js
    assert 'getElementById("start-over")?.addEventListener("click", startOverStudio)' in js
    assert "function refreshProductionPage" in js
    assert "res.status === 409" in js
    assert "dropped tunnel" in js
    assert "function stopRunWatch" in js
    assert 'id="run-log"' in html
    assert "<pre id=\"run-log\"" not in html
    assert "paintActivity" in js
    assert 'id="run-clock"' in html
    assert "paintClock" in js
    assert "pollClock" in js
    assert 'id="run-clips"' in html
    assert "clip.generation_lane_label" in js
    assert "clip.generation_fallback" in js
    assert "clip.effective_denoise_steps" in js
    assert 'id="run-clip-summary"' in html
    assert 'id="run-signal"' in html
    assert 'id="run-signal-title"' in html
    assert 'id="run-signal-progress"' in html
    assert 'id="run-signal-metrics"' in html
    assert 'id="run-log" class="run-log" aria-label="Run activity" aria-live="off"' in html
    assert "function paintSignal" in js
    assert "function resetProductionViewport" in js
    assert "productionFloor.scrollTop = 0" in js
    assert 'className = "clip-milestones"' in js
    assert 'className = "activity-evidence activity-raw-message"' in js
    assert "const previousScrollTop = logEl.scrollTop" in js
    assert "logEl.scrollTop = logEl.scrollHeight" not in js
    assert ".run-signal" in css
    assert ".clip-milestones" in css
    assert ".activity-evidence" in css
    assert 'id="run-beat"' in html
    assert 'id="master-standard"' in html
    assert 'id="master-standard-status"' in html
    assert 'id="master-standard-blockers"' in html
    assert "1920×1080" in html
    assert "function renderProductionStandard" in js
    assert "run.production_standard" in js
    assert ".master-standard" in css
    dock = html[html.index('class="idea-dock"') : html.index('id="ref-files"')]
    assert 'id="cast"' in dock
    assert 'id="bible-sheet"' not in html
    assert "save-bible" not in html
    assert 'id="allotment"' in html
    assert "Not a stopwatch" not in html
    assert 'id="run-now"' in html
    assert "No clips yet" in html
    assert 'id="run-steps"' in html
    assert 'id="reel"' not in html
    assert "Edit this picture" not in html
    assert "allotment-picks" in js
    assert "closest(\"button[data-minutes]\")" in js
    assert "btn.disabled = written" not in js
    assert "calc(100vw" in css
    assert "calc(100vh - 4.6rem)" in css
    assert "targetMinutes" in js
    assert "/api/finish" not in js
    assert "pollFinish" in js
    assert "/api/run" in js
    assert "/api/farm" in js
    assert "pollFarm" in js
    assert "pollRun" in js
    assert "failed" in js
    assert "/api/gallery" in js
    assert "border: 2px dashed" not in css
    assert "sprockets" not in html
    assert "/api/studio/open" in js
    assert "/api/studio/roll" in js
    assert 'showBay("production")' in js
    assert 'showBay("dailies")' not in js
    make_at = js.index("async function makeStudio")
    production_in_make = js.find('showBay("production")', make_at)
    roll_in_make = js.find("/api/studio/roll", make_at)
    farm_in_make = js.find("/api/farm", make_at)
    assert 0 < production_in_make < farm_in_make < roll_in_make
    assert "lock-picture" not in html
    assert "lock-mix" not in html
    assert "lock-distribution" in html
    assert "Picture lock" not in html
    assert 'id="join-selected"' in html
    assert 'id="gallery-selection"' in html
    assert 'id="join-workspace"' in html
    assert 'id="join-sequence"' in html
    assert 'id="join-junctions"' in html
    assert 'id="join-preview"' in html
    assert "Create new version" in html
    assert "function toggleGallerySelection" in js
    assert "function renderJoinWorkspace" in js
    assert "function previewJoin" in js
    assert "function createJoinedVersion" in js
    assert "Revise version" in js
    assert "parent_composition_id: original.id" in js
    assert "/api/gallery/joins/plan" in js
    assert "/api/gallery/joins/preview" in js
    assert "/api/gallery/joins" in js
    assert ".join-workspace" in css
    assert ".splice-rail" in css
    assert ".join-junction" in css


def test_producer_actions_share_the_studio_capsule_language() -> None:
    css = (STATIC / "app.css").read_text()

    assert "--action-pill-radius: 999px;" in css
    for selector in (
        ".word",
        ".bays button",
        ".allotment-picks button",
        ".clap-slate",
        ".sample-action.word",
        ".invite-send",
        ".invite-send-action",
        ".run-stop-action",
        ".run-refresh-action",
    ):
        rules = re.findall(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
        assert any("border-radius: var(--action-pill-radius);" in rule for rule in rules)

    stop_start = css.rindex(".production-stop-choice {")
    stop_rule = css[stop_start : css.index("}", stop_start)]
    assert "border-radius: var(--action-card-radius);" in stop_rule

    gallery_start = css.rindex(".gallery-card-actions button,")
    gallery_rule = css[gallery_start : css.index("}", gallery_start)]
    assert "border-radius: var(--action-pill-radius);" in gallery_rule


def test_idea_length_controls_have_breathing_room_and_team_header_label() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()

    assert '<button type="button" id="studio-control" class="word studio-control">Team</button>' in html
    assert '>Studio</button>' not in html

    pick_rules = re.findall(r"\.allotment-picks\s*\{([^}]*)\}", css)
    assert any("margin-top: 0.65rem;" in rule for rule in pick_rules)


def test_selected_header_page_is_emphasized_without_a_capsule() -> None:
    css = (STATIC / "app.css").read_text()

    active_start = css.rindex(".bays button.on {")
    active_rule = css[active_start : css.index("}", active_start)]
    assert "border-color: transparent;" in active_rule
    assert "background: transparent;" in active_rule
    assert "font-weight: 700;" in active_rule
    assert "box-shadow: none;" in active_rule

    underline_start = css.rindex(".bays button.on::after {")
    underline_rule = css[underline_start : css.index("}", underline_start)]
    assert "height: 3px;" in underline_rule
    assert "background: var(--signal);" in underline_rule


def test_production_monitor_scales_to_the_window_with_quiet_scrollbars() -> None:
    css = (STATIC / "app.css").read_text()

    assert "--production-scrollbar-size: 2px;" in css
    assert 'body[data-active-bay="production"] .sheet' in css
    assert "grid-template-columns: minmax(0, 1.2fr) minmax(20rem, 0.8fr);" in css
    assert "#bay-production::-webkit-scrollbar" in css
    assert ".run-clips::-webkit-scrollbar" in css
    assert ".run-log::-webkit-scrollbar" in css
    assert "width: var(--production-scrollbar-size);" in css
    assert "--production-scrollbar-rest: rgba(255, 255, 255, 0.08);" in css
    assert "::-webkit-scrollbar-thumb:active" in css
    assert "overflow: hidden auto;" in css
    assert "overflow-wrap: anywhere;" in css


def test_production_stop_modal_offers_keep_or_delete_completed_clips() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    production = html[html.index('id="bay-production"') : html.index('id="bay-gallery"')]
    assert 'id="run-stop"' in production
    assert ">STOP<" in production
    assert 'id="production-stop-dialog"' in html
    assert 'data-stop-disposition="keep"' in html
    assert 'data-stop-disposition="delete"' in html
    assert ">Keep<" in html
    assert ">Delete<" in html
    assert "completed clips" in html.lower()
    assert 'id="run-incomplete-result"' in production
    assert "/api/production/stop" in js
    assert "function openProductionStop" in js
    assert "function submitProductionStop" in js
    assert ".production-stop-dialog" in css
    assert ".run-stop-action" in css
    hidden_start = css.rindex(".run-stop-action[hidden]")
    hidden_rule = css[hidden_start : css.index("}", hidden_start)]
    assert "display: none;" in hidden_rule


def test_production_folio_fits_the_viewport_and_clears_the_last_pipeline_row() -> None:
    css = (STATIC / "app.css").read_text()

    body_selector = 'body[data-active-bay="production"] {'
    body_start = css.index(body_selector)
    body_rule = css[body_start : css.index("}", body_start)]
    assert "display: flex;" in body_rule
    assert "flex-direction: column;" in body_rule

    stage_selector = 'body[data-active-bay="production"] .studio {'
    stage_start = css.index(stage_selector)
    stage_rule = css[stage_start : css.index("}", stage_start)]
    assert "flex: 1 1 auto;" in stage_rule
    assert "height: auto;" in stage_rule
    assert "min-height: 0;" in stage_rule
    assert "env(safe-area-inset-bottom)" in stage_rule

    sheet_selector = 'body[data-active-bay="production"] .sheet {'
    sheet_start = css.index(sheet_selector)
    sheet_rule = css[sheet_start : css.index("}", sheet_start)]
    assert "height: 100%;" in sheet_rule
    assert "max-height: 100%;" in sheet_rule

    production_start = css.rindex("#bay-production {")
    production_rule = css[production_start : css.index("}", production_start)]
    assert "--production-end-clearance:" in production_rule
    assert "scroll-padding-bottom: var(--production-end-clearance);" in production_rule

    assert "padding-bottom: var(--production-end-clearance);" in production_rule


def test_production_shots_fill_the_remaining_desktop_height_and_scroll_inside_it() -> None:
    css = (STATIC / "app.css").read_text()

    desktop_start = css.index("@media (min-width: 1181px) {")
    desktop_rule = css[desktop_start : css.index("@media (max-width: 1180px)", desktop_start)]

    assert "#bay-production .run-workspace {" in desktop_rule
    assert "flex: 1 1 0;" in desktop_rule
    assert "min-height: 0;" in desktop_rule
    assert "align-items: stretch;" in desktop_rule

    assert "#bay-production .run-primary {" in desktop_rule
    assert "height: 100%;" in desktop_rule
    assert "flex-direction: column;" in desktop_rule

    assert "#bay-production .run-col-clips {" in desktop_rule
    assert "flex: 1 1 0;" in desktop_rule

    assert "#bay-production .run-clips {" in desktop_rule
    assert "max-height: none;" in desktop_rule
    assert "overflow: hidden auto;" in desktop_rule
    assert "grid-auto-rows: max-content;" in desktop_rule
    assert "align-content: start;" in desktop_rule
    assert "padding-bottom: var(--production-shots-bottom-clearance);" in desktop_rule
    assert "scroll-padding-bottom: var(--production-shots-bottom-clearance);" in desktop_rule


def test_gallery_is_a_thumbnail_grid_with_complete_clip_actions() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'id="gallery-viewer"' in html
    assert 'id="gallery-viewer-video"' in html
    assert 'id="gallery-viewer-fullscreen"' in html
    assert 'id="gallery-viewer-download"' in html
    assert 'id="gallery-details"' in html
    assert 'id="gallery-details-body"' in html
    assert "item.thumbnail_url" in js
    assert "item.download_url" in js
    assert "item.details_url" in js
    assert 'method: "DELETE"' in js
    assert "requestFullscreen" in js
    assert "function openGalleryDetails" in js
    assert "navigator.clipboard.writeText" in js
    assert "gallery-details-icon" in js
    assert "Watch" in js
    assert "Full screen" in js
    assert "Details" in js
    assert "Download" in js
    assert "Delete" in js
    assert ".gallery-card-actions" in css
    assert ".gallery-viewer" in css
    assert ".gallery-details-dialog" in css
    assert ".gallery-source-slate" in css
    viewer_shell_start = css.index(".gallery-viewer-shell {")
    viewer_shell_rule = css[viewer_shell_start : css.index("}", viewer_shell_start)]
    viewer_video_start = css.index(".gallery-viewer-frame video {")
    viewer_video_rule = css[viewer_video_start : css.index("}", viewer_video_start)]
    assert "height: min(52rem, calc(100vh - 2rem));" in viewer_shell_rule
    assert "height: 100%;" in viewer_video_rule
    assert "aspect-ratio:" not in viewer_video_rule


def test_gallery_extends_to_the_viewport_and_keeps_prompt_copy_with_actions() -> None:
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'body[data-active-bay="gallery"] {' in css
    assert 'body[data-active-bay="gallery"] .studio {' in css
    assert 'body[data-active-bay="gallery"] .sheet {' in css
    gallery_start = css.rindex("#bay-gallery {")
    gallery_rule = css[gallery_start : css.index("}", gallery_start)]
    assert "--gallery-scrollbar-size: 3px;" in gallery_rule
    assert "overflow: hidden;" in gallery_rule
    grid_start = css.rindex(".gallery-grid {")
    grid_rule = css[grid_start : css.index("}", grid_start)]
    assert "grid-auto-rows: max-content;" in grid_rule
    assert "overflow: hidden auto;" in grid_rule
    assert "scrollbar-gutter: stable;" in grid_rule
    assert ".gallery-grid::-webkit-scrollbar" in css
    assert ".gallery-grid::-webkit-scrollbar-thumb:hover" in css

    assert 'setTextNode(copy, "span", "Prompt", "gallery-prompt-label");' in js
    assert 'item.prompt || item.title' in js
    assert '"gallery-prompt-description"' in js
    assert ".gallery-prompt-label" in css
    assert ".gallery-prompt-description" in css
    assert "card.appendChild(actions);" in js


def test_gallery_can_be_opened_by_a_fresh_cache_busted_url() -> None:
    js = (STATIC / "app.js").read_text()

    assert 'new URLSearchParams(location.search).get("bay")' in js
    assert '["production", "gallery"].includes(requestedBay)' in js


def test_gallery_library_remains_available_when_the_active_project_is_blank() -> None:
    js = (STATIC / "app.js").read_text()

    assert 'if (name === "gallery") btn.disabled = false;' in js
    assert 'if (openGallery) openGallery.disabled = false;' in js
    assert 'if (name === "gallery" && !galleryReady())' not in js
    assert "function galleryReady()" not in js
    assert 'if (name === "gallery") loadGallery();' in js
    assert 'meta.textContent = "Loading finished clips…";' in js
    assert 'note.textContent = `Gallery could not be loaded: ${error.message}`;' in js


def test_gallery_prompt_copy_uses_one_icon_and_cross_platform_clipboard_fallbacks() -> None:
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    css = (STATIC / "app.css").read_text()

    assert 'id="gallery-copy-feedback" class="gallery-copy-feedback" role="status" aria-live="polite"' in html
    assert "function galleryCopyIcon()" in js
    assert 'copy.setAttribute("aria-label", "Copy prompt");' in js
    assert 'copy.setAttribute("title", "Copy prompt");' in js
    assert "copy.appendChild(galleryCopyIcon());" in js
    assert js.count("copy.appendChild(galleryCopyIcon());") == 1
    assert 'copy.textContent = "Copy prompt";' not in js
    assert 'button.textContent = "Copied";' not in js
    assert 'button.classList.add("is-copied")' in js
    assert 'promptField.className = "gallery-idea-prompt";' in js
    assert "promptField.select();" in js
    assert "promptField.setSelectionRange(0, promptField.value.length);" in js
    copy_handler = js[js.index("async function copyGalleryPrompt") : js.index("function renderGalleryDetails")]
    selected_copy = copy_handler.index('document.execCommand("copy")')
    clipboard_fallback = copy_handler.index("await navigator.clipboard.writeText(prompt)")
    assert selected_copy < clipboard_fallback
    assert 'feedback.textContent = "Prompt copied.";' in copy_handler
    assert "Command-C on Mac or Ctrl-C on Windows" in copy_handler
    assert ".gallery-copy-prompt svg" in css
    assert ".gallery-copy-prompt.is-copied" in css
    assert ".gallery-copy-feedback" in css


def test_production_header_and_idea_body_explain_the_working_state() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'id="site-line" class="site-line" role="status"' in html
    assert "function renderSiteStatus" in js
    assert 'label: "Site"' in js
    assert 'label: "Engine plan"' in js
    assert 'label: "Handoff"' in js
    assert 'label: "Camera"' in js
    assert 'value: "Drain → unload → load"' in js
    assert 'className = "site-status-label"' in js
    assert 'className = "site-status-value"' in js
    assert ".site-status-item" in css
    assert 'body[data-active-bay="production"] .site-status-value' in css
    assert ".site-status-item.is-live .site-status-value::before" in css

    assert 'class="reference-hint-label">Reference files</span>' in html
    assert "Attach images or files, or drag them anywhere onto this window." in html
    assert ".reference-hint" in css
    assert ".reference-hint-label" in css
    assert 'stageEl.addEventListener("drop"' in js
    assert 'href="/static/app.css?v=wanvoice1"' in html
    assert 'src="/static/app.js?v=wanvoice1"' in html
    assert "v=run30" not in html


def test_idea_page_uses_the_open_space_for_a_larger_higher_character_sketch() -> None:
    css = (STATIC / "app.css").read_text()

    selector = 'body[data-active-bay="idea"] .spirit-producer {'
    start = css.index(selector)
    rule = css[start : css.index("}", start)]
    assert "top: clamp(" in rule
    assert "bottom: auto;" in rule
    assert "width: clamp(21rem, 29vw, 30rem);" in rule
    assert "opacity: 0.18;" in rule
    assert "pointer-events: none;" in rule


def test_producer_uses_the_diony_chorus_design_system() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()
    assert "family=Archivo:wdth,wght@125,850" in html
    assert "family=Inter" in html
    assert 'data-product-surface="producer"' in html
    assert 'class="diony-spirit' in html
    assert 'class="brand diony-brand"' in html
    assert 'class="diony-mark"' in html
    assert '<span class="diony-word">Diony</span>' in html
    assert 'fill="currentColor"' in html
    assert "diony-ai-optical-mark" not in html
    assert '<i>/</i><span>Diony</span><strong>AI</strong>' not in html
    assert 'src="/static/dionysus-sketch.png?v=run31"' in html
    assert "function triggerBayTransition" in js
    assert "producer-scene-transition" in js
    assert "producer-scene-transition" in css
    assert "producer-projector-cue" in css
    assert "producer-scene-in" not in css
    sketch = (STATIC / "dionysus-sketch.png").read_bytes()
    assert sketch.startswith(b"\x89PNG\r\n\x1a\n")
    assert sketch[25] in {4, 6}
    assert "Attach images or files, or drag them anywhere onto this window." in html
    assert "A still, a clip, or a page can land here." not in html
    assert "A goal for the finished show. Not a stopwatch." not in html
    assert "ideaEl.focus();" not in js
    assert "--charcoal: #0d0d0d" in css.lower()
    assert "--light: #f6f6f6" in css.lower()
    assert "--signal: #ff402f" in css.lower()
    assert "--ember: #ffa23a" in css.lower()
    assert "--flare: #ff7a2f" in css.lower()
    assert "--garnet: #e43b4b" in css.lower()
    assert "--brand-gradient" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css
    mast_brand_start = css.index(".brand.diony-brand {")
    mast_brand_rule = css[mast_brand_start : css.index(".diony-mark", mast_brand_start)]
    assert "background: transparent;" in mast_brand_rule
    assert "overflow: visible;" in mast_brand_rule
    assert "color: var(--light);" in mast_brand_rule
    assert "clip-path" not in mast_brand_rule
    word_start = css.index(".diony-word {")
    word_rule = css[word_start : css.index("}", word_start)]
    assert "var(--display)" in word_rule
    assert ".diony-brand::after" in css
    mast_line_start = css.index(".diony-brand::after {")
    mast_line_rule = css[mast_line_start : css.index(".bays", mast_line_start)]
    assert "content: none;" in mast_line_rule


def test_idea_produce_clap_reserves_space_for_rotation_and_shadow() -> None:
    css = (STATIC / "app.css").read_text()
    final_dock_start = css.index(".idea-dock {\n  --clap-safe-edge:")
    final_dock_rule = css[final_dock_start : css.index("}", final_dock_start)]

    assert "--clap-safe-edge: clamp(3rem, 4vw, 4.5rem);" in final_dock_rule
    assert "padding-right: var(--clap-safe-edge);" in final_dock_rule
    assert "padding-bottom: var(--clap-safe-edge);" in final_dock_rule


def test_idea_produce_clap_is_complete_without_hover() -> None:
    css = (STATIC / "app.css").read_text()
    final_clap_start = css.index(".clap {\n  flex-basis:")
    final_clap_rule = css[final_clap_start : css.index("}", final_clap_start)]
    final_stick_start = css.index(".clap-stick {", final_clap_start)
    final_stick_rule = css[final_stick_start : css.index("}", final_stick_start)]

    assert "padding-top: 0.65rem;" in final_clap_rule
    assert "transform: rotate(-2deg);" in final_stick_rule
    assert ".clap:hover .clap-stick" not in css


def test_production_refresh_is_a_dedicated_toolbar_action() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    production = html[html.index('id="bay-production"') : html.index('id="bay-gallery"')]
    toolbar_at = production.index('class="run-head-toolbar"')
    refresh_at = production.index('id="run-refresh"')
    title_at = production.index('id="run-title"')
    workspace_at = production.index('class="run-workspace"')

    assert toolbar_at < refresh_at < title_at < workspace_at
    assert production.count('id="run-refresh"') == 1
    assert 'class="word run-refresh-action"' in production
    assert 'data-run-refresh-label>Refresh</strong>' in production
    assert 'class="run-refresh-icon" aria-hidden="true">↻</span>' in production
    assert ".run-head-toolbar {" in css
    assert ".run-refresh-action {" in css
    assert ".run-refresh-action:hover:not(:disabled)" in css
    assert ".run-refresh-action:focus-visible" in css
    assert ".run-refresh-action:disabled" in css
    assert 'btn.querySelector("[data-run-refresh-label]")' in js
    assert 'refreshLabel.textContent = "Refreshing…";' in js


def test_production_monitor_has_one_live_focus_and_collapsed_evidence() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    production = html[html.index('id="bay-production"') : html.index('id="bay-gallery"')]

    assert 'class="run-workspace"' in production
    assert 'class="run-primary"' in production
    assert 'class="run-overview"' in production
    assert production.index('id="run-signal"') < production.index('id="run-clips"')
    assert '<details id="master-standard" class="master-standard"' in production
    assert '<details class="run-activity-drawer"' in production
    assert '<summary class="run-disclosure-head">' in production
    assert '<strong>Live production log</strong>' in production
    assert 'id="run-activity-live" class="activity-live-work"' in production
    assert 'id="run-activity-state"' in production
    assert 'id="run-activity-updated"' in production
    assert '? "LIVE NOW"' in js
    assert 'id="run-activity-current"' in production
    assert 'id="run-activity-current-detail"' in production
    assert 'id="run-activity-identity"' in production
    assert 'id="run-activity-stage"' in production
    assert 'id="run-activity-gpu"' in production
    assert 'id="run-activity-compute"' in production
    assert 'id="run-activity-log-meta"' in production
    assert 'Current work first. Evidence stays attached.' not in production
    assert 'Open a row for technical detail' not in production
    assert 'id="run-now" class="sr"' in production

    assert "#bay-production .run-workspace {" in css
    assert "#bay-production .run-primary {" in css
    assert "#bay-production .run-overview {" in css
    assert "#bay-production .run-activity-drawer {" in css
    assert "#bay-production .activity-live-work {" in css
    assert "#bay-production .activity-work-grid {" in css
    assert "#bay-production .activity-raw-message {" in css
    assert "#bay-production .run-disclosure-head {" in css
    assert "function paintActivityFocus" in js
    assert 'util !== null && util !== undefined && util !== ""' in js
    assert 'rawLabel.textContent = "Raw message";' in js
    assert 'status.textContent = activityStatusLabel(row.level);' in js
    assert 'count.textContent = run.tally || "—";' in js
    assert 'count.innerHTML = `${run.tally || "—"}' not in js
    assert 'clip.attempt_label || ""' in js
    assert 'clip.av_evidence_label || ""' in js


def test_production_watch_is_single_flight_and_never_animates_the_full_folio() -> None:
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    chorus_sheet_at = css.index(".sheet {\n  width: min(92rem")
    chorus_sheet_rule = css[chorus_sheet_at : css.index("}", chorus_sheet_at)]
    assert "animation:" not in chorus_sheet_rule
    assert ".producer-scene-transition .bay:not(.hidden)" not in css
    assert "@keyframes producer-scene-in" not in css
    assert "@keyframes producer-scene-back" not in css

    assert "let productionWatchInFlight = false;" in js
    assert "if (productionWatchInFlight) return;" in js
    assert "document.hidden" in js
    assert "window.setTimeout(productionWatchTick, delay)" in js
    assert "setInterval(tick, 2000)" not in js
    assert "setInterval(tick, 4000)" not in js
    assert "runVisualSignature" in js
    assert "if (signature === runRenderSignature)" in js


def test_studio_invitation_uses_a_distinct_resilient_primary_action() -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    js = (STATIC / "app.js").read_text()

    assert 'class="word invite-send invite-send-action"' in html
    assert 'data-invite-action-label>Send invitation</strong>' in html
    assert 'class="invite-send-arrow" aria-hidden="true">→</span>' in html
    assert ".invite-send-action {" in css
    assert ".invite-send-action:hover:not(:disabled)" in css
    assert ".invite-send-action:focus-visible" in css
    assert ".invite-send-action:disabled" in css
    assert 'submit.querySelector("[data-invite-action-label]")' in js
    assert 'submit.textContent = "Sending…"' not in js
    assert 'submit.textContent = "Send invitation"' not in js


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
    assert "diony-ai-optical-mark" not in html
    assert 'href="http://studio.diony.local"' in html
    assert "wizard" + ".diony.local" not in html
    assert not (STATIC / "diony-ai-optical-mark.png").exists()
    assert "The Studio checks this invitation. Diony cloud does not." in html
    assert "The Studio—not Diony cloud—checks this invitation." not in html
    assert ".join-brand .diony-mark" in css
    join_brand_start = css.index(".join-brand {")
    join_brand_rule = css[join_brand_start : css.index(".join-brand .diony-mark", join_brand_start)]
    assert "background: transparent;" in join_brand_rule
    assert "overflow: visible;" in join_brand_rule
    assert "color: var(--ink);" in join_brand_rule
    assert "clip-path" not in join_brand_rule
    join_line_start = css.index(".join-brand::after {")
    join_line_rule = css[join_line_start : css.index(".join-sheet > h1", join_line_start)]
    assert "content: none;" in join_line_rule
    assert "WHAT DIONY AI IS" in html
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
    assert "sessionStorage" in js
    assert ".inert = true" in js
    assert ".studio-drawer" in css
    assert ".join-studio" in css
    assert ".join-studio[hidden]" in css
    assert ".join-step[hidden]" in css
    assert 'document.body.classList.add("join-open")' in js
    assert "body.join-open" in css
    assert "body.join-open > .studio" in css
    assert ".cost-admin-only[hidden]" in css
    assert "@media (max-width: 760px)" in css
