# SpendWise Transaction Analysis Report

This report turns the transaction notebook and source files into a clean, GPT-friendly summary for deeper analysis.

Note: the notebook source in `ml_preprocessing/Analyse.ipynb` had not been executed in the workspace, so this report is reconstructed from the underlying data files in `ml_preprocessing/CSVS`.

## Data Sources

| File | Rows | Columns | Date Range |
| --- | ---: | ---: | --- |
| `SpendWise2k26.xlsx` | 1,653 | 12 | 2023-04-01 to 2026-03-14 |
| `true_financial_sms.csv` | 68 | 16 | 2026-01-01 to 2026-05-09 |

## Workbook Schema

The consolidated bank-statement workbook contains these fields:

`Transaction_Date`, `Debit`, `Credit`, `Balance`, `Transaction_Mode`, `DR/CR_Indicator`, `Transaction_ID`, `Recipient_Name`, `Bank`, `UPI_ID`, `Note`, `Amount`

## Core Workbook Summary

| Metric | Value |
| --- | ---: |
| Total transactions | 1,653 |
| Debit entries | 1,294 |
| Credit entries | 359 |
| Total debit amount | 642,299.95 |
| Total credit amount | 643,826.54 |
| Net signed amount | 1,526.59 |
| Largest single debit | 150,000.00 |
| Largest single credit | 150,000.00 |

## Data Quality Notes

| Field | Null Count |
| --- | ---: |
| `Transaction_Mode` | 36 |
| `Bank` | 180 |
| `UPI_ID` | 180 |
| `Note` | 933 |

All other workbook columns are fully populated.

## Transaction Mode Breakdown

| Mode | Count |
| --- | ---: |
| UPI | 1,481 |
| INB | 86 |
| IMP | 41 |
| NEFT | 4 |
| POS | 3 |
| ACH | 2 |
| Missing | 36 |

## Recipient and Counterparty Concentration

### Top Debit Recipients

| Recipient | Debit Total |
| --- | ---: |
| PHONE_TRANSFER | 193,500.00 |
| SPIT | 99,069.80 |
| SAMEER B | 34,452.00 |
| UNKNOWN | 32,708.30 |
| MANOEUVR | 15,000.00 |
| Pratham | 13,187.20 |
| ABHIJIT | 11,200.00 |
| Mr Vihaa | 10,064.00 |
| M | 10,000.00 |
| SNOW CRE | 6,684.00 |

### Top Credit Recipients

| Recipient | Credit Total |
| --- | ---: |
| PHONE_TRANSFER | 220,884.00 |
| CHEQUE   CSB    400047008-100116 400002538 | 150,000.00 |
| UNKNOWN | 126,294.00 |
| PRACHI S | 39,790.00 |
| DIGAMBER | 31,600.00 |
| NEHA NIT | 10,000.00 |
| RUCHA NI | 7,702.00 |
| Mr Vihaa | 6,734.00 |
| SAKSHI S | 5,000.00 |
| SHUBHAM | 4,924.06 |

## Yearly Breakdown

| Year | Transactions | Debit Total | Credit Total | Net Amount | Debit Count | Credit Count |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2023 | 306 | 34,536.89 | 35,062.33 | 525.44 | 236 | 70 |
| 2024 | 515 | 346,002.51 | 346,631.12 | 628.61 | 385 | 130 |
| 2025 | 596 | 125,983.68 | 126,348.09 | 364.41 | 460 | 136 |
| 2026 | 236 | 135,776.87 | 135,785.00 | 8.13 | 213 | 23 |

## Monthly Breakdown

| Month | Transactions | Debit Total | Credit Total | Net Amount |
| --- | ---: | ---: | ---: | ---: |
| 2023-04 | 47 | 5,355.99 | 6,253.33 | 897.34 |
| 2023-05 | 54 | 3,640.00 | 3,537.00 | -103.00 |
| 2023-06 | 42 | 3,901.20 | 4,390.00 | 488.80 |
| 2023-07 | 11 | 5,096.00 | 5,115.00 | 19.00 |
| 2023-08 | 40 | 5,210.70 | 4,301.00 | -909.70 |
| 2023-09 | 23 | 2,952.00 | 2,578.00 | -374.00 |
| 2023-10 | 32 | 2,544.00 | 2,970.00 | 426.00 |
| 2023-11 | 26 | 2,602.00 | 2,850.00 | 248.00 |
| 2023-12 | 31 | 3,235.00 | 3,068.00 | -167.00 |
| 2024-01 | 41 | 4,329.52 | 4,230.00 | -99.52 |
| 2024-02 | 32 | 3,823.00 | 4,065.00 | 242.00 |
| 2024-03 | 44 | 9,675.00 | 9,877.00 | 202.00 |
| 2024-04 | 71 | 23,302.49 | 25,314.12 | 2,011.63 |
| 2024-05 | 34 | 8,831.97 | 6,100.00 | -2,731.97 |
| 2024-06 | 69 | 9,147.47 | 10,340.00 | 1,192.53 |
| 2024-07 | 25 | 103,358.80 | 102,500.00 | -858.80 |
| 2024-08 | 33 | 5,882.02 | 5,675.00 | -207.02 |
| 2024-09 | 26 | 2,627.96 | 2,405.00 | -222.96 |
| 2024-10 | 35 | 7,733.00 | 8,570.00 | 837.00 |
| 2024-11 | 35 | 3,479.74 | 2,650.00 | -829.74 |
| 2024-12 | 70 | 163,811.54 | 164,905.00 | 1,093.46 |
| 2025-01 | 48 | 10,620.69 | 9,645.00 | -975.69 |
| 2025-02 | 55 | 8,148.96 | 13,130.00 | 4,981.04 |
| 2025-03 | 46 | 11,263.82 | 6,412.00 | -4,851.82 |
| 2025-04 | 47 | 9,284.96 | 8,906.00 | -378.96 |
| 2025-05 | 32 | 2,630.06 | 2,700.00 | 69.94 |
| 2025-06 | 32 | 5,545.00 | 5,960.00 | 415.00 |
| 2025-07 | 40 | 4,477.99 | 4,522.00 | 44.01 |
| 2025-08 | 60 | 8,080.20 | 7,802.00 | -278.20 |
| 2025-09 | 37 | 3,978.70 | 4,028.10 | 49.40 |
| 2025-10 | 26 | 11,033.00 | 12,300.00 | 1,267.00 |
| 2025-11 | 103 | 32,014.96 | 34,080.50 | 2,065.54 |
| 2025-12 | 70 | 18,905.34 | 16,862.49 | -2,042.85 |
| 2026-01 | 88 | 44,543.49 | 52,662.00 | 8,118.51 |
| 2026-02 | 98 | 67,976.38 | 81,065.00 | 13,088.62 |
| 2026-03 | 50 | 23,257.00 | 2,058.00 | -21,199.00 |

## SMS Financial File Summary

| Metric | Value |
| --- | ---: |
| Total rows | 68 |
| Financial debit messages | 36 |
| Financial credit messages | 32 |
| Amount total | 349,577.42 |
| Minimum amount | 1.00 |
| Maximum amount | 75,000.00 |
| Reference-ID overlap with workbook | 13 |

### SMS Bank Distribution

| Bank | Count |
| --- | ---: |
| SBI | 60 |
| Missing | 8 |

### SMS Debit Recipients With Clean Labels

| Recipient | Amount |
| --- | ---: |
| Sameer Ac x4470 dt 26.02.26 | 20,000.00 |
| Mrs. PRACHI SAMEER S. Avl Balance IN | 20,000.00 |
| Prachi Ac x3861 dt 26.03.26 | 10,000.00 |
| Prachi Ac x3861 dt 26.02.26 | 10,000.00 |
| SAMEER BALIRAM SAWANT | 5,000.00 |
| Sameer Ac x4470 dt 30.03.26 | 3,000.00 |
| block your card. Call 18001234 if ca | 2,900.00 |
| Mr Vihaan Sachin | 2,000.00 |
| KEWAL BIREN NANA | 1,487.00 |
| Sameer Ac x8237 dt 04.04.26 | 1,000.00 |

## Data Quality Observations

- The workbook is dense and mostly clean, but `Note`, `Bank`, `UPI_ID`, and `Transaction_Mode` contain missing values.
- `PHONE_TRANSFER` dominates both debit and credit totals, so it should be treated as a special internal-transfer bucket rather than a normal merchant.
- The SMS file is much smaller and later in time than the workbook, which suggests it is a validation or enrichment source rather than a full transaction ledger.
- Seven SMS debit rows do not have a usable recipient label, with a combined amount of 77,080.42.

## Suggested Follow-Up Questions For GPT

1. Which merchants or recipient clusters explain the largest monthly spikes, especially July 2024, December 2024, November 2025, January 2026, and February 2026?
2. How much of the workbook activity is internal transfer flow versus external spend?
3. Can the recipient names be normalized into higher-level categories such as food, travel, transfers, savings, bills, and personal expenses?
4. Are there suspicious or one-off large-value transactions that deserve manual review?
