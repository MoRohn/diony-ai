# Blueprint 15 — Creative design system

Status: Day 1 locked  
Owner: Diony AI  
Applies to: Desk, Studio, Producer, future public pages

## Thesis

Diony AI is controlled creative ecstasy: intelligent production machinery with the confidence, pace, and wit of a great creative director. The product is bold, theatrical, and alive, but it never hides operational truth or slows the work.

The workflow is the performance. Progress, generation, finishing, retakes, and human gates provide the narrative motion. Decorative motion must not compete with those states.

## Visual tokens

| Token | Value | Use |
|---|---:|---|
| Deep Void | `#16191F` | dominant stage, dark video surfaces, primary ink |
| Parchment | `#F4F0E6` | work surface and primary type on Void |
| Diony Crimson | `#C41E3A` | one narrative ribbon, active actions, live generation |
| Stage Amber | `#E5AC48` | focus, machine magic, rare success punctuation |
| Smoke | `#9A968C` | secondary labels on Void |

Do not introduce gradients as decoration. A restrained radial light or progress treatment may explain depth, state, or focus. High contrast is mandatory.

The Diony AI ticket mark is the current logo across Desk, Studio, Producer, browser icons, and packaged desktop icons. Preserve the supplied ticket artwork and its transparent canvas on product surfaces. Square app icons center the complete ticket on opaque Parchment so the OS never substitutes a gray transparency field. Do not restore the former camera-lens wordmark or its clipped white-card treatment.

## Type performs

- Display: Archivo Black. Uppercase, tightly tracked, deliberately broken only at major story beats.
- Work: IBM Plex Sans. Clear at small sizes and calm in dense tools.
- Machine truth: IBM Plex Mono. Status, runtime, shot IDs, clocks, and compact navigation.
- Large display type is punctuation, not body copy. Operational content remains easy to scan.

## Signature: the Diony Chorus

The Chorus is a small family of original line-drawn stage spirits built from the same crimson line, laurel or vine gestures, direct eyes, and one amber accent. Desk welcomes and Studio guides. Producer's spirit is the namesake Dionysus: a grape-and-ivy crowned line portrait derived from classical subject matter but drawn as original Diony UI art. Chorus images are `aria-hidden`, never contain required information, and never dominate a working surface.

A single crimson ribbon runs through every surface: edge of the work sheet, active progress, CTA shadow, or stage frame. Do not multiply it into decorative stripes.

## Surface direction

### Desk

The overture. Oversized `IDEA IN. SHOW OUT.` atmosphere sits behind one decisive first-open card. Its tracking must preserve distinct letterforms at every supported width; display letters may approach but never overlap. The Diony AI ticket mark and the namesake Dionysus sketch carry the brand. Installation state, recovery, and Continue remain the only required actions, with one short supporting line beneath the state title.

### Studio

The build rehearsal. Studio is an integrated **production folio**, not a floating notebook: a full-height dark cue rail is joined directly to a flat, precision-framed operator workspace. The rail is a real ordered setup sequence, so numbering and its crimson cue edge encode progress. The mast is intentionally substantial enough to read as the product frame, not browser chrome. On the intro header, the `OWN YOUR STUDIO / PRODUCE AT COST` motto is centered against the window itself, independent of the logo and status columns. The current lesson performs in large type; fields and technical proof stay quiet and exact. The Start / Join decision uses distinct original Chorus portraits for maker and ensemble; it does not repeat the Diony Studio name inside both paths.

### Producer

The working stage. The Diony AI ticket mark anchors the mast. Idea may be expressive and spacious: one display thought, one sentence of guidance, one writing surface, and the namesake sketch. Production and Gallery become progressively denser. Live render motion belongs to the pipeline meter, current beat, clip states, and elapsed clock—not a generic spinner.

The Idea page's Produce clapboard is complete in its resting state at every supported viewport: its striped top, slate, and shadow remain inside the reserved action area without hover. Hover and focus may provide feedback, but never reveal geometry that was clipped or absent before interaction.

## Motion contract

- Page entrance: at most one orchestrated 620–700 ms reveal.
- Interaction feedback: 120–220 ms.
- State changes: 220–420 ms when movement helps explain the change.
- No decorative delay before a task can be used.
- Prefer transform and opacity; avoid layout-thrashing animation.
- `prefers-reduced-motion: reduce` collapses animation and transform while preserving state color and text.
- Studio line changes and Producer bay changes use one directional scene entrance plus one amber projector cue. The cue runs only when the logical page changes, never when status polling redraws the current page, and clears itself after the transition.
- Rapid navigation cancels the prior cue before starting the next one. Buttons and fields remain usable throughout; animation never owns or delays application state.

## Interaction and accessibility

- Every interactive control has a visible Stage Amber focus ring.
- The interface remains usable at 320 CSS pixels and with browser zoom.
- Color never carries status alone; labels, copy, or existing state semantics remain present.
- Decorative Chorus art is hidden from assistive technology.
- Minimum working-text contrast is WCAG AA. Primary actions and display text target AAA where practical.
- Short, active, specific copy wins. Wit may season an empty or success state; it may not obscure recovery.

## Originality boundary

Outside work may inform pacing, confidence, typographic scale, and the discipline of illustration. Diony does not copy another studio's layouts, characters, palette exactness, copy, or motifs. Every visual decision must return to Diony's real world: stage cues, showrunning, shot IDs, generation, 720 negatives, 1080 finish, and distribution.
