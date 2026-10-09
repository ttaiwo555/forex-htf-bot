"""
ETM Matrix Chart Generator
Generates high-contrast, professional dark-mode candlestick charts with:
- Dark TradingView aesthetic (#131722)
- ETM Matrix levels (Fresh A/V, RBS, SBR, QM)
- 3-Candle Sweep annotations (Candle 1 Victim, Candle 2 Sweeper, Candle 3 Entry)
- Lower TF breakout level overlay
"""
import os
import logging
from typing import List, Dict, Optional, Any
from pathlib import Path
from datetime import datetime

from config import CHARTS_DIR
from levels import ETMLevel
from bias_models import BiasSignal
from confirmation import ConfirmationResult

logger = logging.getLogger("ETM_ChartGen")


class ChartGenerator:
    """
    Renders candlestick charts with technical ETM annotations.
    """

    def __init__(self, output_dir: Path = CHARTS_DIR):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_chart(
        self,
        symbol: str,
        timeframe: str,
        candles: List[Dict[str, Any]],
        bias_signal: Optional[BiasSignal] = None,
        confirmation: Optional[ConfirmationResult] = None,
        key_levels: Optional[List[ETMLevel]] = None,
        display_candle_count: int = 40
    ) -> Optional[Path]:
        """
        Creates a high-contrast candlestick chart and saves to disk.
        Returns the Path to the generated image file.
        """
        try:
            import matplotlib
            matplotlib.use("Agg")  # Headless backend
            import matplotlib.pyplot as plt
            import matplotlib.patches as patches
        except ImportError:
            logger.warning("Matplotlib not yet available. Skipping chart generation.")
            return None

        if not candles:
            logger.warning("No candle data to plot for %s", symbol)
            return None

        # Take the most recent N candles
        plot_candles = candles[-display_candle_count:] if len(candles) > display_candle_count else candles
        n = len(plot_candles)

        # Dark theme colors
        bg_color = "#131722"
        panel_color = "#1E222D"
        grid_color = "#2A2E39"
        text_color = "#D1D4DC"
        up_color = "#089981"      # Green
        down_color = "#F23645"    # Red
        neutral_color = "#787B86"

        fig, ax = plt.subplots(figsize=(14, 7), dpi=130)
        fig.patch.set_facecolor(bg_color)
        ax.set_facecolor(bg_color)

        # Plot candlesticks
        for i, c in enumerate(plot_candles):
            c_open = float(c["open"])
            c_high = float(c["high"])
            c_low = float(c["low"])
            c_close = float(c["close"])

            is_up = c_close >= c_open
            color = up_color if is_up else down_color

            # Draw high-low wick
            ax.plot([i, i], [c_low, c_high], color=color, linewidth=1.2, zorder=2)

            # Draw candle body
            body_bottom = min(c_open, c_close)
            body_height = max(abs(c_close - c_open), 0.00001)
            rect = patches.Rectangle(
                (i - 0.35, body_bottom),
                0.7,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=1.0,
                zorder=3
            )
            ax.add_patch(rect)

        # Determine y-limits with padding
        all_highs = [float(c["high"]) for c in plot_candles]
        all_lows = [float(c["low"]) for c in plot_candles]
        min_p = min(all_lows)
        max_p = max(all_highs)
        padding = (max_p - min_p) * 0.12 if max_p > min_p else 0.01
        ax.set_ylim(min_p - padding, max_p + padding)
        ax.set_xlim(-1, n + 2)

        # Plot Key ETM Matrix Levels
        if key_levels:
            for lvl in key_levels:
                # Only plot levels within visible price range
                if min_p - padding <= lvl.price <= max_p + padding:
                    if lvl.classification == "FRESH":
                        line_color = down_color if lvl.active_role == "Resistance" else up_color
                        label = f"Fresh {lvl.type} ({lvl.price:.5f})"
                        ls = "--"
                    elif lvl.classification == "FLIPPED":
                        line_color = "#2962FF" if lvl.active_role == "RBS" else "#FF9800"
                        label = f"{lvl.active_role} from {lvl.type} ({lvl.price:.5f})"
                        ls = "-."
                    else:  # QM
                        line_color = "#AB47BC"
                        label = f"QM Level ({lvl.price:.5f})"
                        ls = ":"

                    ax.axhline(y=lvl.price, color=line_color, linestyle=ls, linewidth=1.2, alpha=0.85, zorder=4)
                    ax.text(
                        n - 0.5, lvl.price, f" {label}",
                        color=line_color, fontsize=8, va="center", weight="bold", zorder=5
                    )

        # Highlight Swept Candle Annotations if Sweep Bias
        if bias_signal and bias_signal.sweep:
            # Candle1 is at index n - 2, Candle2 is at index n - 1
            c1_idx = n - 2
            c2_idx = n - 1

            if bias_signal.sweep.candle1 and bias_signal.sweep.candle2:
                c1_low = float(bias_signal.sweep.candle1["low"])
                c1_high = float(bias_signal.sweep.candle1["high"])
                c2_low = float(bias_signal.sweep.candle2["low"])
                c2_high = float(bias_signal.sweep.candle2["high"])

                # Draw victim range box on Candle 1
                c1_box = patches.Rectangle(
                    (c1_idx - 0.45, c1_low),
                    0.9,
                    c1_high - c1_low,
                    fill=False,
                    edgecolor="#FFD600",
                    linestyle=":",
                    linewidth=1.5,
                    zorder=6
                )
                ax.add_patch(c1_box)
                ax.text(
                    c1_idx, c1_high + (padding * 0.25), "Candle 1\n(Victim)",
                    color="#FFD600", fontsize=8, ha="center", weight="bold", zorder=7
                )

                # Annotate Candle 2 Sweeper
                ax.text(
                    c2_idx, c2_high + (padding * 0.25), "Candle 2\n(Sweeper)",
                    color="#00E5FF", fontsize=8, ha="center", weight="bold", zorder=7
                )

                # Annotate Candle 3 Entry point
                ax.text(
                    n, (c2_low + c2_high) / 2.0, "--> Candle 3\n(Entry)",
                    color="#76FF03", fontsize=8.5, ha="left", va="center", weight="bold", zorder=7
                )

        # Highlight Lower TF Breakout Level if confirmed
        if confirmation and confirmation.is_confirmed:
            bo_p = confirmation.breakout_price
            ax.axhline(y=bo_p, color="#00E5FF", linestyle="--", linewidth=1.5, alpha=0.9, zorder=6)
            ax.text(
                1, bo_p, f" LTF Breakout ({confirmation.breakout_level_type}): {bo_p:.5f}",
                color="#00E5FF", fontsize=8.5, va="bottom", weight="bold", zorder=7
            )

        # Format axes and grid
        ax.grid(True, color=grid_color, linestyle="--", alpha=0.5, zorder=1)
        ax.tick_params(colors=neutral_color, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(grid_color)

        # X-ticks with datetimes
        step = max(1, n // 8)
        tick_indices = list(range(0, n, step))
        tick_labels = [plot_candles[i]["datetime"].split(" ")[0] for i in tick_indices]
        ax.set_xticks(tick_indices)
        ax.set_xticklabels(tick_labels, rotation=0, color=neutral_color)

        # Titles and Header (clean ascii for matplotlib canvas)
        bias_type = bias_signal.confluence_type if bias_signal else "MONITORING"
        clean_bias_type = bias_type.replace("🔥", "").strip()
        direction = bias_signal.direction if bias_signal else "NEUTRAL"
        header_color = up_color if direction == "BULLISH" else down_color if direction == "BEARISH" else text_color

        plt.title(
            f"ETM THE MATRIX | [{symbol}] {timeframe} {direction} BIAS -- {clean_bias_type}",
            color=header_color, fontsize=12, weight="bold", pad=12
        )

        timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        chart_filename = f"{symbol.replace('/', '')}_{timeframe}_{timestamp_str}.png"
        chart_path = self.output_dir / chart_filename

        plt.tight_layout()
        plt.savefig(chart_path, facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)

        logger.info("Generated chart snapshot: %s", chart_path)
        return chart_path
