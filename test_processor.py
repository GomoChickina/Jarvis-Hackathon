import unittest
from datetime import datetime
from decimal import Decimal
from processor import Account, TransactionProcessor


def make(balance="100", limit="1000", status="ACTIVE"):
    accts = {
        "A1": Account("A1", status, Decimal(balance), Decimal(limit)),
        "A2": Account("A2", "ACTIVE", Decimal("0"), Decimal("1000")),
        "A3": Account("A3", "CLOSED", Decimal("50"), Decimal("1000")),
    }
    return TransactionProcessor(accts), accts


def tx(i, typ="PURCHASE", frm="A1", to="", amt="10", ts="2026-10-15T10:00:00"):
    return dict(transactionId=i, timestamp=ts, type=typ, fromAccount=frm,
                toAccount=to, amount=amt, channel="POS", description="")


class Tests(unittest.TestCase):
    def test_unknown_account(self):
        p, _ = make()
        self.assertEqual(p.process(tx("T1", frm="NOPE")).reject_reason, "INVALID ACCOUNT")

    def test_inactive_account(self):
        p, _ = make()
        self.assertIn("INACTIVE", p.process(tx("T1", frm="A3")).reject_reason)

    def test_non_positive_and_malformed_amount(self):
        p, _ = make()
        for i, a in enumerate(["0", "-5", "12.5O", ""]):
            self.assertIn("INVALID AMOUNT", p.process(tx(f"T{i}", amt=a)).reject_reason)

    def test_duplicate_id(self):
        p, _ = make()
        self.assertTrue(p.process(tx("T1")).approved)
        self.assertEqual(p.process(tx("T1")).reject_reason, "DUPLICATE TRANSACTION ID")

    def test_insufficient_funds_and_exact_zero(self):
        p, a = make(balance="100")
        self.assertEqual(p.process(tx("T1", amt="100.01")).reject_reason, "INSUFFICIENT FUNDS")
        self.assertTrue(p.process(tx("T2", amt="100")).approved)
        self.assertEqual(a["A1"].balance, 0)

    def test_rejected_does_not_change_balance_or_burn_id(self):
        p, a = make(balance="50")
        p.process(tx("T1", amt="500"))
        self.assertEqual(a["A1"].balance, 50)
        self.assertTrue(p.process(tx("T1", amt="5")).approved)  # ID not consumed

    def test_transfer_moves_money(self):
        p, a = make(balance="100")
        self.assertTrue(p.process(tx("T1", "TRANSFER", "A1", "A2", "40")).approved)
        self.assertEqual((a["A1"].balance, a["A2"].balance), (60, 40))

    def test_transfer_to_inactive_rejected_atomically(self):
        p, a = make(balance="100")
        self.assertTrue(p.process(tx("T1", "TRANSFER", "A1", "A3", "40")).reject_reason)
        self.assertEqual(a["A1"].balance, 100)

    def test_lowercase_type_accepted(self):
        p, _ = make()
        self.assertTrue(p.process(tx("T1", typ="purchase")).approved)

    def test_reversal_unsupported(self):
        p, _ = make()
        self.assertIn("UNSUPPORTED", p.process(tx("T1", typ="REVERSAL")).reject_reason)

    def test_large_amount_flag_boundary(self):
        p, _ = make(balance="100000", limit="100000")
        self.assertFalse(p.process(tx("T1", amt="4999.99")).flagged)
        r = p.process(tx("T2", amt="5000"))
        self.assertTrue(r.approved and r.flagged)

    def test_daily_limit_boundary_and_still_processed(self):
        p, a = make(balance="5000", limit="1000")
        self.assertFalse(p.process(tx("T1", amt="1000")).flagged)       # exactly at limit
        r = p.process(tx("T2", amt="1"))
        self.assertTrue(r.approved and r.flagged)                       # over -> flagged
        self.assertEqual(a["A1"].balance, 3999)                         # but processed

    def test_daily_limit_resets_next_day(self):
        p, _ = make(balance="5000", limit="1000")
        p.process(tx("T1", amt="1000"))
        self.assertFalse(p.process(tx("T2", amt="1000", ts="2026-10-16T09:00:00")).flagged)

    def test_velocity_flag(self):
        p, _ = make(balance="1000")
        flags = [p.process(tx(f"T{i}", ts=f"2026-10-15T10:0{i}:00")).flagged for i in range(5)]
        self.assertEqual(flags, [False] * 4 + [True])

    def test_velocity_window_expires(self):
        p, _ = make(balance="1000")
        for i in range(4):
            p.process(tx(f"T{i}", ts=f"2026-10-15T10:0{i}:00"))
        self.assertFalse(p.process(tx("T9", ts="2026-10-15T10:30:00")).flagged)


if __name__ == "__main__":
    unittest.main()
