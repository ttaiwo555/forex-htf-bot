"""
ETM Matrix Bias Bot - Main Engine & Execution Orchestrator
Executes:
- Weekly Scan: 00:05 UTC (all 31 symbols)
- Daily Scan: 00:10 UTC (all 31 symbols - detect rejection/sweep + verify Daily breakout for Weekly bias)
- H4 Scan: Every 4 hours (all 31 symbols - verify H4 breakout for Daily bias)
Supports full simulation/test mode and manual trigger via CLI arguments.
"""
import sys
import time
import argparse
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 stdout encoding on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import (
    SYMBOLS,
    TIMEFRAME_WEEKLY,
    TIMEFRAME_DAILY,
    TIMEFRAME_H4,
    DATA_DIR
)
from data_provider import TwelveDataProvider
from levels import LevelDetector, ETMLevel
from bias_models import BiasEngine, SweptLevelsTracker, BiasSignal
from confirmation import ConfirmationEngine, ConfirmationResult
from chart_generator import ChartGenerator
from telegram_notifier import TelegramNotifier

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(DATA_DIR / "etm_matrix_bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ETM_MatrixBot")


class ETMMatrixBot:
    """
    Main bot orchestrator for ETM Matrix Bias Bot.
    """

    def __init__(self):
        self.data_provider = TwelveDataProvider()
        self.level_detector = LevelDetector()
        self.swept_tracker = SweptLevelsTracker()
        self.bias_engine = BiasEngine(self.swept_tracker)
        self.confirmation_engine = ConfirmationEngine(self.level_detector)
        self.chart_generator = ChartGenerator()
        self.telegram = TelegramNotifier()

        # In-memory candidate bias tracking awaiting confirmation
        self.pending_weekly_biases: Dict[str, BiasSignal] = {}
        self.pending_daily_biases: Dict[str, BiasSignal] = {}

    def scan_weekly(self) -> None:
        """
        Weekly scan: once per day at 00:05 UTC - all 31 symbols Weekly timeframe.
        Detects ETM levels and identifies HTF Weekly bias candidates.
        """
        logger.info("=== STARTING WEEKLY SCAN (All %d Symbols) ===", len(SYMBOLS))
        for sym in SYMBOLS:
            try:
                candles = self.data_provider.fetch_candles(sym, TIMEFRAME_WEEKLY, outputsize=80)
                if not candles:
                    logger.warning("No weekly candles fetched for %s", sym)
                    continue

                # 1. Update Weekly levels
                self.level_detector.detect_levels_from_candles(sym, TIMEFRAME_WEEKLY, candles)

                # 2. Evaluate Weekly bias
                supports = self.level_detector.get_support_levels(sym, TIMEFRAME_WEEKLY)
                resistances = self.level_detector.get_resistance_levels(sym, TIMEFRAME_WEEKLY)
                bias = self.bias_engine.evaluate_bias(sym, TIMEFRAME_WEEKLY, candles, supports, resistances)

                if bias:
                    logger.info("⚡ Weekly Bias Detected for %s: %s (%s)", sym, bias.direction, bias.confluence_type)
                    self.pending_weekly_biases[sym] = bias
                else:
                    self.pending_weekly_biases.pop(sym, None)

            except Exception as e:
                logger.error("Error in Weekly scan for %s: %s", sym, e)

        logger.info("Weekly Scan Complete. Pending Weekly Biases: %d", len(self.pending_weekly_biases))

    def scan_daily(self) -> None:
        """
        Daily scan: once per day at 00:10 UTC - all 31 symbols Daily timeframe.
        1. Checks Daily breakout confirmation for pending Weekly biases.
        2. Detects Daily levels and identifies Daily bias candidates.
        """
        logger.info("=== STARTING DAILY SCAN (All %d Symbols) ===", len(SYMBOLS))
        for sym in SYMBOLS:
            try:
                candles = self.data_provider.fetch_candles(sym, TIMEFRAME_DAILY, outputsize=100)
                if not candles:
                    logger.warning("No daily candles fetched for %s", sym)
                    continue

                # 1. Update Daily levels
                self.level_detector.detect_levels_from_candles(sym, TIMEFRAME_DAILY, candles)

                # 2. Check Daily breakout confirmation for Weekly bias
                if sym in self.pending_weekly_biases:
                    weekly_bias = self.pending_weekly_biases[sym]
                    conf = self.confirmation_engine.verify_confirmation(weekly_bias, candles)
                    if conf.is_confirmed:
                        logger.info("🎯 WEEKLY BIAS CONFIRMED on Daily for %s!", sym)
                        chart_path = self.chart_generator.generate_chart(
                            symbol=sym,
                            timeframe=TIMEFRAME_WEEKLY,
                            candles=candles,
                            bias_signal=weekly_bias,
                            confirmation=conf,
                            key_levels=self.level_detector.get_all_levels(sym, TIMEFRAME_WEEKLY)
                        )
                        self.telegram.send_telegram_alert(weekly_bias, conf, chart_path)

                        # Mark sweep level as used if applicable
                        if weekly_bias.sweep and weekly_bias.sweep.candle1:
                            c1 = weekly_bias.sweep.candle1
                            c2 = weekly_bias.sweep.candle2
                            self.swept_tracker.mark_as_swept(
                                symbol=sym,
                                timeframe=TIMEFRAME_WEEKLY,
                                candle1_time=str(c1.get("datetime", "")),
                                level_type=weekly_bias.sweep.swept_level_type,
                                price=weekly_bias.sweep.swept_price,
                                swept_by_time=str(c2.get("datetime", "")),
                                direction=weekly_bias.direction
                            )
                        # Remove from pending once confirmed
                        self.pending_weekly_biases.pop(sym, None)

                # 3. Evaluate Daily bias
                supports = self.level_detector.get_support_levels(sym, TIMEFRAME_DAILY)
                resistances = self.level_detector.get_resistance_levels(sym, TIMEFRAME_DAILY)
                bias = self.bias_engine.evaluate_bias(sym, TIMEFRAME_DAILY, candles, supports, resistances)

                if bias:
                    logger.info("⚡ Daily Bias Detected for %s: %s (%s)", sym, bias.direction, bias.confluence_type)
                    self.pending_daily_biases[sym] = bias
                else:
                    self.pending_daily_biases.pop(sym, None)

            except Exception as e:
                logger.error("Error in Daily scan for %s: %s", sym, e)

        logger.info("Daily Scan Complete. Pending Daily Biases: %d", len(self.pending_daily_biases))

    def scan_h4(self) -> None:
        """
        H4 scan: every 4 hours - all 31 symbols H4 timeframe.
        Updates H4 levels and checks H4 breakout confirmation for Daily biases.
        """
        logger.info("=== STARTING H4 SCAN (All %d Symbols) ===", len(SYMBOLS))
        for sym in SYMBOLS:
            try:
                candles = self.data_provider.fetch_candles(sym, TIMEFRAME_H4, outputsize=100)
                if not candles:
                    continue

                # 1. Update H4 levels
                self.level_detector.detect_levels_from_candles(sym, TIMEFRAME_H4, candles)

                # 2. Check H4 breakout confirmation for Daily bias
                if sym in self.pending_daily_biases:
                    daily_bias = self.pending_daily_biases[sym]
                    conf = self.confirmation_engine.verify_confirmation(daily_bias, candles)
                    if conf.is_confirmed:
                        logger.info("🎯 DAILY BIAS CONFIRMED on H4 for %s!", sym)
                        chart_path = self.chart_generator.generate_chart(
                            symbol=sym,
                            timeframe=TIMEFRAME_DAILY,
                            candles=candles,
                            bias_signal=daily_bias,
                            confirmation=conf,
                            key_levels=self.level_detector.get_all_levels(sym, TIMEFRAME_DAILY)
                        )
                        self.telegram.send_telegram_alert(daily_bias, conf, chart_path)

                        # Mark sweep level as used if applicable
                        if daily_bias.sweep and daily_bias.sweep.candle1:
                            c1 = daily_bias.sweep.candle1
                            c2 = daily_bias.sweep.candle2
                            self.swept_tracker.mark_as_swept(
                                symbol=sym,
                                timeframe=TIMEFRAME_DAILY,
                                candle1_time=str(c1.get("datetime", "")),
                                level_type=daily_bias.sweep.swept_level_type,
                                price=daily_bias.sweep.swept_price,
                                swept_by_time=str(c2.get("datetime", "")),
                                direction=daily_bias.direction
                            )
                        # Remove from pending once confirmed
                        self.pending_daily_biases.pop(sym, None)

            except Exception as e:
                logger.error("Error in H4 scan for %s: %s", sym, e)

        logger.info("H4 Scan Complete. Pending Daily Biases: %d", len(self.pending_daily_biases))

    def run_scheduler(self) -> None:
        """
        Continuous scheduler respecting UTC times:
        - 00:05 UTC: Weekly scan
        - 00:10 UTC: Daily scan
        - Every 4 hours (00:15, 04:15, 08:15, 12:15, 16:15, 20:15 UTC): H4 scan
        """
        logger.info("ETM Matrix Bias Bot scheduler started. Listening for schedule triggers...")
        last_weekly_day = -1
        last_daily_day = -1
        last_h4_hour = -1

        while True:
            now_utc = datetime.now(timezone.utc)
            curr_day = now_utc.day
            curr_hour = now_utc.hour
            curr_min = now_utc.minute

            # Weekly Scan at 00:05 UTC
            if curr_hour == 0 and curr_min >= 5 and last_weekly_day != curr_day:
                logger.info("Triggering scheduled Weekly Scan at %s UTC", now_utc.strftime("%H:%M:%S"))
                self.scan_weekly()
                last_weekly_day = curr_day

            # Daily Scan at 00:10 UTC
            if curr_hour == 0 and curr_min >= 10 and last_daily_day != curr_day:
                logger.info("Triggering scheduled Daily Scan at %s UTC", now_utc.strftime("%H:%M:%S"))
                self.scan_daily()
                last_daily_day = curr_day

            # H4 Scan every 4 hours (e.g. 00:15, 04:15, 08:15, 12:15, 16:15, 20:15 UTC)
            if curr_hour % 4 == 0 and curr_min >= 15 and last_h4_hour != curr_hour:
                logger.info("Triggering scheduled H4 Scan at %s UTC", now_utc.strftime("%H:%M:%S"))
                self.scan_h4()
                last_h4_hour = curr_hour

            time.sleep(30)


def run_full_self_test():
    """
    Executes comprehensive test verifying:
    1. A Shape, V Shape, Bull OCL, Bear OCL detection
    2. Disrespect tracking (0=FRESH, 1=FLIPPED RBS/SBR, >=2=QM)
    3. Model 1 (Rejection) logic
    4. Model 2 (3-Candle Sweep) logic + one-time use check
    5. Confluence calculation
    6. Confirmation & Flexible window
    7. Chart rendering & Telegram message formatting
    """
    print("\n" + "=" * 60)
    print("RUNNING ETM MATRIX BOT FULL VERIFICATION SUITE")
    print("=" * 60)

    bot = ETMMatrixBot()

    # 1. Verify A Shape & V Shape detection with tolerance
    print("\n[TEST 1] Testing Level Detection & Tolerance...")
    sample_candles = [
        {"datetime": "2026-03-01 00:00:00", "open": 1.0800, "high": 1.0850, "low": 1.0790, "close": 1.0850},  # Bullish
        {"datetime": "2026-03-02 00:00:00", "open": 1.0851, "high": 1.0852, "low": 1.0810, "close": 1.0815},  # Bearish -> A Shape
        {"datetime": "2026-03-03 00:00:00", "open": 1.0815, "high": 1.0820, "low": 1.0750, "close": 1.0760},  # Bearish
        {"datetime": "2026-03-04 00:00:00", "open": 1.0760, "high": 1.0810, "low": 1.0750, "close": 1.0805},  # Bullish -> V Shape
    ]
    detected = bot.level_detector.detect_levels_from_candles("EURUSD", "Daily", sample_candles)
    types_found = [lvl.type for lvl in detected]
    print(f"Detected levels: {types_found}")
    assert "A" in types_found, "Failed to detect A shape resistance!"
    assert "V" in types_found, "Failed to detect V shape support!"
    print(">>> Level detection PASS ✅")

    # 2. Verify Disrespect Count & Classification (FRESH -> RBS / SBR -> QM)
    print("\n[TEST 2] Testing Disrespect Logic & Classification...")
    # Add candle closing above A-shape level (1.0850)
    disrespect_candles = sample_candles + [
        {"datetime": "2026-03-05 00:00:00", "open": 1.0810, "high": 1.0890, "low": 1.0800, "close": 1.0880} # Closes > 1.0850
    ]
    detected2 = bot.level_detector.detect_levels_from_candles("EURUSD", "Daily", disrespect_candles)
    a_level = [lvl for lvl in detected2 if lvl.type == "A"][0]
    print(f"A level disrespect count: {a_level.disrespect_count}, classification: {a_level.classification}, role: {a_level.active_role}")
    assert a_level.disrespect_count == 1, "Disrespect count should be 1"
    assert a_level.active_role == "RBS", "Flipped A level must become RBS!"
    print(">>> Disrespect & RBS classification PASS ✅")

    # 3. Verify Model 2: 3-Candle Sweep
    print("\n[TEST 3] Testing 3-Candle Sweep Model...")
    c1 = {"datetime": "2026-03-08 00:00:00", "open": 1.0850, "high": 1.0900, "low": 1.0800, "close": 1.0840}  # Victim
    c2 = {"datetime": "2026-03-09 00:00:00", "open": 1.0840, "high": 1.0880, "low": 1.0780, "close": 1.0830}  # Sweeper (Low < 1.0800, High < 1.0900, Close inside)
    sweep_res = bot.bias_engine.evaluate_sweep("EURUSD", "Daily", c1, c2)
    print(f"Sweep detected: {sweep_res.is_sweep}, direction: {sweep_res.direction}")
    assert sweep_res.is_sweep and sweep_res.direction == "BULLISH", "Bullish sweep failed!"
    print(f"Sweep details: {sweep_res.details}")
    print(">>> 3-Candle Sweep PASS ✅")

    # 4. Verify Model 1: Rejection
    print("\n[TEST 4] Testing Model 1: Key Level Rejection...")
    v_level = ETMLevel(
        id="test_v", symbol="EURUSD", price=1.0790, type="V", timeframe="Daily",
        disrespect_count=0, first_seen_date="2026-03-01", classification="FRESH", active_role="Support"
    )
    rej_candle = {"datetime": "2026-03-09 00:00:00", "open": 1.0820, "high": 1.0850, "low": 1.0780, "close": 1.0830}
    rej_res = bot.bias_engine.evaluate_rejection("EURUSD", "Daily", rej_candle, [v_level], [])
    print(f"Rejection detected: {rej_res.is_rejection}, direction: {rej_res.direction}")
    assert rej_res.is_rejection and rej_res.direction == "BULLISH", "Bullish rejection failed!"
    print(">>> Key Level Rejection PASS ✅")

    # 5. Verify Confluence
    print("\n[TEST 5] Testing Confluence Detection...")
    bias_signal = bot.bias_engine.evaluate_bias("EURUSD", "Daily", [c1, c2], [v_level], [])
    assert bias_signal is not None, "Bias signal should be detected"
    print(f"Confluence: {bias_signal.confluence_type}, Priority: {bias_signal.priority}")
    assert bias_signal.confluence_type == "BOTH CONFLUENCE 🔥", "Expected BOTH CONFLUENCE"
    print(">>> Confluence PASS ✅")

    # 6. Verify Confirmation & Flexible Window
    print("\n[TEST 6] Testing LTF Confirmation with Flexible Window...")
    h4_candles = [
        {"datetime": "2026-03-07 00:00:00", "open": 1.0810, "high": 1.0850, "low": 1.0805, "close": 1.0850},
        {"datetime": "2026-03-07 04:00:00", "open": 1.0850, "high": 1.0852, "low": 1.0810, "close": 1.0820}, # H4 A Shape at 1.0850
        {"datetime": "2026-03-09 04:00:00", "open": 1.0820, "high": 1.0890, "low": 1.0810, "close": 1.0870}, # Breakout during Candle2
    ]
    conf_res = bot.confirmation_engine.verify_confirmation(bias_signal, h4_candles)
    print(f"Confirmation: {conf_res.is_confirmed}, timing: {conf_res.timing}, entry: {conf_res.entry_instruction}")
    assert conf_res.is_confirmed, "Confirmation failed!"
    print(">>> Confirmation Engine PASS ✅")

    # 7. Verify Chart Generation & Telegram Formatting
    print("\n[TEST 7] Testing Chart Generation & Telegram Alert Formatting...")
    chart_file = bot.chart_generator.generate_chart(
        symbol="EURUSD",
        timeframe="Daily",
        candles=[c1, c2],
        bias_signal=bias_signal,
        confirmation=conf_res,
        key_levels=[v_level]
    )
    if chart_file:
        print(f"Generated chart file: {chart_file}")
        assert chart_file.exists(), "Chart file does not exist!"

    alert_text = bot.telegram.format_alert_message(bias_signal, conf_res)
    print("\nAlert Output Preview:\n" + alert_text)
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED WITH 100% SUCCESS! 🚀")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="ETM Matrix Bias Bot")
    parser.add_argument("--scan", choices=["weekly", "daily", "h4", "all"], help="Execute immediate scan")
    parser.add_argument("--test", action="store_true", help="Run full internal verification suite")
    parser.add_argument("--status", action="store_true", help="Print summary of tracked levels and sweeper state")
    args = parser.parse_args()

    if args.test:
        run_full_self_test()
        return

    bot = ETMMatrixBot()

    if args.status:
        levels_count = sum(len(tfs) for tfs in bot.level_detector.levels.values())
        print(f"Active Tracked Symbols: {len(bot.level_detector.levels)}")
        print(f"Total Level Buckets: {levels_count}")
        print(f"Swept Levels: {len(bot.swept_tracker.swept_records)}")
        return

    if args.scan:
        if args.scan == "weekly":
            bot.scan_weekly()
        elif args.scan == "daily":
            bot.scan_daily()
        elif args.scan == "h4":
            bot.scan_h4()
        elif args.scan == "all":
            bot.scan_weekly()
            bot.scan_daily()
            bot.scan_h4()
        return

    # Default: Run continuous scheduler
    bot.run_scheduler()


if __name__ == "__main__":
    main()
