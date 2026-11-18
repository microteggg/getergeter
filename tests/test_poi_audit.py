import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import smc
from intel import fvg_quality
from poi_journal import PoiJournal


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


class PoiAuditTests(unittest.TestCase):
    def test_order_block_is_last_opposite_before_displacement(self):
        bars = [bar(0, 9, 9.5, 8.5, 9.2), bar(60, 9.2, 9.4, 8.8, 9.0),
                bar(120, 9.1, 9.3, 8.7, 8.9), bar(180, 8.9, 10.8, 8.8, 10.6),
                bar(240, 10.6, 11.2, 10.4, 11.0)]
        event = {"pivot_index": 0, "index": 4, "impulse_index": 3, "dir": "bull",
                 "scope": "swing", "id": "e", "t": 240, "type": "BOS"}
        wick = smc.order_block(bars, event, 5, "WICK")
        body = smc.order_block(bars, event, 5, "BODY")
        self.assertEqual(wick["t"], 120)
        self.assertEqual((wick["bottom"], wick["top"]), (8.7, 9.3))
        self.assertEqual((body["bottom"], body["top"]), (8.9, 9.1))

    def test_order_block_requires_opposite_candle(self):
        bars = [bar(i * 60, 10 + i, 11 + i, 9 + i, 10.8 + i) for i in range(5)]
        event = {"pivot_index": 0, "index": 4, "impulse_index": 3, "dir": "bull",
                 "scope": "swing", "id": "e", "t": 240, "type": "BOS"}
        self.assertIsNone(smc.order_block(bars, event, 5, "WICK"))

    def test_fvg_fresh_tapped_filled(self):
        formed = [bar(0, 9.5, 10.0, 9.0, 9.8), bar(60, 9.8, 11.6, 9.7, 11.5),
                  bar(120, 11.0, 11.4, 10.8, 11.2)]
        zones = smc.fair_value_gaps(formed, [1, 1, 1], "ATR", .1, 2, .5)
        self.assertEqual(len(zones), 1)
        fresh, active = smc.fvg_state(zones[0], formed + [bar(180, 11.2, 11.5, 10.9, 11.3)])
        self.assertTrue(active); self.assertEqual(fresh["status"], "fresh")
        tapped, active = smc.fvg_state(zones[0], formed + [bar(180, 11.2, 11.5, 10.5, 11.0)])
        self.assertTrue(active); self.assertEqual(tapped["status"], "tapped")
        filled, active = smc.fvg_state(zones[0], formed + [bar(180, 11.2, 11.5, 9.9, 10.2)])
        self.assertFalse(active); self.assertEqual(filled["status"], "filled")
        self.assertEqual(filled["mitigation_pct"], 100.0)

    def test_ob_lifecycle(self):
        z = {"id": "o", "kind": "OB", "dir": "bull", "t": 0, "top": 10.0,
             "bottom": 8.0, "mid": 9.0, "created": 0}
        base = [bar(0, 10.5, 11, 10.2, 10.6)]
        cases = [
            (bar(60, 10.4, 10.5, 9.5, 10.1), "tapped", True),
            (bar(60, 10.4, 10.5, 9.0, 9.4), "CE", True),
            (bar(60, 10.4, 10.5, 7.9, 8.5), "deep", True),
            (bar(60, 10.4, 10.5, 7.7, 7.9), "invalid", False),
        ]
        for b, status, active in cases:
            got, is_active = smc.ob_state(z, base + [b])
            self.assertEqual(got["status"], status)
            self.assertEqual(is_active, active)

    def test_fvg_quality_uses_real_status_and_scores_all_candidates(self):
        a = {"fvg": [
            {"id": "old", "dir": "bull", "t": 60, "status": "tapped", "age": 120, "mitigation_pct": 60},
            {"id": "fresh", "dir": "bull", "t": 120, "status": "fresh", "age": 2, "mitigation_pct": 0, "context": True},
        ], "sweeps": [{"dir": "bull", "t": 100}], "events": [{"dir": "bull", "t": 110, "displacement": True}],
             "premium_discount": "discount"}
        got = fvg_quality.best(a, {"direction": "bull"}, {"direction": "bull"}, {"complete": True})
        self.assertEqual(got["id"], "fresh")
        self.assertEqual(got["factors"]["freshness"], 10)
        self.assertEqual(len(got["candidates"]), 2)

    def test_poi_journal_advances_zone_after_origin_rolls_out(self):
        zone = {"id": "FVG:bull:60", "kind": "FVG", "dir": "bull", "t": 60,
                "top": 11.0, "bottom": 10.0, "mid": 10.5, "status": "fresh", "mitigation_pct": 0}
        with tempfile.TemporaryDirectory() as td:
            j = PoiJournal(td, "TEST")
            j.merge({"M15": {"fvg": [zone], "fvg_mit": [], "ob": [], "ob_mit": []}},
                    {"M15": [bar(120, 11.2, 11.4, 11.1, 11.3)]}, 200)
            j.save()
            j = PoiJournal(td, "TEST")
            j.merge({"M15": {"fvg": [], "fvg_mit": [], "ob": [], "ob_mit": []}},
                    {"M15": [bar(180, 11.2, 11.4, 9.9, 10.2)]}, 260)
            pub = j.public()["tf"]["M15"]
            self.assertFalse(pub["fvg"])
            self.assertEqual(pub["fvg_mit"][0]["status"], "filled")


if __name__ == "__main__":
    unittest.main()
