import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from msoffcrypto.format.ooxml import OOXMLFile

from app.routes.statementUpload import router as statement_upload_router
from app.services import statementFileManager
from app.services.statementParser import normalize_statement_dataframe

app = FastAPI()
app.include_router(statement_upload_router)


@pytest.fixture(autouse=True)
def isolate_upload_dirs(tmp_path, monkeypatch):
    """Redirect raw/processed uploads to a pytest tmp_path for every test in this
    file, instead of the real data/statement_uploads/ folders. Without this, every
    test run leaves real files behind in production-data directories that were
    meant for actual user uploads, not test noise."""
    monkeypatch.setattr(statementFileManager, "RAW_UPLOAD_ROOT", tmp_path / "raw")
    monkeypatch.setattr(statementFileManager, "PROCESSED_UPLOAD_ROOT", tmp_path / "processed")

CSV_A = (
    "Date,Details,Debit,Credit,Balance\n"
    "01/01/2026,DEP TFR UPI/CR/123456/ALICE/HDFC/upi123 1001 AT,0,1200,1200\n"
)
CSV_B = (
    "Date,Details,Debit,Credit,Balance\n"
    "02/01/2026,DEP TFR UPI/CR/234567/BOB/HDFC/upix 1002 AT,0,500,1700\n"
)


def _plain_xlsx_with_letterhead() -> bytes:
    """A workbook shaped like a real SBI export: a letterhead block (name,
    email, account number) above the 'Date | ... | Balance' table."""
    rows = [
        ["Mr. Test User test@example.com", None, None, None, None, None],
        ["Account Number  :  41014247686", None, None, None, None, None],
        ["Date", "Details", "Ref No/Cheque No", "Debit", "Credit", "Balance"],
        ["01/01/2026", "DEP TFR UPI/CR/123456/ALICE/HDFC/upi123 1001 AT", None, None, 1200, 1200],
    ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, header=False)
    buf.seek(0)
    return buf.read()


def _encrypted_xlsx(password: str) -> bytes:
    plain = io.BytesIO(_plain_xlsx_with_letterhead())
    encrypted = io.BytesIO()
    office_file = OOXMLFile(plain)
    office_file.load_key(password=password)
    office_file.encrypt(password, encrypted)
    encrypted.seek(0)
    return encrypted.read()


def test_normalize_statement_dataframe_parses_statement_rows():
    raw_data = pd.DataFrame([
        {
            "Date": "01/01/2026",
            "Details": "DEP TFR UPI/CR/123456/ALICE/HDFC/upi123 1001 AT",
            "Debit": "0",
            "Credit": "1200",
            "Balance": "1200",
        }
    ])

    cleaned = normalize_statement_dataframe(raw_data)

    assert cleaned.loc[0, "Transaction_Date"] == "2026-01-01"
    assert cleaned.loc[0, "Recipient_Name"] == "ALICE"
    assert cleaned.loc[0, "Recipient_Canonical"] == "ALICE"
    assert cleaned.loc[0, "Amount"] == 1200.0


def test_upload_accepts_multiple_csv_files_and_returns_json():
    client = TestClient(app)

    response = client.post(
        "/statements/upload",
        files=[
            ("files", ("a.csv", CSV_A.encode("utf-8"), "text/csv")),
            ("files", ("b.csv", CSV_B.encode("utf-8"), "text/csv")),
        ],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["rows_processed"] == 2
    assert {row["Recipient_Name"] for row in payload["transactions"]} == {"ALICE", "BOB"}
    # Account-level metadata is returned once, not stamped onto every row.
    assert "account_metadata" in payload
    assert all("Account_Holder_Name" not in row for row in payload["transactions"])
    assert payload["processed_file"].startswith("cleaned_")
    assert payload["download_url"] == f"/statements/processed/{payload['processed_file']}"


def test_upload_accepts_plain_excel_and_skips_letterhead():
    client = TestClient(app)

    response = client.post(
        "/statements/upload",
        files=[("files", ("statement.xlsx", _plain_xlsx_with_letterhead(), "application/vnd.ms-excel"))],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["rows_processed"] == 1
    assert payload["transactions"][0]["Recipient_Name"] == "ALICE"
    # The letterhead's PII must never leak into a transaction row.
    row_text = str(payload["transactions"][0])
    assert "test@example.com" not in row_text
    assert "41014247686" not in row_text


def test_upload_encrypted_excel_with_correct_password():
    client = TestClient(app)
    encrypted_bytes = _encrypted_xlsx("correct-horse")

    response = client.post(
        "/statements/upload",
        files=[("files", ("protected.xlsx", encrypted_bytes, "application/vnd.ms-excel"))],
        data={"password": "correct-horse"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rows_processed"] == 1


def test_upload_encrypted_excel_missing_password_returns_400():
    client = TestClient(app)
    encrypted_bytes = _encrypted_xlsx("correct-horse")

    response = client.post(
        "/statements/upload",
        files=[("files", ("protected.xlsx", encrypted_bytes, "application/vnd.ms-excel"))],
    )

    assert response.status_code == 400
    assert "password" in response.json()["detail"].lower()


def test_upload_encrypted_excel_wrong_password_returns_400():
    client = TestClient(app)
    encrypted_bytes = _encrypted_xlsx("correct-horse")

    response = client.post(
        "/statements/upload",
        files=[("files", ("protected.xlsx", encrypted_bytes, "application/vnd.ms-excel"))],
        data={"password": "wrong-password"},
    )

    assert response.status_code == 400
    assert "password" in response.json()["detail"].lower()


def test_upload_rejects_unsupported_file_type():
    client = TestClient(app)

    response = client.post(
        "/statements/upload",
        files=[("files", ("statement.pdf", b"%PDF-1.4 not a real pdf", "application/pdf"))],
    )

    assert response.status_code == 400
    assert "supported" in response.json()["detail"].lower()


def test_upload_download_query_param_returns_csv_file():
    client = TestClient(app)

    response = client.post(
        "/statements/upload?download=true",
        files=[("files", ("a.csv", CSV_A.encode("utf-8"), "text/csv"))],
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "ALICE" in response.text


def test_download_processed_endpoint_round_trips():
    client = TestClient(app)

    upload_response = client.post(
        "/statements/upload",
        files=[("files", ("a.csv", CSV_A.encode("utf-8"), "text/csv"))],
    )
    processed_file = upload_response.json()["processed_file"]

    download_response = client.get(f"/statements/processed/{processed_file}")

    assert download_response.status_code == 200
    assert "ALICE" in download_response.text


def test_download_processed_endpoint_rejects_path_traversal():
    client = TestClient(app)

    response = client.get("/statements/processed/..%2F..%2F.env")

    assert response.status_code in (400, 404)
