# pyrefly: ignore [missing-import]
from fastapi import APIRouter


from app.parsers.excel_loader import load_transactions_from_excel
from app.services.persistence import create_single_transaction


router = APIRouter()
EXCEL_PATH = "data/SpendWise2k26.xlsx"


@router.post("/load-excel")
def load_excel():
    transactions = load_transactions_from_excel(EXCEL_PATH)

    inserted = 0
    failed = 0
    errors = []

    for tx in transactions:
        try:
            create_single_transaction(tx)
            inserted += 1
        except Exception as e:
            failed += 1
            errors.append(str(e))

    return {
        "rows_processed": len(transactions),
        "inserted": inserted,
        "failed": failed,
        "errors": errors
    }