import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "engine"))

from trading_rules import (account_risk_snapshot, contextual_entry_zone,
                           managed_outcome, position_risk_pct,
                           retest_confirmation)


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


class FinalRuleTests(unittest.TestCase):
    def test_fvg_cannot_stand_alone_and_overlap_is_preferred(self):
        fvg = {"id": "f", "kind": "FVG", "bottom": 10, "top": 12}
        self.assertIsNone(contextual_entry_zone({"fvg": fvg, "ob": None}))
        ob = {"id": "o", "kind": "OB", "bottom": 9, "top": 11, "end_i": None}
        z = contextual_entry_zone({"fvg": fvg, "ob": ob})
        self.assertEqual((z["bottom"], z["top"]), (10, 11))
        self.assertTrue(z["paired_fvg"])

    def test_close_confirmed_break_then_retest_within_eight_m15_bars(self):
        bars = [bar(0, 9.7, 9.9, 9.5, 9.8),
                bar(900, 9.8, 10.7, 9.7, 10.5),
                bar(1800, 10.4, 10.6, 9.95, 10.2),
                bar(2700, 10.2, 10.8, 10.1, 10.7)]
        event = {"index": 1, "t": 900, "dir": "bull", "level": 10.0, "label": "BOS"}
        got = retest_confirmation(bars, [event], 3, "bull", 0, [0.5] * 4, 8, 0.1)
        self.assertIsNotNone(got)
        self.assertEqual(got["retest_index"], 2)
        self.assertEqual(got["bars_after_break"], 2)
        long_bars = bars + [bar(i * 900, 10.5, 10.7, 10.3, 10.6) for i in range(4, 11)]
        self.assertIsNone(retest_confirmation(long_bars, [event], 10, "bull", 0, [0.5] * 11, 8, 0.1))

    def test_be_is_not_activated_at_tp1_but_only_after_1_5r(self):
        rec = {"dir": "bull", "entry": 100, "sl": 90, "tp1": 110, "tp2": 120}
        bars = [bar(1, 100, 111, 99, 108),
                bar(2, 108, 114, 104, 110),
                bar(3, 110, 116, 108, 115),
                bar(4, 115, 116, 99, 100)]
        out = managed_outcome(rec, bars, be_trigger_r=1.5)
        self.assertTrue(out["tp1_hit"])
        self.assertEqual(out["be_t"], 3)
        self.assertEqual(out["status"], "BE")
        self.assertEqual(out["result_r"], 0.4)

    def test_candle_colour_alone_does_not_exit_runner(self):
        rec = {"dir": "bull", "entry": 100, "sl": 90, "tp1": 110, "tp2": 120}
        bars = [bar(1, 100, 111, 99, 108),
                bar(2, 109, 109.5, 107.5, 108),
                bar(3, 108, 121, 107, 120)]
        out = managed_outcome(rec, bars, displacement_atr=5.0)
        self.assertEqual(out["status"], "TP2")
        self.assertEqual(out["result_r"], 1.6)  # 40%×1R + 60%×2R
        self.assertIsNone(out["partial_exit_t"])

    def test_risk_gate_daily_loss_and_three_consecutive_losses(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "journal"))
            now = 1_800_000_000
            rows = {}
            for i in range(3):
                rows[str(i)] = {"symbol": "BTCUSDT", "status_pm": "SL", "result_pm_r": -1.0,
                                "exit_t_pm": now - 120 + i * 30, "risk_pct": 1.0}
            with open(os.path.join(td, "journal", "BTCUSDT.json"), "w", encoding="utf-8") as f:
                json.dump({"entries": rows}, f)
            got = account_risk_snapshot(td, now, 3.0, 3, 7, 0.5)
            self.assertFalse(got["allowed"])
            self.assertEqual(got["daily_net_pct"], -3.0)
            self.assertEqual(got["consecutive_losses"], 3)

    def test_best_setup_is_capped_at_one_percent(self):
        entry = {"rr": 2.7, "zone": {"paired_fvg": True},
                 "htf_aligned": True, "retest_confirmed": True}
        pct, best = position_risk_pct(entry, 0.5, 1.0, 2.5)
        self.assertTrue(best)
        self.assertEqual(pct, 1.0)


if __name__ == "__main__":
    unittest.main()
