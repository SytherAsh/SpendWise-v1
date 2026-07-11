# SpendWise SMS Pipeline Audit and Data Quality Report

This report summarizes the FastAPI SMS ingestion and transaction extraction pipeline after the refactor.

## Scope

Audited components:

- `ml_service/app/main.py`
- `ml_service/app/routes/ingest.py`
- `ml_service/app/sms_parser.py`
- `ml_service/app/financial_sms_processor.py`
- `ml_service/app/schemas/transaction.py`
- `ml_service/app/phase2_to_4_pipeline.py`
- `ml_service/app/phase5_analytics.py`
- `ml_service/data/*` generated outputs

## Bugs Found Before Refactor

1. The parser only had a binary financial/non-financial decision and could not assign explicit labels such as OTP, promotional, banking alert, failed transaction, or bill reminder.
2. Spam and alert detection were incomplete, so non-financial messages could drift into the financial path when they contained bank-like keywords.
3. Amount extraction was too dependent on the first currency token and could be confused by balance fields or unrelated numeric values.
4. Recipient extraction returned noisy merchant strings and did not consistently normalize names.
5. The batch SMS processor produced only cleaned and financial CSVs; there was no structured unknown-review queue for manual labeling.
6. Timestamp normalization emitted warnings on mixed numeric/string inputs.
7. The pipeline did not expose confidence scores, which makes downstream ML review harder.

## Refactor Summary

### Parser and classification

- Added explicit SMS labels: `FINANCIAL_TRANSACTION`, `FAILED_TRANSACTION`, `OTP`, `PROMOTIONAL`, `BANKING_ALERT`, `BILL_REMINDER`, `UNKNOWN`.
- Added rule-based classification with a clear hierarchy.
- Added confidence scoring and classification reason tracking.
- Improved amount extraction by scoring amount candidates using surrounding context.
- Normalized recipient and merchant-like names before returning them.

### Processor and CSV pipeline

- Kept the raw capture pipeline intact.
- Added classification columns to the clean SMS output.
- Kept only `FINANCIAL_TRANSACTION` rows in `true_financial_sms.csv`.
- Added `unknown_sms.csv` for reviewable cases.
- Tightened timestamp parsing to avoid mixed-format warnings.

### Validation and tests

- Added focused pytest coverage for amount extraction, direction detection, spam filtering, OTP filtering, banking alert filtering, failed transaction filtering, bill reminder filtering, merchant cleanup, and processor output generation.
- Added a temp-file processor test to confirm the unknown-review queue is created.

## Current Dataset Metrics

The latest processor pass over `ml_service/data/captured_sms.csv` produced the following metrics.

| Metric | Value |
| --- | ---: |
| Raw SMS rows | 11,812 |
| Cleaned rows after dedup and parsing | 1,627 |
| Strict financial transactions | 66 |
| Unknown review queue rows | 629 |
| Exact duplicates removed | 611 |
| Cross-platform duplicates removed | 9,574 |

## Label Distribution In Clean SMS Data

| Label | Count |
| --- | ---: |
| UNKNOWN | 1,455 |
| PROMOTIONAL | 98 |
| FINANCIAL_TRANSACTION | 66 |
| OTP | 4 |
| FAILED_TRANSACTION | 3 |
| BANKING_ALERT | 1 |

## Financial Field Extraction Rates

| Field | Rate |
| --- | ---: |
| Amount | 100.0% |
| Direction | 100.0% |
| Bank | 90.9% |
| Reference ID | 78.8% |
| Merchant / Recipient | 86.4% |
| UPI ID | 0.0% |

### Interpretation

- Amount and direction extraction are stable for the current financial subset.
- Bank extraction is strong but still misses some senders that require body-level fallback or dictionary expansion.
- Reference-ID extraction is good but not complete, which is expected for some bank formats.
- UPI ID extraction is zero for the current financial subset because these financial rows mostly rely on bank reference text rather than explicit VPAs.

## Unknown Review Queue

The pipeline now writes `ml_service/data/unknown_sms.csv` with these columns:

- `body`
- `sender`
- `predicted_label`
- `confidence`
- `review_status`
- `true_label`

This dataset is designed for manual review and future supervised training.

## Recommendations

1. Expand the merchant alias file with more common counterparties and personal-transfer labels.
2. Add a small reviewed training set from `unknown_sms.csv` to bootstrap a supervised classifier.
3. Add bank-specific reference patterns as more SMS formats appear.
4. Keep the rule-based hierarchy as the first filter, then layer a statistical model on top for the `UNKNOWN` bucket.
5. Keep `true_financial_sms.csv` as the ML training source, not `clean_sms_eda.csv`, because the latter still contains resolved non-financial classes.

## Files Produced By The Refactor

- `ml_service/data/clean_sms_eda.csv`
- `ml_service/data/true_financial_sms.csv`
- `ml_service/data/unknown_sms.csv`
- `ml_service/tests/test_sms_pipeline.py`

## Final Assessment

The pipeline is now significantly closer to a production-grade preprocessing layer for financial ML work.

The most important improvement is that every SMS now receives an explicit label, and only rows that satisfy the strict financial validation path are allowed into `true_financial_sms.csv`.
