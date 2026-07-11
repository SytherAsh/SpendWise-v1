# Vision

## What SpendWise is

A personal finance platform that ingests transaction data from multiple sources — bank-statement
exports (PDF/Excel) and SMS/notification capture from an Android app — cleans and structures it, and
will expose it for analytics on a website.

## Who it's for

{{TODO: currently a solo/personal-use project (the account holder's own SBI transactions). State here
if/when the scope broadens beyond personal use — e.g. multi-user, other banks, portfolio-demo
audience.}}

## Problem it solves

Bank and UPI-provider exports (SBI Excel statements, GPay/Paytm SMS notifications) are unstructured —
free-text narration fields with no clean merchant name, no category, and inconsistent formats across
providers. SpendWise unifies these into one structured transaction ledger so spending can actually be
analyzed.

## Success criteria

{{TODO: define what "done" looks like for the platform as a whole — e.g. "any SBI PDF or Excel
statement can be uploaded and produces a clean CSV with >X% merchant-extraction accuracy," "the
website shows unified spending analytics across SMS + statement sources." Fill in as these are
decided; the current concrete deliverable is described in CLAUDE.md's "Current task focus."}}

## Out of scope (for now)

- Full category classification (food/travel/bills/etc.) — see `CLAUDE.md`.
- Multi-bank support beyond SBI — the current Excel/PDF pipeline is being built against SBI exports
  first.
- The website/frontend itself — not yet started; `ml_service` + `ml_preprocessing` are the current
  focus.
