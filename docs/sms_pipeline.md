# SMS Ingestion Pipeline

The pipeline that turns raw SMS/notification text captured from the user's phone into clean,
deduplicated financial transactions. See `CLAUDE.md`'s "Explicitly out of scope" section — this is a
separate concern from the bank-statement (PDF/Excel) pipeline and from category assignment
(`app/routes/categorize.py`).

Verified against the code and the live captured dataset (single SBI account, ~3,300 messages spanning
2022–2026) as of 2026-07-12.

## Two paths, one parser

```
Android app
  │  POST /api/data (single) or POST /api/data/bulk (batch)
  ▼
app/routes/ingest.py                          ── LIVE capture path
  │  parse_sms_body()  [app/parsers/sms_parser.py]     (parsed only for the response + Supabase)
  │
  ├──► data/captured_sms.csv   raw append-only capture: id, sender, body, timestamp_ms,
  │                            timestamp_human, device_id — NO parser output (see note)
  └──► Supabase `transactions` only when financial; failure never blocks the CSV write

app/services/sms_pipeline.py                   ── OFFLINE batch path (run by hand)
  reads data/captured_sms.csv, re-parses everything, and writes:
    ├──► data/clean_sms_eda.csv        every row, cleaned + labeled (all classes)
    ├──► data/true_financial_sms.csv   deduplicated financial transactions — the deliverable
    └──► data/review_queue_sms.csv     UNKNOWN / low-confidence rows for manual labeling
```

The raw CSV deliberately stores **only what the phone sent** — no `is_financial`/`amount`/etc.
Classification and extraction are always re-derived downstream from `body`, so the raw file never goes
stale when the parser improves, and never needs to be re-captured from the phone. Both paths share one
engine (`sms_parser.py`), so a parser change affects both identically.

## Running it

```bash
cd ml_service

# Live capture (phone -> CSV). Supabase is OPTIONAL — without a .env it logs a warning
# and still captures to CSV; it does not crash.
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000   # 0.0.0.0 so the phone can reach it over Wi-Fi

# Offline batch: regenerate the clean outputs after new data has synced.
python -m app.services.sms_pipeline
```

Point the Android app's backend URL at `http://<pc-lan-ip>:8000/api/data`. Run tests with
`pytest tests/test_sms_pipeline.py -q`.

## The five stages (`app/services/sms_pipeline.py`)

`SmsPipeline.run()` executes these in order; each stage is a pure DataFrame→DataFrame transform, so
the whole thing is re-runnable over the raw file at any time.

1. **Structural clean** (`structural_clean`) — content-agnostic. Drop exact resends
   (`body` + `timestamp_ms`), normalize mixed timestamp formats to **IST** (Indian user + banks; UTC
   would misattribute late-night transactions to the previous day), drop unparseable timestamps
   (counted, not silently dropped), sort oldest-first. **No year filter** — the raw file legitimately
   spans several years.
2. **Classify + extract** (`classify_and_extract`) — one `parse_sms_body()` call per row assigns a
   label + confidence and, for financial rows, extracts amount, direction, bank, mode, recipient,
   UPI ID, ref ID, balance. One pass, not two.
3. *(extraction is part of stage 2 — same parser call)*
4. **Transaction dedup** (`dedup_transactions`) — two passes. **Pass 1** collapses rows sharing a
   non-null **`ref_id`** (authoritative: the same SBI SMS re-delivered via different telco gateways
   minutes/hours apart; different refs stay distinct — SBI assigns a fresh reference per payment).
   **Pass 2** clusters adjacent `(amount, direction)` rows within a window and asks how many distinct
   transactions the cluster holds: `max(distinct target-phones, distinct ref_ids, 1)`. This is what
   lets one telecom recharge (bank debit + 2-3 operator confirmations, one target phone) collapse to
   **one** row, while two family numbers recharged for the same amount minutes apart (two phones) stay
   **two** — and two separate same-amount UPI payments (two refs) stay two. The survivor is the bank
   SMS (ref-bearing, higher confidence). Adjacency, not a fixed clock bucket, is deliberate:
   floor-bucketing split genuine twins straddling a boundary (e.g. 19s apart across :50/:52).
5. **Validate** (`validate`) — measures quality without mutating: extraction-rate per field,
   direction split, `amount == balance` suspect count, amount total/max. A rate table alone hides the
   defects that matter (see below), so this exists to surface them.

A **review queue** (`build_review_queue`) captures every `UNKNOWN` or sub-0.75-confidence row with a
blank `true_label` column — the raw material for a future labeling pass / supervised model.

## Classifier: `app/parsers/sms_parser.py`

`parse_sms_body(body, sender)` returns a `ParsedTransaction`. Classification assigns one of
`FINANCIAL_TRANSACTION`, `OTP`, `PROMOTIONAL`, `BANKING_ALERT`, `FAILED_TRANSACTION`,
`BILL_REMINDER`, `UNKNOWN`. A message only reaches `FINANCIAL_TRANSACTION` if it has an amount, a
direction, and financial context; anything short of that is demoted to `UNKNOWN` for review rather
than guessed.

**Scam gate** (added 2026-07-12, protects both the live and batch paths): fake-transaction spam mimics
real wording ("Transaction done of Rs.98,650 to your Rummy A/C, Withdraw now") and, left unchecked,
injected ~₹381k of phantom money into the financial set. Rejected before the financial path by four
signals — raw phone-number senders (`+9163…`; real banks use alphanumeric IDs), URL shorteners
(bank SMS never carry links), gambling keywords, and collect/request-money messages ("has requested
Rs500… once approved", which are conditional, not completed).

**Amount vs. balance** (fixed 2026-07-12): in some SBI formats the balance sits far from the amount
("Debited INR 10,000 … Avl Balance INR 42,916.45") and a naive scorer picked the larger balance.
`_extract_transaction_amount` now uses `BALANCE_PATTERN` to pinpoint the balance figure and excludes
that exact number from amount candidates.

**Payment-confirmation recovery** (added 2026-07-12, from a manual-review pass): real outgoing
payments that don't use "debited" wording were being dropped — telecom recharges ("Recharge of INR 319
is successful", "we have processed Rs 299 for your Airtel Mobile"), internet-banking transfers ("INB
txn of Rs 102.12 to IRCTC"), and booking payments. `PAYMENT_CONFIRMATION_PATTERN` recovers them as
**DEBIT** transactions (runs before the promo gate so Airtel confirmations aren't lost to it). Two
guards: mandate/autopay *setup* SMS ("UPI-Mandate for Rs.29 created") are excluded — no money moves at
creation; and for telecom confirmations the per-SMS "Order Id"/"Transaction ID" is **not** stored as a
`ref_id` (it isn't a bank reference, and keeping it would stop the 2-3 SMS of one recharge from
deduping). The pipeline's Pass-2 identity rule (above) then collapses those multi-SMS recharges.

**Recipient extraction — "you" bug** (fixed 2026-07-12, from a manual-review pass): an SBI IMPS-credit
format puts the payer's name at the end ("...credited by Rs.1500 ... by a/c linked to mobile
9XXXXXX567-SAMEER BALIRAM SAWA (IMPS Ref no ...)"), but the generic "by NAME" credit pattern backtracked
past it onto the safety boilerplate ("If not done by **you**, call...") and returned "you" as the
recipient — silently wrong on 63 rows. Fixed with a dedicated pattern for this format (highest
priority in `RECIPIENT_CREDIT_PATTERNS`) plus a `RECIPIENT_JUNK` guard so boilerplate words ("you",
"the", "call", ...) can never be returned as a recipient — the extractor falls through to the next
pattern instead.

## Reference notebook: `ml_preprocessing/SMS_Pipeline.ipynb`

A cell-by-cell walkthrough of this pipeline for interactive inspection. It **imports and runs the real
`sms_pipeline.py` / `sms_parser.py` code** rather than reimplementing any logic, so it can't drift out
of sync — several cells print function source (`inspect.getsource`) so the regex/classification logic
is readable inline without switching files. Also includes the SMS-vs-bank-statement cross-check (see
below). Not to be confused with `Raw_SmS.ipynb`, which is earlier exploratory work that predates this
architecture.

## Persistence: `app/services/persistence.py`

`persist_sms_transaction()` resolves/creates an account and recipient, runs a DB-level dedup
(`transaction_exists`: by ref_id, else amount+direction+same-day), and inserts. Supabase is optional —
`supabase_client.py` sets the client to `None` and logs a warning when credentials are absent, and
every call site is exception-safe, so the live capture path runs fine without a `.env`. Known gap: the
`accounts` table has no `account_suffix` column, so two accounts at the same bank collapse into one.

## Data artifacts

| File | Written by | Purpose |
| --- | --- | --- |
| `data/captured_sms.csv` | `routes/ingest.py` | Raw append-only capture, every message, never cleaned |
| `data/sync_state.json` | `routes/ingest.py` | Last-synced timestamp per device, for incremental sync |
| `data/clean_sms_eda.csv` | `sms_pipeline.py` | Full cleaned + labeled dataset, all classes |
| `data/true_financial_sms.csv` | `sms_pipeline.py` | Deduplicated financial transactions — the deliverable |
| `data/review_queue_sms.csv` | `sms_pipeline.py` | `UNKNOWN` / confidence < 0.75, with a `true_label` column to fill in |

Row counts drift as more SMS is captured — don't hardcode them into docs. Run the pipeline and read
its printed summary for a current snapshot.

## Validation against ground truth

The extracted amounts were cross-checked against the independently-produced bank workbook
(`ml_preprocessing/CSVS/SpendWise_4yrs_Clean_Merchants.xlsx`) by reference ID: on the ~840 transactions
present in both, **every amount matched exactly** (0 mismatches). This is the strongest available
correctness signal and specifically validates the amount-vs-balance fix. Re-run it whenever the parser
changes materially.

## SMS vs. bank-statement overlap (important before merging sources)

The SMS pipeline only dedupes SMS-against-SMS — it has no knowledge of the bank statement, and most SMS
transactions **already exist there**. A manual audit (2026-07-12, see
`ml_service/data/manual_review_error_found.xlsx`, `tru_financial_deplicate.xlsx`,
`ambiguous_matches_review.xlsx`) reconciled all 1,078 SMS financial transactions against the workbook:

| Category | Count | Resolution |
| --- | --- | --- |
| Exact `ref_id` match | 846 | Confirmed duplicate |
| Date + amount + direction match (ref differed/missing — e.g. bank-fee/insurance auto-debits never carry a ref on either side) | 54 | Confirmed duplicate |
| Ambiguous (multiple statement candidates same day/amount/direction), resolved by reading recipient text or raw SMS | 9 | 3 confirmed duplicate; 6 confirmed **not** ambiguous — both SMS rows already correctly 1:1-match both statement rows (two family phones recharged the same amount minutes apart) |
| Confirmed via manual review as a genuinely different payment instrument (money moved to a linked wallet app, e.g. slice) | 1 | Genuine SMS-only |
| Remaining, no statement match at all | 168 | Genuine SMS-only |
| **Total** | **1,078** | **909 duplicates / 169 genuinely SMS-only** |

**This merge is now built**: `ml_preprocessing/build_unified_dataset.py` produces
`ml_preprocessing/CSVS/SpendWise_Unified_Merchants.xlsx` — the bank statement as the base (1,917 rows),
with recipient names backfilled from the fuller SMS name wherever unambiguously matched (821 rows), and
the 169 genuinely SMS-only transactions appended as new rows — 2,086 rows total, deduped, one source of
truth. Every SMS row gets exactly one outcome (`backfilled` / `no_name` / `ambiguous` / `new`), asserted
in code to sum to the SMS financial count so a routing bug can't silently duplicate or drop a row. The 6
same-day/same-amount Airtel pairs are confirmed real duplicates but not safely 1:1-pairable by
reference id, so their statement names are intentionally left untouched rather than risk a wrong
pairing. Re-run the script whenever `captured_sms.csv` gets new data.

### Recipient canonicalization (`ml_preprocessing/merchant_normalizer.py`)

After the merge, `Recipient_Canonical` is recomputed from scratch over the whole unified dataset (UPI-ID
grouping + fuzzy name clustering, pre-existing tooling) plus two additions from 2026-07-12:

- **`_first_names_conflict` safety guard** — fixes a real bug found during this pass: two different real
  people sharing a UPI ID (a family's shared payment handle) could be merged into one canonical name if
  their names shared enough tokens (`"PRACHI SAMEER SAWANT"` was being folded into `"YASH SAMEER
  SAWANT"` — different first names, same surname). The guard forces two names with unambiguously
  different, non-prefix-related first tokens into different clusters regardless of similarity score or
  shared UPI ID.
- **`merge_prefix_chains`** — safely auto-merges truncation-prefix variants (`"SAMEER B"` →
  `"SAMEER BALIRAM SAWA"`) that the existing fuzzy-similarity tiers miss (too dissimilar by
  length-penalized string metrics). Merges a variant into the single unambiguous longer form it chains
  into; if a short form is compatible with two or more genuinely different longer names in the same
  component (e.g. two different people both named `"MAHENDRA ..."`), it's correctly left unmerged rather
  than guessed.
- A small hand-curated `MANUAL_ALIASES` map in `build_unified_dataset.py` covers the residual cases that
  are unsafe to merge algorithmically but safe by real-world knowledge (e.g. `"BHARTI AIRTEL LI"` /
  `"AIRTEL PREPAID R"` are the same telecom company, not a name-prefix relationship) — kept deliberately
  small; not a general-purpose alias system.

Known residual gap: title-prefixed variants (`"Mr. SAMEER BALIRAM S"`) don't merge into their untitled
form, because `find_prefix_variants` generates candidates from raw string prefixes and a leading title
breaks that match. Low-impact (a handful of rows); not yet fixed.

## Known gaps / open questions

- **Review queue has never been labeled.** Most captured SMS is `UNKNOWN` — not because parsing is
  broken, but because most of a real inbox is promo/OTP/alert noise. A hand-labeling pass over
  `review_queue_sms.csv` is the highest-leverage next step: it both fixes any residual regex misses
  and produces the labeled set a supervised classifier would need.
- **Credit-side recipient is often blank** for one specific SBI format (`credited by Rs4 … by  (Ref no
  …)`) that omits the payer name entirely — a data limitation, not a parser bug. A different SBI IMPS
  format that does carry the name at the end (`... by a/c linked to mobile 9XXXXXX567-NAME (IMPS Ref
  no ...)`) was previously mis-extracted as "you" (boilerplate leakage) and is now fixed — see
  "Recipient extraction — 'you' bug" above.
- **UPI-ID extraction is ~2%.** Expected: SBI transaction SMS identify the counterparty by name/ref,
  not by VPA.
- **Single bank / account today** (SBI, one account). The parser is bank-general; multi-bank behavior
  is simply unverified until other banks' SMS appear in the data.
- **No supervised model yet** — classification is entirely rule-based. Layering a model over the
  `UNKNOWN` bucket needs the labeled sample above first.
- **Unified dataset needs categorization labels.** `SpendWise_Unified_Merchants.xlsx` has clean,
  backfilled recipient names but no `category` column — the existing `SpendWise_4yrs_Labelled.xlsx`
  (1,810 rows) is a separate, older labeling pass that needs re-linking to this dataset and has its own
  quality issue (its `Transfers` category is a catch-all: ~50% of all labels, mixing genuine
  person-to-person transfers with mislabeled real spending).
