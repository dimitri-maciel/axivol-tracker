# Interface verification

The locally generated screenshots show the public HCC_002 case from HCC-TACE-Seg, baseline acquisition 1, published SEG label Mass (segment 2), with window 350 and level 50. The measured segmentation volume is 495.04 cm³ (324,430 voxels, 109 referenced slices). The follow-up image is shown without a segmentation. No registration or treatment-response result is represented.

- Desktop: baseline slice 76/109 and follow-up slice 49/97.
- Mobile: baseline slice 73/109 and follow-up slice 46/97.
- ui-validation.json: functional browser checks and image-export verification.
- accessibility-desktop.json and accessibility-mobile.json: automated axe-core findings; incomplete checks still require manual review.
- performance.json: observed local timings before and after the loading changes.

Images: Moawad et al. (2021), The Cancer Imaging Archive, [DOI 10.7937/TCIA.5FNA-0924](https://doi.org/10.7937/TCIA.5FNA-0924), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). These are UI captures derived from the source images and segmentation, not raw DICOM files. Display windowing and the cyan overlay do not alter measurement geometry.

These checks are technical verification of a prototype, not clinical validation or certification.
