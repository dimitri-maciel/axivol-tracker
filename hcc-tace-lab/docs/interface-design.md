# Axivol Tracker interface

The workspace supports reviewing baseline and follow-up CT, inspecting the published baseline segmentation and measuring it in native geometry. The two real CT images are the primary content. Case identity is presented in a compact context bar; source selection belongs to each viewport, and measurement provenance remains next to the result.

The interface uses English, sentence case and established imaging terms: **Baseline**, **Follow-up**, **Series**, **Acquisition**, **Segmentation**, **Window**, **Level** and **Export report**. Original DICOM descriptions and segment labels remain unchanged. The heading no longer repeats the diagnosis.

## Visual system

- Canvas #111418; surface #1a1f25; raised controls #222931; text #f0f3f7; accent #a7c9ff; pending state #edca88. The existing segmentation overlay remains cyan #32d5de.
- Helvetica Neue with platform sans-serif fallbacks, restrained sentence-case labels and tabular measurement numerals.
- A compact case context above a dual-view CT workspace and a right-hand measurement panel. At narrower widths the panel moves below; on phones the viewports stack.
- Labels align left; source and status values align right. Color supplements explicit text.

## Usability references

These are design inputs, not a certification or a claim of full conformity. Representative-user testing, clinical validation, diagnostic display calibration and a complete standards assessment have not been performed.

| Reference | Application |
| --- | --- |
| [FDA human factors guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/applying-human-factors-and-usability-engineering-medical-devices), current page checked November 11, 2026 | Case identity, current selection, explicit failure states, separation of measurements from pending comparisons, rejection of stale asynchronous results. |
| [IEC 62366-1:2015+A1:2020](https://webstore.iec.ch/en/publication/67220), public scope and current status | Use-related safety guides the interaction review. This work does not constitute the standard's usability engineering process. |
| WCAG 2.2: [contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [focus](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html), [target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) | Keyboard focus, named controls, 40 px buttons and a 44 px primary action, contrast, loading/error states and responsive layout. |
| [OHIF viewport guide](https://docs.ohif.org/user-guide/viewer/viewport/) | Paired viewports, per-study selection, zoom/pan, window/level controls and source metadata. |

## Interaction rules

- Baseline and follow-up are assigned from the dataset's chronological study pair. Contrast phase is **not verified**.
- Linked scrolling uses relative slice position; it does not imply anatomical registration.
- Changing case, series, acquisition or segment immediately clears the previous measurement.
- Measurement requires unique segment references and compatible geometry. Missing or mismatched references produce an explicit error.
- Follow-up segmentation and volume change remain unavailable. No clinical response is inferred.
- During slice loading, the previous image remains visible with a loading indicator. Its counter and overlay label change with the decoded image.
- Export is disabled while images are unresolved. Failed loads hide the stale image; a view change invalidates an in-progress PNG export.
- Focus an image for mouse-wheel or arrow-key scrolling. Zoom is independent; drag to pan. Reset display restores zoom and the existing 350/50 window/level defaults.
- Invalid window/level input is rejected; navigation uses the last valid values.

## Slice loading

The browser module **web/frame-loader.js** requests one image at a time, replaces queued intermediate slices with the latest request, and prefetches up to three neighbors in each direction while idle. Its 48-frame LRU cache pins displayed images. Changing case clears the cache and revokes blob URLs.

**Lab.image** caches up to 192 PNGs. Its key includes series, acquisition, slice, window, level and segment. Neither cache changes DICOM pixels, mask references or measurement geometry. Downloaded DICOM files remain read-only and excluded from Git and the image build context.

## Verification

Geometry and public-case integration checks, from hcc-tace-lab:

    docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v

Browser checks require Playwright and Chromium outside the application image:

    node tests/ui_review.cjs

Use AXIVOL_URL to override http://127.0.0.1:8770, AXIVOL_QA_DIR for artifacts and AXIVOL_AXE_PATH for an installed axe-core script.

Screenshots and JSON reports are generated locally in `output/ui/` by default. They are excluded from version control; the executable checks remain in `tests/`.

The browser check exercises HCC_002 baseline acquisition 1, segment 2 (Mass), both real CT viewports, source changes between its two referenced acquisitions, overlay pixels, linked and independent scrolling, burst navigation, zoom/pan, numeric validation, missing segmentation, JSON/PNG exports, failure states and 1600/1280/1024/390/320 px layouts. Automated checks supplement visual inspection; they do not establish clinical usability.
