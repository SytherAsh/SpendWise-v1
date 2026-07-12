# Testing Strategy

## `ml_service` (FastAPI backend)

- Framework: `pytest`, tests live in `ml_service/tests/` (`conftest.py`, `test_sms_pipeline.py`).
- Current coverage: SMS classification paths in `sms_parser.py` — financial-transaction field
  extraction, OTP/promotional/banking-alert/failed-transaction labeling.
- Convention demonstrated in `test_sms_pipeline.py`: call `parse_sms_body(body, sender)` directly
  with a realistic SMS string, assert on the structured `ParsedTransaction` fields
  (`classification_label`, `is_financial`, `amount`, `direction`, `bank`, `ref_id`,
  `recipient_name`) rather than mocking internals.
- Run: `cd ml_service && pytest`.

## `ml_preprocessing` (notebooks)

- No automated test suite today — correctness is currently checked via in-notebook validation cells
  (e.g. `Segregation.ipynb`'s `validate_dataframe` pass, the "100% CLEAN DATA" checks).
- {{TODO: `merchant_normalizer.py` and `build_unified_dataset.py` now live in
  `ml_service/app/services/` (moved 2026-07-12) precisely so they're testable — add pytest coverage
  for them in `ml_service/tests/` alongside the existing SMS-pipeline tests.}}

## Bank-statement pipeline (current task focus)

{{TODO: define once the pipeline exists — likely needs: fixture bank-statement files (with fake/
scrubbed data, never a real statement) covering both PDF and Excel input, assertions on the clean-CSV
output shape and merchant-extraction accuracy.}}

## What NOT to do

- Don't commit real personal bank-statement data as a test fixture, even scrubbed — build synthetic
  fixtures instead (see `docs/spec/security.md`).
