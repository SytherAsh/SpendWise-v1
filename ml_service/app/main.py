import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.bulk import router as bulk_router
from app.routes.transaction import router as transactions_router
from app.routes.categorize import router as categorize_router
from app.routes.ingest import router as ingest_router
from app.routes.statementUpload import router as statement_upload_router

# Configure logging so all modules output to console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

app = FastAPI(
    title="SpendWise ML Service",
    description="Ingests notifications/SMS from the SpendWise Android app, "
                "parses financial transactions, and provides categorization.",
    version="2.0.0",
)

# Allow the Android app to call this from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {"status": "running", "service": "SpendWise ML Service"}


# Existing routers
app.include_router(bulk_router, tags=["bulk"])
app.include_router(transactions_router, tags=["transactions"])
app.include_router(categorize_router, tags=["categorization"])
app.include_router(statement_upload_router, tags=["statements"])

# New router for Android app notification/SMS ingest
app.include_router(ingest_router, tags=["ingest"])