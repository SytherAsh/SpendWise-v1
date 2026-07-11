# SpendWise Full System Walkthrough

Welcome to the SpendWise project! This guide covers the full architecture, what each component does, and how to run and test the system end-to-end. This document reflects the **current stable state** of the pipeline as of May 2026.

---

## 1. High-Level Architecture

SpendWise uses a layered pipeline to go from raw phone SMS/notifications all the way to structured, queryable financial data.

```
[ Android App ]
      │  POST /api/data  (live) / POST /api/data/bulk  (batch)
      ▼
[ FastAPI ML Service ]  ←──── sms_parser.py  (real-time regex)
      │                 ←──── financial_sms_processor.py  (offline EDA cleanup)
      │
      ├──► captured_sms.csv        (raw append-only log)
      ├──► clean_sms_eda.csv       (deduplicated + parsed, all sources)
      └──► true_financial_sms.csv  (strictly financial, 2026-only)
      │
      ▼
[ Supabase PostgreSQL ]   ←── Excel bulk-load via /load-excel (historical)
```

### Component Roles

| Component | Role |
|---|---|
| **Android App** | Captures SMS & app notifications from banking/payment apps. Stores records locally (Room DB) and syncs to FastAPI. |
| **FastAPI ML Service** | The brain. Receives raw text, parses it with `sms_parser.py`, appends to CSV. Also exposes a bulk Excel-load endpoint for historical data. |
| **`sms_parser.py`** | Real-time per-message parser. Extracts Amount, Direction, Bank, UPI ID, Mode, Account Suffix, Balance, Recipient from a single SMS body. |
| **`FinancialSmsProcessor`** | Offline batch processor. Runs on the full `captured_sms.csv` to deduplicate, filter to 2026, apply cross-platform deduplication (2-minute window), and produce clean EDA files. |
| **Supabase (PostgreSQL)** | Cloud DB. Stores structured `accounts`, `recipients`, `transactions` tables. Currently used mainly for Excel bulk-load and the transaction/bulk routes. |

---

## 2. Project Directory Structure

```
SpendWise/
├── ml_preprocessing/               # Offline EDA notebooks
│   ├── EDA.ipynb                   # Exploratory data analysis
│   ├── Raw_SmS.ipynb               # Raw SMS inspection
│   ├── Segregation.ipynb           # Data segregation experiments
│   └── CSVS/                       # Historical Excel exports
│       ├── 2024.xlsx
│       ├── 2025.xlsx
│       ├── 2026.xlsx
│       ├── SpendWise2k26.xlsx      # Master 2026 dataset (used by /load-excel)
│       └── captured_sms.csv        # Full raw dump from Android app
│
├── ml_service/                     # FastAPI Python backend
│   ├── app/
│   │   ├── main.py                 # FastAPI app, router registration
│   │   ├── sms_parser.py           # Real-time SMS regex parser
│   │   ├── service.py              # Supabase CRUD helpers
│   │   ├── supabase_client.py      # Supabase client init
│   │   ├── excel_loader.py         # Reads SpendWise2k26.xlsx for bulk load
│   │   ├── schemas/
│   │   │   └── transaction.py      # Pydantic models: SmsPayload, ParsedTransaction, TransactionCreate
│   │   └── routes/
│   │       ├── ingest.py           # /api/data  (single + bulk SMS ingest → CSV)
│   │       ├── bulk.py             # /load-excel (historical Excel → Supabase)
│   │       ├── transaction.py      # /transactions CRUD
│   │       └── categorize.py       # /categorize
│   │
│   ├── financial_sms_processor.py  # Offline batch cleanup script
│   ├── captured_sms.csv            # Live append-only CSV (written by /api/data)
│   ├── clean_sms_eda.csv           # Output: cleaned, deduplicated all records
│   ├── true_financial_sms.csv      # Output: only confirmed financial, 2026
│   ├── data/
│   │   ├── SpendWise2k26.xlsx      # Historical data for /load-excel
│   │   └── migration_raw_notifications.sql
│   └── .env                        # SUPABASE_URL, SUPABASE_KEY, ACCOUNT_ID
│
└── walkthrough.md                  # This file
```

---

## 3. Setting Up the Database (Supabase)

> **Note:** The live SMS ingestion pipeline writes to local CSVs and does **not** currently require Supabase. Supabase is needed only for the Excel bulk-load route.

1. Create a free project on [Supabase](https://supabase.com/).
2. Go to the **SQL Editor** and run the schema from `README.md` to create `accounts`, `recipients`, and `transactions` tables.
3. Copy your **Project URL**, **API Key**, and **Postgres connection string** from Supabase settings.

---

## 4. Starting the FastAPI ML Service

This is the **core backend** that the Android app talks to.

```bash
cd Desktop/Journey/SpendWise/ml_service
```

```bash
# One-time setup
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in `ml_service/`:
```env
SUPABASE_URL=your_project_url
SUPABASE_KEY=your_api_key
ACCOUNT_ID=some_uuid_for_default_account
```

Start the server:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

> Binding to `0.0.0.0` is critical — it lets your phone reach the server over Wi-Fi using your PC's local IP.

### Available API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Health check |
| `POST` | `/api/data` | Ingest a **single** SMS → appends to `captured_sms.csv` |
| `POST` | `/api/data/bulk` | Ingest a **batch** of SMS → batch-appends to `captured_sms.csv` |
| `GET` | `/api/data` | List raw records (reads Supabase `raw_sms` table) |
| `GET` | `/api/data/{id}` | Get a specific raw record |
| `GET` | `/api/data/stats/summary` | Dashboard stats |
| `POST` | `/api/data/{id}/reparse` | Re-parse an old record with the latest parser |
| `POST` | `/load-excel` | Bulk-load `data/SpendWise2k26.xlsx` → Supabase |
| `GET` | `/transactions` | List structured transactions from Supabase |
| `POST` | `/transactions` | Create a transaction manually |
| `GET` | `/transactions/{id}` | Get single transaction |
| `GET` | `/transactions/{id}/logic` | Get categorized logic (DEBIT/CREDIT, size bucket) |

---

## 5. The Ingestion Pipeline (How SMS Data Flows)

### 5a. Live Path — Real-Time SMS from Android App

```
Android App  →  POST /api/data  (SmsPayload)
                     │
                     ▼
             parse_sms_body()   [sms_parser.py]
             ┌─────────────────────────────────┐
             │  Checks FINANCIAL_KEYWORDS       │
             │  Extracts amount (Rs./INR/₹)     │
             │  Detects DEBIT / CREDIT          │
             │  Maps sender → bank name         │
             │  Extracts UPI ID                 │
             │  Detects mode (UPI/IMPS/NEFT…)  │
             │  Extracts account suffix (XXXX)  │
             │  Extracts balance after txn       │
             │  Extracts recipient name          │
             └─────────────────────────────────┘
                     │
                     ▼
             Appended to captured_sms.csv
             Returns IngestResponse (JSON)
```

The `SmsPayload` schema fields:
- `id` — unique UUID from the phone
- `source` — always `"sms"`
- `sender` — SMS sender code (e.g. `HDFCBK`)
- `body` — full SMS text
- `timestamp_ms` — Unix ms or date string
- `timestamp_human` — readable timestamp
- `device_id` — phone identifier

### 5b. Batch Path — Historical Sync from Android App

When the Android app syncs a large backlog, it POSTs to `/api/data/bulk` with a JSON array of `SmsPayload` objects. Each record is parsed and all valid records are **batch-written** to `captured_sms.csv` in a single file operation.

---

## 6. Offline Batch Cleanup — `FinancialSmsProcessor`

After accumulating data in `captured_sms.csv`, run the processor to produce clean output files for EDA and analysis.

```bash
# From ml_service/ directory
python financial_sms_processor.py
```

### What It Does (in order)

1. **Load** `captured_sms.csv`
2. **Exact dedup** — drop rows with same `body` + `timestamp_ms`
3. **Parse timestamps** — handles both epoch-ms integers and ISO date strings
4. **Filter to 2026** — drops all records not from the current year
5. **Sort** by parsed datetime ascending
6. **Regex parse** each row:
   - Extracts `parsed_amount`, `parsed_direction`, `parsed_ref_id`, `parsed_entity`
   - Detects spam/ads (telecom keywords, known ad-senders like AIRTEL/JIO/ZUDIO)
   - Sets `parsed_is_financial = True` only if: amount > 0 AND direction found AND NOT ad
7. **Cross-platform dedup** — groups by `(amount, direction, 2-minute time bucket)` to collapse SMS + notification duplicates from the same transaction
8. **Save outputs:**
   - `clean_sms_eda.csv` — all deduplicated records with parsed fields
   - `true_financial_sms.csv` — only rows where `is_financial == True`

### Output Files

| File | Contents |
|---|---|
| `captured_sms.csv` | Raw append-only log from Android (never cleaned) |
| `clean_sms_eda.csv` | All records: deduped, parsed, 2026-filtered |
| `true_financial_sms.csv` | Strictly financial records only |

---

## 7. Excel Bulk-Load (Historical Data)

For loading historical bank statement data (Excel exports) into Supabase:

1. Place your Excel file at `ml_service/data/SpendWise2k26.xlsx`
2. The file must have columns: `Transaction_ID`, `Transaction_Date`, `Amount`, `Debit`, `Credit`, `Balance`, `Bank`, `UPI_ID`, `Note`, `Transaction_Mode`, `DR/CR_Indicator`, `Recipient_Name`
3. Hit the endpoint:
   ```bash
   curl -X POST http://localhost:8000/load-excel
   ```
   Returns: `rows_processed`, `inserted`, `failed`, `errors[]`

> Current `excel_loader.py` loads only the first 10 rows (`df.iloc[:10]`). Increase this slice for a full load.

---

## 8. Running the Android App

1. Open the **Android Studio** project at `AndroidStudioProjects/SpendWise`.
2. Connect a physical Android device (recommended) or start an Emulator. Build and run.
3. On the app dashboard, tap **Show** under System Settings.
4. Grant **SMS Permission** and **Notification Access**.
5. Find your PC's local IPv4 address (e.g. `192.168.1.x`).
6. In the app's **Backend URL** field, enter:
   ```
   http://192.168.1.x:8000/api/data
   ```
7. Tap **Save & Test**. The API status indicator should turn **Green**.

---

## 9. The Complete End-to-End Flow

```
1. You make a UPI payment (e.g. Google Pay → HDFC Bank)

2. Your bank sends an SMS:
   "Rs.500 debited from A/c XX1234 to user@oksbi. Avl Bal Rs.12,345.67"

3. Android App detects the SMS, stores it in Room DB,
   POSTs to FastAPI:  POST /api/data

4. FastAPI → parse_sms_body():
   - is_financial = True
   - amount = 500.0
   - direction = "DEBIT"
   - bank = "HDFC"
   - upi_id = "user@oksbi"
   - account_suffix = "1234"
   - balance_after = 12345.67
   - mode = None (UPI inferred from upi_id)

5. Record appended to  ml_service/captured_sms.csv

6. [Later, offline]  python financial_sms_processor.py
   - Deduplicates SMS + notification duplicates
   - Filters to 2026
   - Outputs clean_sms_eda.csv + true_financial_sms.csv

7. [Optional] POST /load-excel  → loads historical Excel → Supabase
```

---

## 10. Current Status & Known Limitations

| Area | Status |
|---|---|
| Live SMS ingestion → CSV | ✅ Production-stable |
| Bulk batch sync → CSV | ✅ Production-stable |
| Cross-platform dedup (2-min window) | ✅ Working |
| 2026-only filtering | ✅ Active in `FinancialSmsProcessor` |
| Supabase write on live ingest | ⚠️ Disabled (commented out) — CSV-only mode |
| Excel bulk-load → Supabase | ✅ Working (first 10 rows; adjust slice for full load) |
| `sms_parser.py` year filter | ℹ️ `is_valid_year()` helper exists; called by processor, not live route |
| EDA notebooks | ✅ In `ml_preprocessing/` |
