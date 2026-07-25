# Bank Statement Notebook Pipeline Migration

This document explains the offline notebook workflow in `ml_preprocessing/` for SBI bank statement ingestion, the recommended execution order, and the migration approach to a FastAPI service.

## Recommended notebook execution order

1. `ml_preprocessing/CSV_PARSER.ipynb`
   - Ingest raw bank-statement exports.
   - Decrypt password-protected Excel workbooks if needed.
   - Merge multiple yearly exports into one raw dataset.
   - Truncate trailing footer/legend rows per file.
   - Normalize raw columns and save a canonical raw workbook/CSV.

2. `ml_preprocessing/Segregation.ipynb`
   - Read the raw output from `CSV_PARSER.ipynb`.
   - Apply bank-specific regex/extraction rules to narration text.
   - Parse transaction fields such as mode, DR/CR, UPI ID, recipient, bank, and reference.
   - Clean spacing, convert dates and numeric fields, drop irrelevant columns.
   - Remove duplicates from overlapping raw exports.
   - Produce a cleaned statement dataset ready for merchant normalization.

3. `ml_preprocessing/MerchantNormalization.ipynb`
   - Load the cleaned statement dataset from `Segregation.ipynb`.
   - Run `ml_service/app/services/merchant_normalizer.py`-style recipient normalization.
   - Canonicalize `Recipient_Name` into `Recipient_Canonical` using UPI-ID grouping and fuzzy clustering.
   - Optionally use manual review / aliasing for unsafe cases if needed in offline QA.

4. `ml_preprocessing/BuildUnifiedDataset.ipynb` (optional, only if you need statement + SMS reconciliation)
   - Merge the cleaned statement dataset with SMS-derived transactions.
   - Deduplicate real-world transactions captured by both statement and SMS.
   - Backfill richer recipient names from SMS into truncated statement rows.
   - Recompute merchant canonicalization over the full unified dataset.
   - Produce a final unified dataset of statement and SMS transactions.

## What each notebook contributes

### `CSV_PARSER.ipynb`
- Entrypoint for raw bank-statement ingestion.
- Handles raw Excel/CSV inputs from SBI exports.
- Loads files from `CSVS/SBI/` and uses `BANK_STATEMENT_PASSWORD` from the environment for decryption.
- Applies a `truncate_at_first_blank_row` step to cut off footers before concatenation.
- Outputs a raw, canonical workbook such as `CSVS/SBI/SpendWise_4yrs_RAW.xlsx`.

### `Segregation.ipynb`
- Performs bank-specific narration parsing and structural extraction.
- Extracts fields from `Description`/`Details` using regex, splitting different statement formats.
- Cleans and renames columns to stable fields like `Transaction_Date`, `Amount`, `DR/CR_Indicator`, `Recipient_Name`, `UPI_ID`, and `Bank`.
- Normalizes whitespace and string casing, fills sentinel values like `UNKNOWN` or `PHONE_TRANSFER`.
- Removes duplicates and invalid rows.
- This notebook is the core of the statement-specific parser logic.

### `MerchantNormalization.ipynb`
- Applies merchant/recipient canonicalization to the cleaned statement data.
- Uses a bank-agnostic normalization algorithm from `ml_service/app/services/merchant_normalizer.py`.
- Outputs a canonical recipient label for transaction-level merchant grouping.
- This is the normalization step the website pipeline should reuse.

### `BuildUnifiedDataset.ipynb`
- Reconciles statement data with SMS transaction captures.
- Uses matching logic based on normalized references, date+amount+direction, and explicit disambiguation hints.
- Backfills SMS names into statement rows when safe.
- Recomputes canonical recipient names across the unified dataset.
- Use this notebook if your website needs the combined SMS + statement dataset; otherwise it is optional.

### `EDA.ipynb` and `Analyse.ipynb`
- Analysis, validation, and exploratory reporting.
- Not part of the core ingestion pipeline.
- Use them for QA, distribution checks, and dataset exploration after the main pipeline runs.

### `Raw_SmS.ipynb`
- Documented SMS ingestion pipeline, not the bank statement pipeline.
- It is related only in the reconciliation step; do not conflate it with the statement parser.

## Suggested FastAPI migration architecture

### 0. Statement upload storage and temp file organization

The service now stores upload artifacts in `ml_service/data/statement_uploads/`:
- `raw/` for timestamped raw CSV/Excel uploads
- `processed/` for cleaned CSV outputs

Every uploaded file is preserved with a safe timestamped name so you can keep using different input files without overwriting earlier uploads. The cleaned result for each request is also saved under `processed/`.

Use the FastAPI endpoint to upload files; do not manually drop files into the data folder unless you are archiving older uploads.

If a password-protected Excel workbook is uploaded, pass the password on the endpoint using the `password` query parameter.

### 1. Separate the pipeline into clear stages

Create Python service modules that mirror the notebook stages:
- raw ingestion / file loading
- bank-specific statement parsing
- merchant normalization
- optional reconciliation / unified dataset merging
- final output serialization

Keep each stage testable in isolation and avoid notebook-only constructs such as display tables, manual cell outputs, or one-off interactive sampling.

### 2. Build a service layer under `ml_service/app/services/`

Example service files:
- `ml_service/app/parsers/statementLoader.py` - load Excel/CSV, decrypt if needed.
- `ml_service/app/services/statementParser.py` - run the business rules from `Segregation.ipynb`.
- `ml_service/app/services/merchant_normalizer.py` - already exists; reuse it.
- `ml_service/app/routes/statementUpload.py` - endpoint for statement file uploads.

### 3. Expose a FastAPI route for file upload

Design a route such as:
- `POST /api/statements/upload`
- Accept file uploads for CSV/Excel (and later PDF).
- Validate the file type and parse it into a DataFrame.
- Run the sequential processing stages and return a cleaned CSV or JSON payload.

For CSV input specifically, the route should:
- read the CSV into pandas
- apply the same cleaning/normalization functions as `CSV_PARSER.ipynb` and `Segregation.ipynb`
- optionally canonicalize recipients via `merchant_normalizer.py`
- optionally reconcile against existing SMS/statement data if needed
- return or persist the final output

### 4. Keep model labeling separate

The current notebook set does not contain a transaction labeling model.
- The pipeline should produce clean structured transactions, not categories.
- If you need labeling later, treat it as a downstream stage after the final cleaned dataset is ready.
- Do not merge the merchant-normalization pipeline with a separate categorization model unless the use case explicitly requires it.

### 5. Use environment variables for sensitive inputs

Continue the notebook practice of reading the bank statement password from an environment variable (`BANK_STATEMENT_PASSWORD`) rather than hardcoding it.

## Notes on actual run order and outputs

- Run `CSV_PARSER.ipynb` first to produce `SpendWise_4yrs_RAW.xlsx` or equivalent raw dump.
- Run `Segregation.ipynb` next to produce a clean statement dataset with parsed transaction fields.
- Run `MerchantNormalization.ipynb` next to add canonical recipient names.
- If you want the final dataset merged with SMS data, run `BuildUnifiedDataset.ipynb` last.
- Use `EDA.ipynb` and `Analyse.ipynb` afterward for verification and dataset quality checks.

## Existing code to reuse

- `ml_service/app/services/merchant_normalizer.py`: merchant canonicalization logic.
- `ml_service/app/services/build_unified_dataset.py`: SMS/statement reconciliation and unified dataset construction.
- `ml_service/app/parsers/excel_loader.py`: example of loading a cleaned workbook for the live service.

## High-level approach

1. Turn notebook cells into pure functions.
2. Chain those functions inside a service API function.
3. Keep the pipeline sequential and deterministic.
4. Return a final structured dataset, not notebook outputs.
5. Add a FastAPI route that accepts file uploads and executes the cleaned pipeline.

This is the recommended migration path from the current offline notebook workflow to a web-usable Python/FastAPI pipeline.
