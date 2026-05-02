# Fix Notes: Ragged OCBC CSV Parsing (v1.0.2)

## Symptom

Direct upload of OCBC online-banking CSVs failed with:

```
Error tokenizing data. C error: Expected 2 fields in line 6, saw 5
```

Opening the file in Excel and re-saving as CSV made it parse successfully.

## Root Cause

OCBC's CSV exports are **ragged** — different rows have different numbers of fields:

- The first few lines are key-value metadata (`Account Number,1234567890`) — **2 fields each**
- A blank separator line follows
- The transaction header (`Transaction date,Description,Cheque No,Withdrawals,Deposits`) — **5 fields**
- Transaction rows — **5+ fields** (some have extra trailing commas)

Pandas locks in the column count from the first non-empty line. When line 1 has 2 fields and line 6 has 5, the C parser raises a `ParserError`.

Excel tolerates ragged rows on read, then writes back a rectangular CSV (every row padded to the widest line) on save. That's the only reason "open and save in Excel" appeared to fix the file — Excel was silently rectangularising it.

## Why the Obvious Fixes Didn't Work

- **`engine="python"`** — also locks column count from line 1. Same error class, different code path.
- **`engine="python", on_bad_lines="skip"`** — skipped the metadata lines (good), but also skipped the transaction header line (bad), because by the time pandas reached it, "5 fields" was already "more than expected".
- **Naive line-splitting and padding in Python** — broke when transaction descriptions contained embedded newlines inside quoted fields.

## The Fix

Pre-process the bytes with the stdlib `csv` module before handing them to pandas. The `csv` reader respects quoted fields and embedded newlines correctly. Find the maximum field count across all rows, pad every shorter row with empty trailing fields, then re-emit a rectangular CSV that pandas reads without complaint.

Implementation lives in `_rectangularise_csv()` in `parsers/format_b.py`.

## Why This Is the Right Layer

The fix is contained inside Format B's parser, which is the right scope:

- Other formats (A, C, D, E) don't have this problem and don't need the same treatment.
- The bytes-level normalisation runs once per file, before any pandas work.
- No external dependency; `csv` is stdlib.
- Behaviour for already-rectangular CSVs is unchanged (padding adds zero fields when every row already has the max count).

## Diagnostic Lesson

The "open in Excel and re-save" workaround sounded like Excel was doing something magical. It wasn't — it was just padding rows. Whenever a fix involves "open in tool X and re-save", the actual question is: *what specific transformation does X apply on save?* In this case, rectangularisation. That's reproducible in 15 lines of Python.
