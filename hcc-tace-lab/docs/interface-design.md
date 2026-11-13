# Axivol Tracker interface

The workspace supports reviewing baseline and follow-up CT, inspecting published baseline annotations and comparing separate, unreviewed mask versions. The two real CT images are the primary content. Case selection and exports share the top bar; source selection belongs to each viewport, and measurement provenance remains next to the result.

The interface uses English, sentence case and established imaging terms: **Baseline**, **Follow-up**, **Series**, **Acquisition**, **Segmentation**, **Window**, **Level** and **Export report**. Original DICOM descriptions and segment labels remain unchanged. The heading no longer repeats the diagnosis.

## Visual system

- Canvas #111820; surface #1b2530; raised controls #273544; borders #3a4857; text #edf3f8; secondary text #abb9c7; accent #a7c9ff; pending state #edca88. CT backgrounds remain black and segmentation overlays cyan #32d5de.
- Helvetica Neue with platform sans-serif fallbacks, restrained sentence-case labels and tabular measurement numerals.
- Paired CT viewports with a 340 px right-hand **Segmentation** inspector (360 px above 1800 px). At 1150 px or below the inspector moves beneath the images; at 720 px or below the viewports stack.
- **Baseline** and **Follow-up** tabs show one toolset at a time. Overlay display, refinement, editing, seed-based segmentation, source details and comparison status start collapsed; current mask status and primary actions remain visible.
- Labels align left; source and status values align right. Color supplements explicit text.

## Usability references

These are design inputs, not a certification or a claim of full conformity. Representative-user testing, clinical validation, diagnostic display calibration and a complete standards assessment have not been performed.

| Reference | Application |
| --- | --- |
| [FDA human factors guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/applying-human-factors-and-usability-engineering-medical-devices), current page checked November 11, 2026 | Case identity, current selection, explicit failure states, separation of measurements from pending comparisons, rejection of stale asynchronous results. |
| [IEC 62366-1:2015+A1:2020](https://webstore.iec.ch/en/publication/67220), public scope and current status | Use-related safety guides the interaction review. This work does not constitute the standard's usability engineering process. |
| WCAG 2.2: [contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [focus](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html), [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) | Keyboard focus, named controls, 40 px buttons and a 44 px primary action, contrast, loading/error states and responsive layout. |
| [OHIF viewport guide](https://docs.ohif.org/user-guide/viewer/viewport/) | Paired viewports, per-study selection, zoom/pan, window/level controls and source metadata. |
| [3D Slicer Segment Editor](https://slicer.readthedocs.io/en/latest/user_guide/modules/segmenteditor.html) | Explicit source and structure selection, separate display controls, and tools that operate on a selected segmentation. |

## Interaction rules

- Baseline and follow-up are assigned from the dataset's chronological study pair. Contrast phase is **not verified**.
- Linked scrolling uses relative slice position; it does not imply anatomical registration.
- Inspector tabs support Left/Right, Home and End with visible focus and one tab stop. **Mask tools** beneath each viewport opens the corresponding tab. Switching tabs preserves study selections, slice positions, transforms and saved-mask visibility; it pauses active tools in the panel being hidden. Pausing baseline editing retains its unsaved strokes for the current source but restores the published-mask display.
- Changing case, series, acquisition or segment immediately clears the previous measurement.
- Measurement requires unique segment references and compatible geometry. Missing or mismatched references produce an explicit error.
- Follow-up masks retain **Automatic mask · Unreviewed** or **Seed-based mask · Unreviewed** status even when their overlay is hidden. **Mask hidden** and Show/Hide actions describe visibility separately. A missing mask offers generation; a compatible saved mask offers **Show saved mask**. Automatic generation requires the selected published **Mass** reference; edited baseline copies are not its input.
- Automatic registration remains **Unverified**. Failed or abstained runs expose their reason and downloadable run details without replacing an existing mask. Expert reference segmentation and volume change remain unavailable; no clinical response is inferred.
- During slice loading, the previous image remains visible with a loading indicator. Its counter and overlay label change with the decoded image.
- Export is disabled while images are unresolved. Failed loads hide the stale image; a view or inspector-tab change invalidates an in-progress PNG export. The export clone omits closed-disclosure content and preserves the displayed CT contain geometry; the live workspace stays unchanged.
- Focus an image for mouse-wheel or arrow-key scrolling. Zoom is independent; drag to pan. Reset display restores zoom and the existing 350/50 window/level defaults.
- Invalid window/level input is rejected; navigation uses the last valid values.

## Slice loading

The browser module **web/frame-loader.js** requests one image at a time, replaces queued intermediate slices with the latest request, and prefetches up to three neighbors in each direction while idle. Its 48-frame LRU cache pins displayed images. Changing case clears the cache and revokes blob URLs.

**Lab.image** caches up to 192 PNGs. Its key includes series, acquisition, slice, window, level, segment or candidate, mask style and opacity. Neither cache changes DICOM pixels, mask references or measurement geometry. Downloaded DICOM files remain read-only and excluded from Git and the image build context.

## Baseline mask review

The default baseline overlay combines an inner one-pixel outline with 15% fill. **Outline**, **Outline + fill**, **Fill** and opacity controls change presentation only. Automatic follow-up masks also use outline plus 15% fill. No edge smoothing or automatic shrinking is applied by these display controls.

**Edit copy** starts from the published segment. Paint and erase use a physical brush radius, apply to one slice at a time and support undo. Zoom returns to 100% and pan is disabled while editing. Hiding the overlay prevents drawing. The editor rejects stale mask loads and save results after source changes. Saving creates a separate unreviewed version; **Published baseline volume** remains separate from its volume. Saved versions can be previewed and exported, and new edit sessions begin from the original.

`tests/ui_pre_mask.cjs` exercises the real baseline viewport, brush coordinates, mask display, undo, immutable saving, source changes and the separate report fields. Run it against an isolated QA instance with `AXIVOL_URL`; it creates engineering test edits, not clinical annotations.

**Refine boundary** creates a separate PRE proposal using a 1–5 mm **Boundary band** (default 3 mm). Manual drafts block refinement until saved or cleared. The version selector distinguishes **Original (published)**, **Edited** and **Refined**; a refined result reports added/removed voxels and changed slices. Source changes invalidate delayed results. No-change and insufficient-contrast outcomes remain explicit, with no accuracy claim. Refinement starts from the published mask, and brush sessions still start from that original.

`tests/ui_refinement.cjs` tests generation and comparison on an isolated QA app, preserving the original measurement, independent POST state and existing review controls.

## Verification

Geometry and public-case integration checks, from hcc-tace-lab:

    docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v

Browser checks require Playwright and Chromium outside the application image:

    AXIVOL_URL=http://127.0.0.1:8770 AXIVOL_QA_DIR=output/ui-workspace node tests/ui_workspace.cjs

`ui_workspace.cjs` requires existing automatic masks for HCC_002 (baseline acquisition 1) and HCC_006 (baseline acquisition 3), both published **Mass (2)** with follow-up acquisition 1. It blocks every request except GET/HEAD, activates tools without drawing, and checks that saved candidate metadata stays unchanged. It covers both real viewports, keyboard tabs, tool pausing, overlay hide/show, slice navigation and 1600/1280/390 px layouts. It performs no inference or anatomical assessment. The current layout passed these checks and visual review on November 12, 2026.

`node tests/ui_review.cjs` provides broader display and export regression checks; it accepts `AXIVOL_URL`, `AXIVOL_QA_DIR` and an optional `AXIVOL_AXE_PATH` for axe-core. Tests that generate or edit masks require an isolated QA instance, as described above.

Screenshots and JSON reports remain local and excluded from version control; executable checks stay in `tests/`. Automated checks supplement visual inspection and do not establish clinical usability.

Verified on November 12, 2026: 165 backend tests including the public HCC_002 integration checks; 31 browser check groups across display/export, PRE editing, seed-based POST segmentation and boundary refinement. The editing suites used a fresh temporary annotation store per run. A separate check covered existing HCC_002/HCC_006 masks without writes, including an intercepted generation error. Axe reported no automated WCAG A/AA violations on desktop/mobile, including expanded tools; image-background contrast still required visual inspection. These results establish software behavior, not anatomical accuracy or clinical usability.
