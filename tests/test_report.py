import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import report as r

DATA = Path(__file__).resolve().parent.parent / "data"


def run_sample(as_of):
    c, w, p = r.load(DATA)
    rep, _ = r.build_report(c, w, p, as_of)
    return {x.contract_id: x for x in rep}


def mk(works, payments, amount="100.00"):
    d = date
    c = [r.Contract("C", "X", D(amount), d(2026, 1, 1))]
    ws = [r.Work(i, "C", i, D(pr), st, ca, xa) for i, pr, st, ca, xa in works]
    ps = [r.Payment(i, "C", wk, "", D(a), st, cr, pa, rf) for i, wk, a, st, cr, pa, rf in payments]
    return c, ws, ps


class Sample(unittest.TestCase):
    def test_d1_on_2026_11_10(self):
        c = run_sample(date(2026, 11, 10))["D-1"]
        got = {l.work_id: (l.paid, l.outstanding) for l in c.works}
        self.assertEqual(got["W-11"], (D("100000"), D("0")))
        self.assertEqual(got["W-14"], (D("100000"), D("0")))
        self.assertEqual(got["W-13"], (D("0"), D("0")))
        self.assertEqual(got["W-15"], (D("15000"), D("85000")))
        self.assertEqual(got["W-16"], (D("0"), D("100000")))
        self.assertEqual((c.net_paid, c.refunded, c.outstanding, c.queued, c.after_queue),
                         (D("315000"), D("50000"), D("185000"), D("150000"), D("35000")))

    def test_d2_on_2026_11_10_w23_not_yet_accepted_p8_not_yet_exists(self):
        c = run_sample(date(2026, 11, 10))["D-2"]
        self.assertEqual((c.net_paid, c.outstanding, c.overpaid), (D("75000"), D("15000"), D("0")))
        w23 = [l for l in c.works if l.work_id == "W-23"][0]
        self.assertEqual(w23.state, "planned")

    def test_d2_after_p8_is_overpaid(self):
        c = run_sample(date(2026, 11, 12))["D-2"]
        self.assertEqual((c.net_paid, c.outstanding, c.overpaid), (D("95000"), D("0"), D("5000")))

    def test_refund_not_yet_happened_counts_as_paid(self):
        c = run_sample(date(2026, 9, 5))["D-1"]
        self.assertEqual(c.refunded, D("0"))
        self.assertEqual(c.net_paid, D("350000"))

    def test_balance_invariant_on_many_dates(self):
        for day in range(1, 29):
            for m in (9, 10, 11):
                for c in run_sample(date(2026, m, day)).values():
                    paid = sum((l.paid for l in c.works if l.state != "cancelled"), D("0"))
                    self.assertEqual(paid + c.overpaid, c.net_paid)


class Edge(unittest.TestCase):
    d = date

    def test_paid_later_than_date_is_queued(self):
        c, w, p = mk([("W1", "100.00", "planned", None, None)],
                     [("P1", None, "40.00", "paid", self.d(2026, 3, 1), self.d(2026, 3, 9), None)])
        rep, _ = r.build_report(c, w, p, self.d(2026, 3, 5))
        self.assertEqual((rep[0].net_paid, rep[0].queued), (D("0"), D("40")))

    def test_fifo_by_completion_date_not_work_id(self):
        c, w, p = mk([("W1", "100.00", "done", self.d(2026, 2, 1), None),
                      ("W2", "100.00", "done", self.d(2026, 1, 15), None)],
                     [("P1", None, "100.00", "paid", self.d(2026, 3, 1), self.d(2026, 3, 1), None)])
        rep, _ = r.build_report(c, w, p, self.d(2026, 3, 5))
        paid = {l.work_id: l.paid for l in rep[0].works}
        self.assertEqual(paid, {"W1": D("0"), "W2": D("100")})

    def test_linked_excess_goes_to_pool(self):
        c, w, p = mk([("W1", "50.00", "done", self.d(2026, 2, 1), None),
                      ("W2", "50.00", "done", self.d(2026, 2, 2), None)],
                     [("P1", "W1", "70.00", "paid", self.d(2026, 3, 1), self.d(2026, 3, 1), None)])
        rep, _ = r.build_report(c, w, p, self.d(2026, 3, 5))
        paid = {l.work_id: l.paid for l in rep[0].works}
        self.assertEqual(paid, {"W1": D("50"), "W2": D("20")})

    def test_cancelled_after_date_is_still_active(self):
        c, w, p = mk([("W1", "50.00", "cancelled", None, self.d(2026, 4, 1))], [])
        rep, _ = r.build_report(c, w, p, self.d(2026, 3, 5))
        self.assertEqual(rep[0].outstanding, D("50"))

    def test_no_float_and_cents(self):
        c, w, p = mk([("W1", "0.10", "done", self.d(2026, 2, 1), None),
                      ("W2", "0.20", "done", self.d(2026, 2, 2), None)],
                     [("P1", None, "0.30", "paid", self.d(2026, 3, 1), self.d(2026, 3, 1), None)])
        rep, _ = r.build_report(c, w, p, self.d(2026, 3, 5))
        self.assertEqual((rep[0].outstanding, rep[0].overpaid), (D("0.00"), D("0.00")))

    def test_bad_money_rejected(self):
        with self.assertRaises(r.DataError):
            r.parse_money("10.005", "x")
        with self.assertRaises(r.DataError):
            r.parse_money("-1", "x")


if __name__ == "__main__":
    unittest.main()
