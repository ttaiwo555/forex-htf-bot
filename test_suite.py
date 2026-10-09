"""
Unit and Integration Test Suite for ETM Matrix Bias Bot
Covers all 7 specifications from the user prompt.
"""
import sys
import unittest
from pathlib import Path

# Ensure UTF-8 stdout
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import get_tolerance, format_twelvedata_symbol, SYMBOLS
from levels import LevelDetector, ETMLevel
from bias_models import BiasEngine, SweptLevelsTracker, BiasSignal
from confirmation import ConfirmationEngine, ConfirmationResult
from telegram_notifier import TelegramNotifier


class TestETMMatrixBot(unittest.TestCase):

    def test_symbols_count(self):
        """Verify all 31 requested symbols are present."""
        self.assertEqual(len(SYMBOLS), 31)
        self.assertIn("XAUUSD", SYMBOLS)
        self.assertIn("XAGUSD", SYMBOLS)
        self.assertIn("BTCUSD", SYMBOLS)
        self.assertIn("EURUSD", SYMBOLS)
        self.assertIn("NZDCHF", SYMBOLS)

    def test_tolerance_formulas(self):
        """
        Verify Tolerance rules:
        - Gold/Silver: 0.15
        - BTC: 15.0
        - Forex: 0.00015 * price
        """
        self.assertEqual(get_tolerance("XAUUSD", 2650.0), 0.15)
        self.assertEqual(get_tolerance("XAGUSD", 31.5), 0.15)
        self.assertEqual(get_tolerance("BTCUSD", 65000.0), 15.0)

        # Forex EURUSD at 1.0800: 0.00015 * 1.0800 = 0.000162
        forex_tol = get_tolerance("EURUSD", 1.0800)
        self.assertAlmostEqual(forex_tol, 0.000162, places=6)

        # Forex USDJPY at 150.00: 0.00015 * 150.00 = 0.0225
        jpy_tol = get_tolerance("USDJPY", 150.00)
        self.assertAlmostEqual(jpy_tol, 0.0225, places=4)

    def test_twelvedata_symbol_formatting(self):
        """TwelveData requires EUR/USD, XAU/USD, BTC/USD."""
        self.assertEqual(format_twelvedata_symbol("EURUSD"), "EUR/USD")
        self.assertEqual(format_twelvedata_symbol("XAUUSD"), "XAU/USD")
        self.assertEqual(format_twelvedata_symbol("BTCUSD"), "BTC/USD")

    def test_level_detection_all_types(self):
        """
        Detects all 4 Matrix types:
        a) A Shape: bullish candle close == next bearish candle open at SAME HIGH
        b) V Shape: bearish candle close == next bullish candle open at SAME LOW
        c) Bullish OCL: bullish candle close == next bullish candle open
        d) Bearish OCL: bearish candle close == next bearish candle open
        """
        detector = LevelDetector()

        candles = [
            # A Shape: Green closes at 1.0850, Red opens at 1.0850
            {"datetime": "2026-01-01", "open": 1.0800, "high": 1.0850, "low": 1.0790, "close": 1.0850},
            {"datetime": "2026-01-02", "open": 1.0850, "high": 1.0851, "low": 1.0810, "close": 1.0820},
            # V Shape: Red closes at 1.0750, Green opens at 1.0750
            {"datetime": "2026-01-03", "open": 1.0820, "high": 1.0820, "low": 1.0740, "close": 1.0750},
            {"datetime": "2026-01-04", "open": 1.0750, "high": 1.0800, "low": 1.0749, "close": 1.0800},
            # Bullish OCL: Green closes at 1.0800, Green opens at 1.0800
            {"datetime": "2026-01-05", "open": 1.0800, "high": 1.0840, "low": 1.0799, "close": 1.0830},
            # Bearish OCL: Red closes at 1.0810, Red opens at 1.0810
            {"datetime": "2026-01-06", "open": 1.0830, "high": 1.0835, "low": 1.0800, "close": 1.0810},
            {"datetime": "2026-01-07", "open": 1.0810, "high": 1.0815, "low": 1.0770, "close": 1.0780},
        ]

        detected = detector.detect_levels_from_candles("EURUSD", "Daily", candles)
        types = [lvl.type for lvl in detected]
        self.assertIn("A", types)
        self.assertIn("V", types)
        self.assertIn("BullOCL", types)
        self.assertIn("BearOCL", types)

    def test_disrespect_and_classification(self):
        """
        Verify Disrespect logic:
        count 0 = FRESH: Fresh A (Res), Fresh V (Sup), Fresh Bull OCL (Sup), Fresh Bear OCL (Res)
        count 1 = FLIPPED: A -> RBS, V -> SBR, Bull OCL -> SBR, Bear OCL -> RBS
        count >= 2 = QM (Quasimodo) - BOTH ways
        """
        detector = LevelDetector()

        # FRESH A (count 0)
        c, r = detector.classify_level("A", 0)
        self.assertEqual(c, "FRESH")
        self.assertEqual(r, "Resistance")

        # FRESH V (count 0)
        c, r = detector.classify_level("V", 0)
        self.assertEqual(c, "FRESH")
        self.assertEqual(r, "Support")

        # FLIPPED A -> RBS (buys)
        c, r = detector.classify_level("A", 1)
        self.assertEqual(c, "FLIPPED")
        self.assertEqual(r, "RBS")

        # FLIPPED V -> SBR (sells)
        c, r = detector.classify_level("V", 1)
        self.assertEqual(c, "FLIPPED")
        self.assertEqual(r, "SBR")

        # FLIPPED Bull OCL -> SBR
        c, r = detector.classify_level("BullOCL", 1)
        self.assertEqual(c, "FLIPPED")
        self.assertEqual(r, "SBR")

        # FLIPPED Bear OCL -> RBS
        c, r = detector.classify_level("BearOCL", 1)
        self.assertEqual(c, "FLIPPED")
        self.assertEqual(r, "RBS")

        # QM (count >= 2)
        c, r = detector.classify_level("A", 2)
        self.assertEqual(c, "QM")
        self.assertEqual(r, "BOTH")

        c, r = detector.classify_level("V", 3)
        self.assertEqual(c, "QM")
        self.assertEqual(r, "BOTH")

    def test_model1_rejection(self):
        """
        Bullish Rejection: candle low < support AND close > support
        Bearish Rejection: candle high > resistance AND close < resistance
        """
        engine = BiasEngine()
        sup = ETMLevel("sup", "EURUSD", 1.0800, "V", "Daily", 0, "2026-01-01", "FRESH", "Support")
        res = ETMLevel("res", "EURUSD", 1.0900, "A", "Daily", 0, "2026-01-01", "FRESH", "Resistance")

        # Bullish Rejection
        c_bull = {"open": 1.0820, "high": 1.0840, "low": 1.0790, "close": 1.0815, "datetime": "2026-01-05"}
        res_bull = engine.evaluate_rejection("EURUSD", "Daily", c_bull, [sup], [res])
        self.assertTrue(res_bull.is_rejection)
        self.assertEqual(res_bull.direction, "BULLISH")

        # Bearish Rejection
        c_bear = {"open": 1.0880, "high": 1.0915, "low": 1.0870, "close": 1.0885, "datetime": "2026-01-05"}
        res_bear = engine.evaluate_rejection("EURUSD", "Daily", c_bear, [sup], [res])
        self.assertTrue(res_bear.is_rejection)
        self.assertEqual(res_bear.direction, "BEARISH")

    def test_model2_sweep_and_one_time_use(self):
        """
        Bullish Sweep:
        1. Low2 < Low1
        2. High2 < High1 (preserved)
        3. Close2 inside range (Low1 < Close2 < High1)
        Ensure one-time use prevents reusing same Candle1.
        """
        test_file = Path("data/test_swept_levels.json")
        if test_file.exists():
            test_file.unlink()

        tracker = SweptLevelsTracker(storage_path=test_file)
        engine = BiasEngine(swept_tracker=tracker)

        c1 = {"open": 1.0850, "high": 1.0900, "low": 1.0800, "close": 1.0860, "datetime": "2026-02-01"}
        c2 = {"open": 1.0860, "high": 1.0890, "low": 1.0780, "close": 1.0840, "datetime": "2026-02-02"}

        sweep1 = engine.evaluate_sweep("EURUSD", "Daily", c1, c2)
        self.assertTrue(sweep1.is_sweep)
        self.assertEqual(sweep1.direction, "BULLISH")

        # Mark as swept
        tracker.mark_as_swept("EURUSD", "Daily", "2026-02-01", "LOW", 1.0800, "2026-02-02", "BULLISH")

        # Attempt to reuse Candle1
        sweep2 = engine.evaluate_sweep("EURUSD", "Daily", c1, c2)
        self.assertFalse(sweep2.is_sweep, "Candle1 must NOT be reused once marked as swept!")

        # Clean test file
        if test_file.exists():
            test_file.unlink()

    def test_bearish_sweep(self):
        """
        Bearish Sweep:
        1. High2 > High1 (sweeps high)
        2. Low2 > Low1 (low preserved)
        3. Close2 inside range (Low1 < Close2 < High1)
        """
        engine = BiasEngine()
        c1 = {"open": 1.0850, "high": 1.0900, "low": 1.0800, "close": 1.0860, "datetime": "2026-02-10"}
        c2 = {"open": 1.0860, "high": 1.0925, "low": 1.0815, "close": 1.0870, "datetime": "2026-02-11"}

        sweep = engine.evaluate_sweep("EURUSD", "Daily", c1, c2)
        self.assertTrue(sweep.is_sweep)
        self.assertEqual(sweep.direction, "BEARISH")
        self.assertEqual(sweep.swept_level_type, "HIGH")

    def test_confluence_classification(self):
        """
        Confluence:
        rejection only -> "REJECTION ONLY"
        sweep only -> "SWEEP ONLY - 3-Candle BRS/BRB"
        both -> "BOTH CONFLUENCE 🔥"
        """
        engine = BiasEngine()
        sup = ETMLevel("sup", "EURUSD", 1.0800, "V", "Daily", 0, "2026-01-01", "FRESH", "Support")

        c1 = {"open": 1.0850, "high": 1.0900, "low": 1.0800, "close": 1.0860, "datetime": "2026-02-01"}
        # Both sweeps c1 low (1.0800) and rejects sup (1.0800)
        c2 = {"open": 1.0860, "high": 1.0880, "low": 1.0780, "close": 1.0840, "datetime": "2026-02-02"}

        bias = engine.evaluate_bias("EURUSD", "Daily", [c1, c2], [sup], [])
        self.assertIsNotNone(bias)
        self.assertEqual(bias.confluence_type, "BOTH CONFLUENCE 🔥")
        self.assertEqual(bias.priority, "HIGH")

    def test_confirmation_flexible_window(self):
        """
        Flexible Window:
        Breakout can occur during Candle2 or during Candle3.
        """
        conf_engine = ConfirmationEngine()

        bias_sig = BiasSignal(
            symbol="EURUSD",
            timeframe="Daily",
            direction="BULLISH",
            confluence_type="SWEEP ONLY - 3-Candle BRS/BRB",
            priority="Normal",
            sweep=None,
            timestamp="2026-02-02 00:00:00"
        )
        # Mock sweep object
        from bias_models import SweepResult
        c1 = {"datetime": "2026-02-01 00:00:00", "high": 1.0900, "low": 1.0800}
        c2 = {"datetime": "2026-02-02 00:00:00", "high": 1.0880, "low": 1.0780, "close": 1.0830}
        bias_sig.sweep = SweepResult(
            is_sweep=True, direction="BULLISH", candle1=c1, candle2=c2,
            swept_level_type="LOW", swept_price=1.0800, preserved_price=1.0900, close_price=1.0830
        )

        # H4 candles before Candle1 has an A shape at 1.0850
        h4_candles = [
            {"datetime": "2026-01-30 00:00:00", "open": 1.0800, "high": 1.0850, "low": 1.0790, "close": 1.0850},
            {"datetime": "2026-01-30 04:00:00", "open": 1.0850, "high": 1.0851, "low": 1.0810, "close": 1.0820},
            # Breakout during Candle2 (2026-02-02)
            {"datetime": "2026-02-02 08:00:00", "open": 1.0830, "high": 1.0890, "low": 1.0820, "close": 1.0870}
        ]

        conf = conf_engine.verify_confirmation(bias_sig, h4_candles)
        self.assertTrue(conf.is_confirmed)
        self.assertEqual(conf.timing, "during Candle2")
        self.assertIn("at open", conf.entry_instruction)


if __name__ == "__main__":
    unittest.main()
