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
4. **Transaction dedup** (`dedup_transactions`) — among financial rows, collapse cross-channel
   duplicates on `(amount, direction, 2-minute bucket)`, keeping the row with a ref_id, then higher
   confidence (i.e. prefer the authoritative bank SMS over an app-notification twin). The window is
   tight **on purpose**: the same amount recurring on a different day (e.g. a monthly recharge) is a
   distinct transaction and must not be merged — a same-day key would wrongly collapse those.
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
(`ml_preprocessing/CSVS/SpendWise_4yrs_Clean_Merchants.xlsx`) by reference ID: on the 837 transactions
present in both, **every amount matched exactly**. This is the strongest available correctness signal
and specifically validates the amount-vs-balance fix. Re-run it whenever the parser changes materially.

## Known gaps / open questions

- **Review queue has never been labeled.** Most captured SMS is `UNKNOWN` — not because parsing is
  broken, but because most of a real inbox is promo/OTP/alert noise. A hand-labeling pass over
  `review_queue_sms.csv` is the highest-leverage next step: it both fixes any residual regex misses
  and produces the labeled set a supervised classifier would need.
- **Credit-side recipient is often blank** (~40% of credits). This is a data limitation, not a parser
  bug — the SBI credit format literally omits the payer name (`credited by Rs4 … by  (Ref no …)`).
- **UPI-ID extraction is ~2%.** Expected: SBI transaction SMS identify the counterparty by name/ref,
  not by VPA.
- **Single bank / account today** (SBI, one account). The parser is bank-general; multi-bank behavior
  is simply unverified until other banks' SMS appear in the data.
- **No supervised model yet** — classification is entirely rule-based. Layering a model over the
  `UNKNOWN` bucket needs the labeled sample above first.
