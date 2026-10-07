# CBOJ Transaction Processor

A single-page tool for the Canadian Bank of Jarvis (CBOJ) that processes a batch of bank transactions against a set of accounts. Load two CSV files, click **Process**, and see which transactions were approved, rejected, or flagged for review, along with the final account balances.

Everything runs in your browser. No data is uploaded anywhere, and there is nothing to install.

## Quick start

1. Open the HTML file (`CBOJ Transaction Processor.html`) in any modern browser.
2. Choose your **Accounts CSV** and your **Transactions CSV**.
3. (Optional) Adjust the review thresholds.
4. Click **Process**.

The page supports light and dark mode automatically, following your system setting.

## Input files

Both files need a header row. Column names are case-sensitive.

### Accounts CSV

| Column | Used for |
|---|---|
| `accountId` | Unique account identifier |
| `customerName` | Shown on the Balances tab |
| `status` | Only `ACTIVE` accounts can transact (case-insensitive) |
| `balance` | Starting balance |
| `dailyLimit` | Maximum outgoing amount per account per day before a transaction is flagged |

Other columns (for example `accountType`, `currency`, `openedDate`) can be present but are ignored.

### Transactions CSV

| Column | Used for |
|---|---|
| `transactionId` | Unique transaction identifier |
| `timestamp` | When the transaction happened (ISO format, for example `2026-10-01T10:00:00Z`) |
| `type` | `PURCHASE`, `WITHDRAWAL`, `DEPOSIT`, or `TRANSFER` (case-insensitive) |
| `fromAccount` | Source account (blank for deposits) |
| `toAccount` | Destination account (blank for purchases and withdrawals) |
| `amount` | Positive amount, for example `250.00` |

Other columns (for example `channel`, `description`) can be present but are ignored.

### Which accounts each type needs

| Type | Debits `fromAccount` | Credits `toAccount` |
|---|---|---|
| `PURCHASE` | Yes | No |
| `WITHDRAWAL` | Yes | No |
| `DEPOSIT` | No | Yes |
| `TRANSFER` | Yes | Yes |

## How processing works

Transactions are processed **in timestamp order**, not file order. Rows with an unreadable timestamp are rejected. Each transaction is then either approved or rejected, and approved ones may also be flagged.

### Rejection rules

A transaction is rejected, with no effect on any balance, at the first of these checks it fails:

1. Missing transaction ID
2. Unsupported transaction type
3. Invalid timestamp
4. A required account is missing, doesn't exist, or isn't `ACTIVE`
5. Amount isn't a number, or isn't greater than zero
6. Duplicate transaction ID (every ID that has been handled counts, even if its earlier use was rejected)
7. Insufficient funds in the source account
8. Source and destination are the same account

### Review flags

A flag does **not** reject a transaction. The transaction is approved, balances are updated, and it appears in the **Flagged** tab with the reason.

| Flag | Triggered when |
|---|---|
| Large amount | The amount is at or above the **Large amount** threshold (default $5,000) |
| Daily limit exceeded | The source account's outgoing total for that day, including this transaction, goes over its `dailyLimit` |
| High frequency | An account (source or destination) has reached the **count** within the last **minutes** window, including this transaction (defaults: 5 within 10 minutes) |

The three thresholds can be changed in the page before you click **Process**.

## Reading the results

After processing, summary tiles show the totals for processed, approved, rejected and flagged transactions. Use the tabs to switch views:

- **All results**: every transaction with its outcome and detail
- **Approved**: approved transactions only
- **Rejected**: rejected transactions with the reason
- **Flagged**: approved transactions that need review, with each reason listed
- **Balances**: every account's final balance after all approved transactions

## Notes and limitations

- Money is handled internally as whole cents, which avoids floating-point rounding errors.
- Balances start from the Accounts CSV every time you click **Process**. Nothing is saved between runs, and the CSV files are never modified.
- Results can only be viewed on screen. There is no CSV or file export.
- The "day" for the daily limit is the date as written in the timestamp (the first 10 characters), without time zone conversion.
- Currency is not checked, so a transfer between accounts in different currencies is not rejected.
- Account rows are not validated beyond what's needed to run, so check that balances and limits are valid numbers before loading.
- This is an MVP for review and testing, not a system of record.

## Files

- `CBOJ Transaction Processor.html`: the whole application (HTML, CSS and JavaScript in one file)
