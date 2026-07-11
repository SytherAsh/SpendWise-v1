**SMS → True Financial Pipeline**

This document explains the code paths, functions, and key heuristics that convert raw SMS/notification rows into the `true_financial_sms.csv` output used by SpendWise's analytics and optional Supabase persistence.

**Repository Files**
- **Parser:** [ml_service/app/sms_parser.py](ml_service/app/sms_parser.py)
- **Processor:** [ml_service/app/financial_sms_processor.py](ml_service/app/financial_sms_processor.py)
- **Schemas / Models:** [ml_service/app/schemas/transaction.py](ml_service/app/schemas/transaction.py)
- **Ingest route:** [ml_service/app/routes/ingest.py](ml_service/app/routes/ingest.py)

**High-level pipeline**
- **1. Capture (ingest):** messages are appended to `data/captured_sms.csv` by the mobile client or ingest endpoint.
- **2. Batch processing:** `FinancialSmsProcessor.process_all()` reads `data/captured_sms.csv`, runs dedupe and parsing, and writes `data/clean_sms_eda.csv`, `data/true_financial_sms.csv`, and `data/unknown_sms.csv`.
- **3. Optional persistence:** `process_all(push_to_supabase=True)` will call the Supabase persistence helper (deferred import) for each parsed financial row.

**Key components and functions**

**Parser: `parse_sms_body()`** — [ml_service/app/sms_parser.py](ml_service/app/sms_parser.py)
- Entry point: `parse_sms_body(body: Optional[str], sender: Optional[str]) -> ParsedTransaction`.
- Behavior: normalizes text, classifies message label using `_classify_sms`, and when detected as `FINANCIAL_TRANSACTION` extracts structured fields and returns a `ParsedTransaction` instance.

Important helper functions used by the parser:
- **`_classify_sms(body, sender)`** — Applies a set of prioritized pattern checks to choose a label: `FINANCIAL_TRANSACTION`, `PROMOTIONAL`, `OTP`, `FAILED_TRANSACTION`, `BANKING_ALERT`, `BILL_REMINDER`, or `UNKNOWN`. Uses patterns such as `OTP_PATTERN`, `PROMOTIONAL_PATTERN`, `BANKING_ALERT_PATTERN` and a financial gate `FINANCIAL_KEYWORDS` to avoid false positives.
- **`_extract_transaction_amount(body, direction)`** — Finds amount candidates via `AMOUNT_PATTERN`, scores candidates using contextual cues (presence of debit/credit keywords, balance words, refund/cashback markers), and returns the best-scoring amount.
- **`_score_financial_confidence(body, parsed_amount, direction)`** — Returns a numeric confidence (0.0–0.98 cap) based on presence of amount, direction, financial keywords, refs, and account words.
- **`_extract_ref_id(body)`** — Finds transaction reference IDs with `REF_PATTERN` (Ref, UTR, UPI Ref, Txn ID).
- **Recipient extraction helpers:** `_extract_recipient`, `_fallback_merchant_extraction`, `_fallback_account_recipient` attempt to obtain a clean `recipient_name` (merchant or counterparty).
- **Normalization helpers:** `normalize_timestamp`, `sms_body_hash`, `_normalize_text`, `_clean_entity_name`.

Regex and signals the parser relies on (not exhaustive):
- `AMOUNT_PATTERN`, `BALANCE_PATTERN`, `REF_PATTERN`, `UPI_PATTERN`, `MODE_PATTERN`, `DEBIT_KEYWORDS`, `CREDIT_KEYWORDS`, `FINANCIAL_KEYWORDS`, `PROMOTIONAL_PATTERN`, `OTP_PATTERN`, `BANKING_ALERT_PATTERN`, `SPAM_BODY_PATTERN`.

**Models: `ParsedTransaction`** — [ml_service/app/schemas/transaction.py](ml_service/app/schemas/transaction.py)
- Fields populated by the parser include: `amount`, `direction` (DEBIT/ CREDIT), `bank`, `upi_id`, `recipient_name`, `transaction_mode`, `account_suffix`, `balance_after`, `ref_id`, `is_financial`, `classification_label`, `classification_confidence`, `classification_reason`.
- Validators: numeric coercion for `amount` / `balance_after`, normalization for `direction`, and confidence clamping to [0.0, 1.0].

**Processor: `FinancialSmsProcessor`** — [ml_service/app/financial_sms_processor.py](ml_service/app/financial_sms_processor.py)
- Primary method: `process_all(push_to_supabase: bool = False) -> Optional[dict]`.
- Steps inside `process_all()`:
  - Read `data/captured_sms.csv` (default) into a DataFrame.
  - Remove exact duplicates by `['body', 'timestamp_ms']`.
  - Normalize mixed timestamp formats (epoch ms and ISO strings) into `parsed_datetime`.
  - Drop rows with invalid timestamps and keep rows in the expected year.
  - Call `_parse_financial_sms(df)` which runs `parse_sms_body()` for each row and materializes parsed columns:
    - `parsed_is_financial`, `parsed_amount`, `parsed_direction`, `parsed_ref_id`, `parsed_entity`, `parsed_bank`, plus classification fields.
  - Normalize the cleaned frame (`_normalize_clean_sms_frame`) to ensure types and default values for `classification_label` and `classification_confidence`.
  - Promote parsed columns into canonical columns `is_financial`, `amount`, `direction`, `ref_id`, `entity`.
  - Cross-platform deduplication: compute `time_bucket_2m = parsed_datetime.floor('2min')` and drop duplicates on `['amount', 'direction', 'time_bucket_2m']` — this deduplicates SMS vs notification pairs.
  - Save `clean_sms_eda.csv` (full cleaned dataset).
  - Build `financial_df` filtered by `classification_label == 'FINANCIAL_TRANSACTION'` and do safe body cleanup before saving `data/true_financial_sms.csv`.
  - Build an unknown review queue via `_build_unknown_review_queue(df)` and save `data/unknown_sms.csv`.
  - Optionally call `_push_to_supabase(financial_df)` to persist rows.

**Unknown review queue**
- `_build_unknown_review_queue(df)` selects rows where `classification_label == 'UNKNOWN'` or `classification_confidence < UNKNOWN_REVIEW_THRESHOLD` (0.75), annotates `predicted_label`, `confidence`, `review_status` (`needs_label` or `needs_review`) and writes columns useful for human labeling.

**Dedup rules and causes of missing rows**
- Exact duplicate removal: `df.drop_duplicates(subset=['body','timestamp_ms'])` removes literal duplicates produced by multiple app writes.
- Cross-platform dedupe: `drop_duplicates(subset=['amount','direction','time_bucket_2m'])` removes near-simultaneous duplicates across channels. If two entries share amount + direction in the same 2-minute bucket they collapse to one row — this is frequently the reason a row present in an older CSV is absent after reprocessing.
- Demotion to `UNKNOWN`: messages classified initially as `FINANCIAL_TRANSACTION` but missing either `amount` or `direction` are demoted to `UNKNOWN` in the parser with lowered confidence; this is another common reason for omission.

**Supabase persistence**
- `_push_to_supabase(financial_df)` re-parses each row into a `ParsedTransaction` and calls `persist_sms_transaction()` from `app/service.py`. The import is deferred so the pipeline works even with Supabase unavailable.

**CLI & developer commands**
- Regenerate outputs:
  ```powershell
  cd SpendWise\ml_service
  & "c:\Users\yashs\Desktop\Journey\venv\Scripts\python.exe" -c "from app.financial_sms_processor import FinancialSmsProcessor; FinancialSmsProcessor().process_all(push_to_supabase=False)"
  ```
- Run tests:
  ```powershell
  cd SpendWise\ml_service
  & "c:\Users\yashs\Desktop\Journey\venv\Scripts\python.exe" -m pytest tests/test_sms_pipeline.py -q
  ```

**Troubleshooting & notes**
- If a transaction is missing from `data/true_financial_sms.csv`:
  - Check whether it was deduplicated by amount+direction+2-min time bucket (see cross-platform dedupe).
  - Check whether parser demoted it to `UNKNOWN` because amount or direction couldn't be reliably extracted.
  - Use `sms_body_hash(body, sender)` from the parser to compare dedupe keys if needed.
- For suspected balance extraction errors (balance captured instead of transaction amount) consider improving `_extract_transaction_amount()` heuristics to penalize `BALANCE_PATTERN` matches more strongly or prefer amounts near debit/credit keywords.
- To reduce false positives from promotional messages, adjust `PROMOTIONAL_PATTERN` and the gating logic in `_classify_sms` which currently prefers `PROMOTIONAL` if strong promo cues exist unless explicit transactional keywords exist.

**Recommended next improvements**
- Add deterministic selection when cross-platform duplicates exist (prefer messages with `ref_id` or higher `classification_confidence`).
- Expand merchant alias canonicalisation in `_fallback_merchant_extraction` for better `recipient_name` coverage.
- Add a small audit function to highlight rows where `balance_after` ≈ `amount` (likely mistaken extraction) and auto-flag them for review.

If you want, I can add direct source-line references for each function (e.g. exact line ranges) or generate a visual flow diagram. Which would you prefer next?
