# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException, Query

from app.schemas.transaction import TransactionCreate
from app.services.persistence import (
    create_single_transaction,
    get_transaction_by_id,
    list_transactions,
    get_transaction_logic,
)

router = APIRouter(prefix="/transactions")


@router.post("")
def create_transaction(payload: TransactionCreate):
    created = create_single_transaction(payload.model_dump())
    return {
        "message": "Transaction created",
        "transaction": created,
    }


@router.get("")
def get_transactions(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    items = list_transactions(limit=limit, offset=offset)
    return {
        "count": len(items),
        "items": items,
    }


@router.get("/{transaction_id}")
def get_transaction(transaction_id: str):
    transaction = get_transaction_by_id(transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@router.get("/{transaction_id}/logic")
def get_logic(transaction_id: str):
    transaction = get_transaction_by_id(transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return get_transaction_logic(transaction)