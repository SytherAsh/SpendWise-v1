# SpendWise Backend Services

## Goal
Build a personal finance platform that cleans bank-statement data, stores it in Supabase, and exposes transaction + analysis APIs through FastAPI. The system serves as a central layer to unify UPI transactions from providers like GPay and Paytm, and deliver accurate spending data using Regex/ML pipelines. Current focus: an ML pipeline that turns raw PDF/Excel bank-statement uploads into clean, structured CSVs (with merchant/recipient extraction) for website analytics — see `CLAUDE.md` for details.

## Project Structure
- **`ml_service/`**: Python FastAPI service. This handles data ingestion (raw SMS/Notifications from the Android app), runs preprocessing and regex matching to identify transaction details, and stores the structured data in Supabase.
- **`ml_preprocessing/`**: Jupyter Notebooks used for data cleaning, exploratory data analysis (EDA), and preparing the Regex/ML pipelines.

## API Summary

### FastAPI (`ml_service`) - Data Ingestion & Logic
- `GET /` - Health check
- `POST /api/data` - Single SMS/Notification ingestion (from Android)
- `POST /api/data/bulk` - Bulk SMS ingestion (from Android)
- `GET /api/data` - List raw SMS rows (Supabase `raw_sms`)
- `GET /api/data/stats/summary` - Ingestion stats summary
- `GET /api/data/last-sync` - Last-synced timestamp per device
- `POST /transactions` - Create transaction manually
- `GET /transactions?limit=50&offset=0` - List raw transactions
- `GET /transactions/{transaction_id}` - Get raw transaction
- `GET /transactions/{transaction_id}/logic` - Analyze transaction logic
- `POST /load-excel` - Bulk load transactions from CSV/Excel

## Supabase Schema
Run the following SQL in your Supabase project to initialize the database:

```sql
create extension if not exists pgcrypto;

create table if not exists accounts (
  id uuid primary key default gen_random_uuid(),
  bank_name text not null,
  account_type text,
  created_at timestamptz not null default now()
);

create table if not exists recipients (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  upi_id text,
  bank_name text,
  created_at timestamptz not null default now()
);

create table if not exists transactions (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references accounts(id),
  recipient_id uuid not null references recipients(id),
  transaction_reference text not null,
  transaction_date date,
  amount numeric(14,2),
  debit numeric(14,2),
  credit numeric(14,2),
  balance numeric(14,2),
  transaction_mode text,
  dr_cr_indicator text,
  note text,
  created_at timestamptz not null default now()
);

create index if not exists idx_accounts_bank_name on accounts(bank_name);
create index if not exists idx_recipients_upi_id on recipients(upi_id);
create index if not exists idx_transactions_account_id on transactions(account_id);
create index if not exists idx_transactions_recipient_id on transactions(recipient_id);
create index if not exists idx_transactions_created_at on transactions(created_at desc);
```

## Environment Setup

### FastAPI (`ml_service/.env`)
Create a `.env` file in the `ml_service` directory:
```env
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_anon_key
ACCOUNT_ID=default_account_uuid
```

## Running Locally

### Start the FastAPI Service
```bash
cd ml_service
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
*(Running on `0.0.0.0` allows the Android app on the same network to connect to it).*

For a comprehensive guide on how the whole system connects, see `docs/sms_pipeline.md`. For the current ML task scope, see `CLAUDE.md`.