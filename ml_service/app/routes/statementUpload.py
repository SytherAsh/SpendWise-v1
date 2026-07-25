import logging
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.parsers.statementLoader import StatementLoadError, load_statement_data, validate_file_type
from app.services.statementFileManager import (
    resolve_processed_file,
    save_processed_csv,
    save_raw_upload,
)
from app.services.statementParser import normalize_statement_dataframe

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/statements/upload")
async def upload_statements(
    files: List[UploadFile] = File(..., description="Bank statement exports (.csv, .xls, .xlsx, .xlsm, .xlsb)."),
    # Deliberately a form field, not a query parameter: query strings are routinely
    # recorded in server access logs, proxy logs and browser history, so a bank
    # statement password passed that way would be written to disk in plaintext.
    password: Optional[str] = Form(None, description="Password, if the workbook is protected."),
    download: bool = Query(False, description="Return the cleaned CSV as a file download instead of JSON."),
):
    """Upload one or more bank statements and get back model-ready clean transactions.

    Raw uploads are preserved under data/statement_uploads/raw/ and the single
    cleaned result under data/statement_uploads/processed/. Intermediate parsing
    stages are not written to disk -- use ml_service/tests/test_pipeline.py
    --interactive for step-by-step inspection.
    """
    payload = []
    saved_raw_files = []

    for upload in files:
        filename = upload.filename or "statement"
        try:
            validate_file_type(filename)
        except StatementLoadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        content = await upload.read()
        if not content:
            continue

        saved_path = save_raw_upload(filename, content)
        saved_raw_files.append(saved_path.name)
        payload.append((filename, content))

    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file(s) are empty.")

    try:
        raw_df, account_metadata = load_statement_data(payload, password=password)
    except StatementLoadError as exc:
        # Password/file-type problems are the caller's to fix, not server faults.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to parse uploaded statement(s)")
        raise HTTPException(status_code=422, detail=f"Could not parse the statement file(s): {exc}") from exc

    if raw_df.empty:
        raise HTTPException(
            status_code=422,
            detail="No transaction rows were found. Check that this is a bank statement export.",
        )

    cleaned_df = normalize_statement_dataframe(raw_df)
    cleaned_path = save_processed_csv(
        cleaned_df, files[0].filename if files else None, account_metadata=account_metadata
    )

    if download:
        return FileResponse(
            path=cleaned_path,
            media_type="text/csv",
            filename=cleaned_path.name,
        )

    return {
        "rows_processed": len(cleaned_df),
        "columns": cleaned_df.columns.tolist(),
        # Account-level, so returned once here rather than stamped onto every row.
        "account_metadata": account_metadata,
        "saved_raw_files": saved_raw_files,
        "processed_file": cleaned_path.name,
        "download_url": f"/statements/processed/{cleaned_path.name}",
        "transactions": cleaned_df.to_dict(orient="records"),
    }


@router.get("/statements/processed/{file_name}")
def download_processed_statement(file_name: str):
    """Download a previously produced cleaned CSV by name."""
    try:
        path = resolve_processed_file(file_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No processed file named '{file_name}'.")

    return FileResponse(path=path, media_type="text/csv", filename=path.name)
