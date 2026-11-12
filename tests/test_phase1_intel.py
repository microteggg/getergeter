import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from resample import resample  # noqa: E402
from intel import confidence, mtf, regime  # noqa: E402


class Phase1IntelTests(unittest.TestCase):
    def test_resample_exposes_incomplete_coverage(self):
        bars = [{"t": i * 300, "o": 100, "h": 101, "l": 99, "c": 100} for i in range(11)]
        row = resample(bars, "H1", "forex")[0]
        self.assertEqual(row["n"], 11)
        self.assertEqual(row["expected_n"], 12)
        self.assertFalse(row["complete"])
        self.assertAlmostEqual(row["coverage"], 11 / 12, places=3)

    def test_xau_uses_h4_until_daily_is_ready(self):
        tfs = {"D": {"enough": False}, "H4": {"enough": True}, "H1": {"enough": True}, "M15": {"enough": True}}
        requested, used, daily = mtf.select_timeframes("forex", tfs)
        self.assertFalse(daily)
        self.assertEqual(requested, ["H4", "H1", "M15"])
        self.assertEqual(used[0], "H4")

    def test_crypto_requests_daily_hierarchy(self):
        tfs = {tf: {"enough": True} for tf in ("D", "H4", "H1", "M15")}
        requested, used, daily = mtf.select_timeframes("crypto", tfs)
        self.assertTrue(daily)
        self.assertEqual(requested, ["D", "H4", "H1", "M15"])
        self.assertEqual(used, requested)

    def test_regime_is_json_serializable_and_explainable(self):
        bars = []
        price = 100.0
        atr = []
        for i in range(140):
            o = price
            c = o + 0.30 + 0.03 * math.sin(i)
            bars.append({"t": i * 3600, "o": o, "h": c + 0.08, "l": o - 0.05, "c": c})
            atr.append(0.43)
            price = c
        tfa = {"enough": True, "_bars": bars, "_atr14": atr, "swing_structure": "bull",
               "internal_structure": "bull", "events": [{"type": "BOS", "dir": "bull", "displacement": True}]}
        out = regime.classify("H4", tfa, 100)
        self.assertIn(out["phase"], {"TREND", "EXPANSION"})
        self.assertGreater(out["strength"], 50)
        self.assertTrue(out["reasons"])
        json.dumps(out)

    def test_confidence_is_explicitly_non_blocking(self):
        reg = {"direction": "bull", "phase": "TREND", "label": "BULLISH_TREND"}
        hierarchy = {"bull_score": 82, "bear_score": 10, "conflicts": []}
        liq = {"levels": [], "nearest_above": {"type": "PDH"}, "nearest_below": None}
        fvg = {"active": [{"direction": "bull", "score": 80, "tf": "H1", "grade": "A", "id": "x",
                            "factors": [{"name": "Active-leg location", "score": 8, "reason": "discount"}]}]}
        data = {"score": 95, "status": "HEALTHY"}
        out = confidence.build(reg, hierarchy, liq, fvg, data)
        self.assertFalse(out["blocking"])
        self.assertEqual(out["action"], "BUY_WATCH")
        self.assertNotIn("probability", out["description"].lower())


if __name__ == "__main__":
    unittest.main()
