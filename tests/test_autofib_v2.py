import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import fibo  # noqa: E402
from fib_journal import FibJournal  # noqa: E402


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


BARS = [
    bar(0, 101, 102, 100, 101),
    bar(60, 101, 108, 101, 107),
    bar(120, 107, 110, 106, 109),
    bar(180, 109, 109, 108, 108.5),
    bar(240, 108.5, 109, 107.5, 108),       # endpoint confirmed + 25% pullback => LOCKED
    bar(300, 108, 111, 106, 109.5),         # wick > 1.0, but close does not extend
    bar(360, 109.5, 112, 109, 111),         # close > 1.0 => EXTENDED; HL=106
    bar(420, 111, 113, 110.5, 112),         # new HH
    bar(480, 112, 112.5, 110, 111),         # >=23.6% pullback, only 1 confirm candle
    bar(540, 111, 112, 110.5, 111),         # k=2 => new Fib LOCKED + old REPLACED
    bar(600, 111, 112, 104, 105),           # close below new 100% => INVALID
]
SEED = {"seed_id": "FIB2:bull:0:120", "dir": "bull", "i": 2, "origin_i": 0, "ext_i": 2,
        "lo": 100, "hi": 110, "event": {"t": 120, "label": "BOS", "type": "BOS", "level": 108}}


class AutoFibV2Tests(unittest.TestCase):
    def states(self, bars=BARS):
        return fibo.lifecycle_series(bars, [SEED], k_confirm=2, min_retrace=.236)

    def test_lock_requires_confirmation_and_retrace(self):
        s = self.states()
        self.assertEqual(s[3]["status"], "FORMING")
        self.assertEqual(s[4]["status"], "LOCKED")
        self.assertIn("FIB_LOCKED", [e["type"] for e in s[4]["events"]])
        self.assertEqual(s[4]["hi"], 110)

    def test_break_uses_close_not_wick(self):
        s = self.states()
        self.assertGreater(BARS[5]["h"], 110)
        self.assertEqual(s[5]["status"], "LOCKED")
        self.assertEqual(s[6]["status"], "EXTENDED")
        self.assertIn("FIB_EXTENDED", [e["type"] for e in s[6]["events"]])

    def test_replacement_waits_for_complete_hl_hh(self):
        before = fibo.lifecycle_series(BARS[:9], [SEED], 2, min_retrace=.236)
        self.assertEqual(before[8]["status"], "EXTENDED")
        s = self.states()
        self.assertEqual(s[9]["status"], "LOCKED")
        self.assertNotEqual(s[9]["id"], SEED["seed_id"])
        self.assertEqual(s[9]["origin_i"], 5)
        self.assertEqual(s[9]["ext_i"], 7)
        self.assertEqual(s[9]["previous"]["status"], "REPLACED")
        types = [e["type"] for e in s[9]["events"]]
        self.assertIn("FIB_REPLACED", types)

    def test_invalidation_uses_close_and_keeps_previous(self):
        s = self.states()
        self.assertEqual(s[10]["status"], "INVALID")
        self.assertEqual(s[10]["previous"]["status"], "REPLACED")
        self.assertIn("FIB_INVALID", [e["type"] for e in s[10]["events"]])

    def test_extension_prices_use_approved_notation(self):
        expected = {1.236: 112.36, 1.382: 113.82, 1.618: 116.18, 2.236: 122.36, 2.618: 126.18}
        for x, price in expected.items():
            self.assertAlmostEqual(fibo.extension_price("bull", 110, 100, x), price, places=8)

    def test_no_lookahead_prefix_invariance(self):
        full = self.states()
        for end in range(3, len(BARS) + 1):
            prefix = fibo.lifecycle_series(BARS[:end], [SEED], 2, min_retrace=.236)
            j = end - 1
            for key in ("id", "status", "origin_i", "ext_i", "lo", "hi"):
                self.assertEqual(prefix[j][key], full[j][key], (end, key))

    def test_cluster_overlap(self):
        active = {"gold": [106, 107], "levels": [], "extensions": []}
        previous = {"gold": [106.5, 108], "levels": [{"price": 106.8, "notation": "61.8%"}], "extensions": []}
        z = fibo._clusters(active, previous, 2)
        self.assertTrue(any(x["kind"] == "gold_overlap" for x in z))
        self.assertTrue(any(x["kind"] == "level_overlap" for x in z))

    def test_journal_is_idempotent_and_persistent(self):
        s = self.states()
        hist = []
        for j, snap in s.items():
            for ev in snap.get("events") or []:
                hist.append({"id": ev["id"], "event": ev, "fib": {
                    "id": snap["id"], "tf": "H4", "dir": snap["dir"], "scope": "swing", "status": snap["status"],
                    "anchor100": {"t": BARS[snap["origin_i"]]["t"], "price": snap["lo"]},
                    "anchor0": {"t": BARS[snap["ext_i"]]["t"], "price": snap["hi"]}}})
        with tempfile.TemporaryDirectory() as td:
            a = {"_fib_history": hist}
            j = FibJournal(td, "TEST")
            first = j.merge({"H4": a}, 1); j.save()
            again = FibJournal(td, "TEST")
            second = again.merge({"H4": a}, 2); again.save()
            self.assertGreater(len(first), 0)
            self.assertEqual(second, [])
            with open(os.path.join(td, "fib", "TEST.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(len(data["events"]), len(first))


if __name__ == "__main__":
    unittest.main()
