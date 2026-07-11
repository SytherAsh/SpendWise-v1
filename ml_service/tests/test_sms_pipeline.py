from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.financial_sms_processor import FinancialSmsProcessor
from app.sms_parser import parse_sms_body


def test_financial_transaction_parsing_extracts_core_fields():
    body = "Dear UPI user A/C X7686 debited by 120 on date 01Jan26 trf to PRADHAN MANTRI B Refno 600132505284"
    parsed = parse_sms_body(body, "JD-SBIUPI-S")

    assert parsed.classification_label == "FINANCIAL_TRANSACTION"
    assert parsed.is_financial is True
    assert parsed.amount == 120.0
    assert parsed.direction == "DEBIT"
    assert parsed.bank == "SBI"
    assert parsed.ref_id == "600132505284"
    assert parsed.recipient_name == "PRADHAN MANTRI B"


def test_otp_messages_are_filtered_and_labeled():
    body = "Your OTP for login is 482913. Do not share this verification code with anyone."
    parsed = parse_sms_body(body, "BANKSMS")

    assert parsed.classification_label == "OTP"
    assert parsed.is_financial is False


def test_promotional_messages_are_filtered():
    body = "Get 50% cashback on your next recharge. Limited time offer, apply now!"
    parsed = parse_sms_body(body, "PROMO-SMS")

    assert parsed.classification_label == "PROMOTIONAL"
    assert parsed.is_financial is False


def test_banking_alert_messages_are_filtered():
    body = "Security alert: your card was blocked. Please contact support to unblock it."
    parsed = parse_sms_body(body, "HDFCBK")

    assert parsed.classification_label == "BANKING_ALERT"
    assert parsed.is_financial is False


def test_failed_transactions_are_filtered():
    body = "Your UPI payment of Rs. 500 failed due to network error and was reversed."
    parsed = parse_sms_body(body, "SBIUPI")

    assert parsed.classification_label == "FAILED_TRANSACTION"
    assert parsed.is_financial is False


def test_bill_reminders_are_filtered():
    body = "Reminder: your electricity bill payment is due by 10 June."
    parsed = parse_sms_body(body, "UTILITYSMS")

    assert parsed.classification_label == "BILL_REMINDER"
    assert parsed.is_financial is False


def test_unknown_message_stays_unknown_when_amount_or_direction_is_missing():
    body = "Your account statement is available online."
    parsed = parse_sms_body(body, "HDFCBK")

    assert parsed.classification_label in {"BANKING_ALERT", "UNKNOWN"}
    assert parsed.is_financial is False


def test_recipient_is_cleaned_for_merchant_style_sms():
    body = "Dear UPI user A/C X7686 debited by 89.00 on date 05May26 trf to Rapido Pvt Ltd India Refno 123456789012"
    parsed = parse_sms_body(body, "JD-SBIUPI-S")

    assert parsed.classification_label == "FINANCIAL_TRANSACTION"
    assert parsed.recipient_name == "Rapido"


def test_processor_creates_clean_financial_and_unknown_outputs(tmp_path: Path):
    input_file = tmp_path / "captured_sms.csv"
    clean_file = tmp_path / "clean_sms_eda.csv"
    financial_file = tmp_path / "true_financial_sms.csv"
    unknown_file = tmp_path / "unknown_sms.csv"

    rows = [
        {
            "id": "1",
            "sender": "JD-SBIUPI-S",
            "body": "Dear UPI user A/C X7686 debited by 120 on date 01Jan26 trf to PRADHAN MANTRI B Refno 600132505284",
            "timestamp_ms": "1767283633363",
            "timestamp_human": "2026-01-01 21:37:13",
            "device_id": "device-1",
            "is_financial": True,
            "amount": 120.0,
            "direction": "DEBIT",
            "bank": "SBI",
            "upi_id": "",
            "recipient": "PRADHAN MANTRI B",
        },
        {
            "id": "1",
            "sender": "JD-SBIUPI-S",
            "body": "Dear UPI user A/C X7686 debited by 120 on date 01Jan26 trf to PRADHAN MANTRI B Refno 600132505284",
            "timestamp_ms": "1767283633363",
            "timestamp_human": "2026-01-01 21:37:13",
            "device_id": "device-1",
            "is_financial": True,
            "amount": 120.0,
            "direction": "DEBIT",
            "bank": "SBI",
            "upi_id": "",
            "recipient": "PRADHAN MANTRI B",
        },
        {
            "id": "2",
            "sender": "RANDOM-SENDER",
            "body": "Reference update Rs. 42 processed.",
            "timestamp_ms": "1767283633364",
            "timestamp_human": "2026-01-01 21:37:14",
            "device_id": "device-1",
            "is_financial": False,
            "amount": None,
            "direction": None,
            "bank": None,
            "upi_id": None,
            "recipient": None,
        },
    ]

    pd.DataFrame(rows).to_csv(input_file, index=False)

    processor = FinancialSmsProcessor(
        input_file=str(input_file),
        output_file=str(clean_file),
        financial_output_file=str(financial_file),
        unknown_output_file=str(unknown_file),
    )

    summary = processor.process_all(push_to_supabase=False)

    assert summary is not None
    assert summary["total_records"] == 2
    assert summary["financial_count"] == 1
    assert clean_file.exists()
    assert financial_file.exists()
    assert unknown_file.exists()

    financial_df = pd.read_csv(financial_file)
    unknown_df = pd.read_csv(unknown_file)

    assert len(financial_df) == 1
    assert financial_df.iloc[0]["classification_label"] == "FINANCIAL_TRANSACTION"
    assert len(unknown_df) >= 1
    assert set(unknown_df.columns) == set(["body", "sender", "predicted_label", "confidence", "review_status", "true_label"])
