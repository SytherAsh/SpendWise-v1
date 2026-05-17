import pandas as pd
import math
import numpy as np

def load_transactions_from_excel(file_path: str):
    df = pd.read_excel(file_path)
    # df = df.iloc[:10]  <-- Removed limit for full migration

   
    transactions = []
   
    for _, row in df.iterrows():

        debit = row["Debit"] or 0
        credit = row["Credit"] or 0
        balance = row["Balance"] or 0
        bank = row["Bank"] or ""
        upi_id = row["UPI_ID"] or ""
        note = row["Note"] or ""
        
        transaction = {
            "transaction_reference": str(row["Transaction_ID"]),
            "transaction_date": str(row["Transaction_Date"]),
            "amount": row.get("Amount"),
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "transaction_mode": row.get("Transaction_Mode", "OTHER"),
            "dr_cr_indicator": row.get("DR/CR_Indicator"),
            "note": note,
            "recipient_name": row.get("Recipient_Name"),
            "bank": bank,
            "upi_id": upi_id
        }

        transactions.append(transaction)

    return transactions