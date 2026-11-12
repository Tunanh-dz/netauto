import unittest

from netauto import timefmt

A = "2026-11-12T10:11:17+07:00"
B = "2026-11-12T10:18:49+07:00"


class TestTimefmt(unittest.TestCase):
    def test_clock_day_stamp_full(self):
        self.assertEqual(timefmt.clock(A), "10:11:17")
        self.assertEqual(timefmt.day(A), "12/11/2026")
        self.assertEqual(timefmt.stamp(A), "10:11:17 12/11")
        self.assertEqual(timefmt.full(A), "10:11:17 12/11/2026")

    def test_missing_value(self):
        for f in (timefmt.clock, timefmt.day, timefmt.stamp, timefmt.full):
            self.assertEqual(f(None), "-")

    def test_utc_z_suffix(self):
        self.assertEqual(timefmt.clock("2026-11-12T03:11:17Z"), "03:11:17")

    def test_span_same_day(self):
        self.assertEqual(timefmt.span(A, B), "10:11:17 → 10:18:49 ngày 12/11/2026")

    def test_span_two_days(self):
        self.assertEqual(
            timefmt.span("2026-11-11T16:33:38+07:00", "2026-11-12T10:00:34+07:00"),
            "16:33:38 11/11 → 10:00:34 12/11/2026")


if __name__ == "__main__":
    unittest.main()
