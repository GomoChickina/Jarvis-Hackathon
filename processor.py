#!/usr/bin/env python3
"""Canadian Bank of Jarvis - transaction processing engine (MVP).

Usage:
    python processor.py accounts.csv transactions.csv [output_dir]

Outputs (written to output_dir, default ./output):
    results.txt          one line per transaction (APPROVED / REJECTED - reason)
    summary.txt          processing summary counts
    flagged_report.txt   flagged transactions and why they need review
    final_balances.csv   account balances after processing
"""
import csv
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

# ----------------------------------------------------------------------
# Configurable business thresholds (documented assumptions)
# ----------------------------------------------------------------------
LARGE_AMOUNT_THRESHOLD = Decimal("5000.00")   # amount >= this -> review
VELOCITY_COUNT = 5                            # this many transactions...
VELOCITY_WINDOW = timedelta(minutes=10)       # ...within this window -> review

DEBIT_TYPES = {"PURCHASE", "WITHDRAWAL"}      # money leaves from_account
CREDIT_TYPES = {"DEPOSIT"}                    # money enters to_account
SUPPORTED_TYPES = DEBIT_TYPES | CREDIT_TYPES | {"TRANSFER"}


# ----------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------
@dataclass
class Account:
    account_id: str
    status: str
    balance: Decimal
    daily_limit: Decimal
    currency: str = "CAD"
    customer_name: str = ""


@dataclass
class Result:
    transaction_id: str
    approved: bool
    reject_reason: str = ""
    review_reasons: list = field(default_factory=list)
    amount: str = ""
    tx_type: str = ""

    @property
    def flagged(self):
        return self.approved and bool(self.review_reasons)

    def line(self):
        if self.approved:
            return f"{self.transaction_id} APPROVED"
        return f"{self.transaction_id} REJECTED - {self.reject_reason}"


class Rejected(Exception):
    """Raised internally to stop validation with a reason."""


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------
def parse_amount(raw):
    """Return Decimal amount or raise Rejected for blank/malformed values."""
    raw = (raw or "").strip()
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise Rejected("INVALID AMOUNT (not a number)")
    if not value.is_finite():
        raise Rejected("INVALID AMOUNT (not a number)")
    return value


def load_accounts(path):
    accounts = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            acc_id = row["accountId"].strip()
            accounts[acc_id] = Account(
                account_id=acc_id,
                status=row["status"].strip().upper(),
                balance=Decimal(row["balance"]),
                daily_limit=Decimal(row["dailyLimit"]),
                currency=row.get("currency", "CAD").strip(),
                customer_name=row.get("customerName", "").strip(),
            )
    return accounts


def load_transactions(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # Process in chronological order; the sort is stable so ties keep file order.
    def key(r):
        try:
            return datetime.fromisoformat(r["timestamp"].strip())
        except (ValueError, AttributeError):
            return datetime.max
    return sorted(rows, key=key)


# ----------------------------------------------------------------------
# Processor
# ----------------------------------------------------------------------
class TransactionProcessor:
    def __init__(self, accounts):
        self.accounts = accounts
        self.processed_ids = set()                      # approved IDs (duplicate check)
        self.daily_outflow = defaultdict(Decimal)       # (account, date) -> total debited
        self.recent = defaultdict(deque)                # account -> recent timestamps
        self.results = []

    # -- helpers -------------------------------------------------------
    def _get_active(self, acc_id, role):
        if not acc_id:
            raise Rejected(f"MISSING {role} ACCOUNT")
        acc = self.accounts.get(acc_id)
        if acc is None:
            raise Rejected("INVALID ACCOUNT")
        if acc.status != "ACTIVE":
            raise Rejected(f"INACTIVE ACCOUNT ({acc.status})")
        return acc

    def _parse_timestamp(self, raw):
        try:
            return datetime.fromisoformat(raw.strip())
        except (ValueError, AttributeError):
            raise Rejected("INVALID TIMESTAMP")

    # -- main entry ----------------------------------------------------
    def process(self, row):
        tx_id = (row.get("transactionId") or "").strip()
        tx_type = (row.get("type") or "").strip().upper()   # tolerate 'purchase'
        res = Result(tx_id, False, amount=(row.get("amount") or "").strip(), tx_type=tx_type)
        try:
            if not tx_id:
                raise Rejected("MISSING TRANSACTION ID")
            if tx_type not in SUPPORTED_TYPES:
                raise Rejected(f"UNSUPPORTED TRANSACTION TYPE ({tx_type or 'BLANK'})")
            ts = self._parse_timestamp(row.get("timestamp"))

            src_id = (row.get("fromAccount") or "").strip()
            dst_id = (row.get("toAccount") or "").strip()

            # Accounts: exist + active
            src = dst = None
            if tx_type in DEBIT_TYPES or tx_type == "TRANSFER":
                src = self._get_active(src_id, "SOURCE")
            if tx_type in CREDIT_TYPES or tx_type == "TRANSFER":
                dst = self._get_active(dst_id, "DESTINATION")

            # Amount: valid and positive
            amount = parse_amount(row.get("amount"))
            if amount <= 0:
                raise Rejected("INVALID AMOUNT (must be > 0)")

            # Duplicate ID
            if tx_id in self.processed_ids:
                raise Rejected("DUPLICATE TRANSACTION ID")

            # Insufficient funds
            if src is not None and src.balance - amount < 0:
                raise Rejected("INSUFFICIENT FUNDS")
            if src is not None and dst is not None and src.account_id == dst.account_id:
                raise Rejected("SAME SOURCE AND DESTINATION ACCOUNT")

        except Rejected as r:
            res.reject_reason = str(r)
            self.results.append(res)
            return res

        # ---- Valid: evaluate review rules (including this transaction) ----
        reasons = []
        if amount >= LARGE_AMOUNT_THRESHOLD:
            reasons.append(f"LARGE AMOUNT (>= {LARGE_AMOUNT_THRESHOLD})")

        if src is not None:
            day_key = (src.account_id, ts.date())
            projected = self.daily_outflow[day_key] + amount
            if projected > src.daily_limit:
                reasons.append(
                    f"DAILY LIMIT EXCEEDED ({projected} > {src.daily_limit} on {ts.date()})"
                )

        for acc in (a for a in (src, dst) if a is not None):
            window = self.recent[acc.account_id]
            while window and ts - window[0] > VELOCITY_WINDOW:
                window.popleft()
            if len(window) + 1 >= VELOCITY_COUNT:
                reasons.append(
                    f"HIGH FREQUENCY ({len(window) + 1} transactions in "
                    f"{int(VELOCITY_WINDOW.total_seconds() // 60)} min on {acc.account_id})"
                )

        # ---- Apply (only approved transactions touch state) ----
        if src is not None:
            src.balance -= amount
            self.daily_outflow[(src.account_id, ts.date())] += amount
        if dst is not None:
            dst.balance += amount
        for acc in (a for a in (src, dst) if a is not None):
            self.recent[acc.account_id].append(ts)
        self.processed_ids.add(tx_id)

        res.approved = True
        res.review_reasons = reasons
        self.results.append(res)
        return res


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------
def summarize(results):
    approved = sum(r.approved for r in results)
    return {
        "Transactions Processed": len(results),
        "Approved": approved,
        "Rejected": len(results) - approved,
        "Flagged For Review": sum(r.flagged for r in results),
    }


def write_reports(results, accounts, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "results.txt").write_text("\n".join(r.line() for r in results) + "\n")

    summary = summarize(results)
    text = "\n".join(f"{k}: {v}" for k, v in summary.items()) + "\n"

    reject_counts = defaultdict(int)
    for r in results:
        if not r.approved:
            reject_counts[r.reject_reason.split(" (")[0]] += 1
    text += "\nRejections by reason:\n"
    for reason, n in sorted(reject_counts.items(), key=lambda kv: -kv[1]):
        text += f"  {reason}: {n}\n"
    (out / "summary.txt").write_text(text)

    flagged = [r for r in results if r.flagged]
    lines = [f"FLAGGED TRANSACTIONS FOR MANUAL REVIEW ({len(flagged)})", "=" * 50]
    for r in flagged:
        lines.append(f"{r.transaction_id}  {r.tx_type}  amount={r.amount}")
        for reason in r.review_reasons:
            lines.append(f"    - {reason}")
    (out / "flagged_report.txt").write_text("\n".join(lines) + "\n")

    with open(out / "final_balances.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["accountId", "status", "balance"])
        for a in accounts.values():
            w.writerow([a.account_id, a.status, f"{a.balance:.2f}"])
    return summary, text


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    accounts = load_accounts(argv[1])
    transactions = load_transactions(argv[2])
    out_dir = argv[3] if len(argv) > 3 else "output"

    proc = TransactionProcessor(accounts)
    for row in transactions:
        proc.process(row)

    _, text = write_reports(proc.results, accounts, out_dir)
    print(text)
    print(f"Reports written to {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
