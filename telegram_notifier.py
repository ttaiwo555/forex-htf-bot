"""
ETM Matrix Telegram Notifier
Formats alerts to user specification and sends Telegram messages + chart snapshots.
Tracks sent alerts in alerts_history.json to prevent duplicates.
"""
import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.parse
import urllib.error
import ssl

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl._create_unverified_context()

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    ALERTS_HISTORY_FILE
)
from bias_models import BiasSignal
from confirmation import ConfirmationResult

logger = logging.getLogger("ETM_Telegram")


class TelegramNotifier:
    """
    Handles formatting and dispatching Telegram alerts with chart images.
    """

    def __init__(
        self,
        bot_token: str = TELEGRAM_BOT_TOKEN,
        chat_id: str = TELEGRAM_CHAT_ID,
        history_file: Path = ALERTS_HISTORY_FILE
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.history_file = history_file
        self.sent_alerts: Dict[str, Any] = {}
        self.load_history()

    def load_history(self) -> None:
        if not self.history_file.exists():
            self.sent_alerts = {}
            return
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                self.sent_alerts = json.load(f)
        except Exception as e:
            logger.error("Failed to load alerts_history.json: %s", e)
            self.sent_alerts = {}

    def save_history(self) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.sent_alerts, f, indent=2)
        except Exception as e:
            logger.error("Failed to save alerts_history.json: %s", e)

    def is_already_alerted(self, alert_key: str) -> bool:
        return alert_key in self.sent_alerts

    def mark_alerted(self, alert_key: str, data: Dict[str, Any]) -> None:
        self.sent_alerts[alert_key] = {
            "timestamp": datetime.utcnow().isoformat(),
            **data
        }
        self.save_history()

    @staticmethod
    def format_alert_message(
        bias_signal: BiasSignal,
        confirmation: ConfirmationResult
    ) -> str:
        """
        Formats alert exactly according to Section 6 specification:
        [{SYMBOL}] {EMOJI} {TIMEFRAME} {DIRECTION} BIAS - {TYPE}
        Level: {Fresh V / RBS from A at price / QM}
        Sweep: Candle1 Low swept X, High preserved Y, Close inside Z (if sweep)
        Confirmation: H4 A Breakout at price (broke during Candle2/3)
        Entry: Candle3 - {at open / now}
        Priority: {Normal / HIGH if BOTH}
        """
        symbol = bias_signal.symbol
        tf_upper = bias_signal.timeframe.upper()
        direction = bias_signal.direction
        dir_emoji = "🟢" if direction == "BULLISH" else "🔴"
        confluence_type = bias_signal.confluence_type

        # Header
        header = f"[{symbol}] {dir_emoji} {tf_upper} {direction} BIAS - {confluence_type}"

        # Level field
        level_desc = "None"
        if bias_signal.key_level:
            lvl = bias_signal.key_level
            if lvl.classification == "FRESH":
                level_desc = f"Fresh {lvl.type} at {lvl.price:.5f}"
            elif lvl.classification == "FLIPPED":
                level_desc = f"{lvl.active_role} from {lvl.type} at {lvl.price:.5f}"
            else:
                level_desc = f"QM {lvl.active_role} at {lvl.price:.5f}"
        elif bias_signal.sweep:
            level_desc = f"Candle1 Victim {bias_signal.sweep.swept_level_type} at {bias_signal.sweep.swept_price:.5f}"

        # Sweep field (if sweep exists)
        sweep_line = ""
        if bias_signal.sweep:
            sw = bias_signal.sweep
            if sw.swept_level_type == "LOW":
                sweep_line = (
                    f"Sweep: Candle1 Low swept {sw.candle2['low']:.5f}, "
                    f"High preserved {sw.preserved_price:.5f}, "
                    f"Close inside {sw.close_price:.5f}"
                )
            else:
                sweep_line = (
                    f"Sweep: Candle1 High swept {sw.candle2['high']:.5f}, "
                    f"Low preserved {sw.preserved_price:.5f}, "
                    f"Close inside {sw.close_price:.5f}"
                )

        # Confirmation field
        ltf_name = "Daily" if bias_signal.timeframe == "Weekly" else "H4"
        conf_line = f"Confirmation: {ltf_name} {confirmation.breakout_level_type} Breakout at {confirmation.breakout_price:.5f} ({confirmation.timing})"

        # Entry field
        entry_timing = "at open" if "open" in confirmation.entry_instruction.lower() else "now"
        entry_line = f"Entry: Candle3 - {entry_timing}"

        # Priority field
        priority_str = "HIGH if BOTH" if confluence_type == "BOTH CONFLUENCE 🔥" else "Normal"
        priority_line = f"Priority: {priority_str}"

        # Assemble lines
        lines = [header, f"Level: {level_desc}"]
        if sweep_line:
            lines.append(sweep_line)
        lines.append(conf_line)
        lines.append(entry_line)
        lines.append(priority_line)

        return "\n".join(lines)

    def send_telegram_alert(
        self,
        bias_signal: BiasSignal,
        confirmation: ConfirmationResult,
        chart_path: Optional[Path] = None
    ) -> bool:
        """
        Sends formatted message and optional chart snapshot to Telegram channel/chat.
        """
        message_text = self.format_alert_message(bias_signal, confirmation)
        alert_key = (
            f"{bias_signal.symbol}_{bias_signal.timeframe}_{bias_signal.direction}_"
            f"{bias_signal.timestamp}_{confirmation.breakout_candle_time}"
        )

        if self.is_already_alerted(alert_key):
            logger.info("Alert %s already sent previously. Skipping duplicate.", alert_key)
            return False

        logger.info("\n========== ETM MATRIX BIAS ALERT ==========\n%s\n===========================================", message_text)

        # Check if Telegram credentials are configured
        if not self.bot_token or not self.chat_id or self.bot_token.startswith("your_"):
            logger.warning("Telegram Bot Token / Chat ID not set. Logged alert locally.")
            self.mark_alerted(alert_key, {"text": message_text, "mode": "local_mock"})
            return True

        # Send to Telegram via Telegram REST API
        success = False
        try:
            if chart_path and chart_path.exists():
                success = self._send_photo_api(chart_path, message_text)
            else:
                success = self._send_message_api(message_text)

            if success:
                self.mark_alerted(alert_key, {"text": message_text, "chart": str(chart_path) if chart_path else None})
        except Exception as e:
            logger.error("Failed to send alert via Telegram API: %s", e)

        return success

    def _send_message_api(self, text: str) -> bool:
        """Sends text message using Telegram Bot sendMessage endpoint."""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            import requests
            resp = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=15)
            data = resp.json()
            return data.get("ok", False)
        except Exception as e:
            logger.error("Telegram sendMessage error: %s", e)
            return False

    def _send_photo_api(self, photo_path: Path, caption: str) -> bool:
        """Sends photo with caption using multipart upload to Telegram API."""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        try:
            import requests
            with open(photo_path, "rb") as photo_file:
                files = {"photo": photo_file}
                data = {"chat_id": self.chat_id, "caption": caption}
                resp = requests.post(url, data=data, files=files, timeout=25)
                res = resp.json()
                return res.get("ok", False)
        except Exception as e:
            logger.error("Telegram sendPhoto error: %s. Falling back to plain message.", e)
            return self._send_message_api(caption)
