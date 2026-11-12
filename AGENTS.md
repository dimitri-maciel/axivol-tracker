# HCC-TACE Lab

- Keep TCIA ingestion, DICOM geometry/pixels, application operations and browser presentation separate. Prefer small functions and explicit source provenance.
- Treat mixed acquisitions, missing references and incompatible geometry as explicit states. Do not derive treatment response or POST volume from a PRE mask or substitute illustrative data for patient data.
- Use `hcc-tace-lab/compose.yaml`. Never add `data/`, downloaded archives, medical files, environment secrets or local dependency directories to Git or image build context.
- Validate a change where it matters: `docker compose exec -T app python -m unittest discover -s tests -v`; add `-e HCC_PILOT_TEST=1` to run the public HCC_002 integration checks after ingest. For UI changes, exercise the two real viewports and relevant controls in a browser.
- Report which case, acquisition and SEG label a measurement uses. Keep figure and app status language consistent with the verified scope.
