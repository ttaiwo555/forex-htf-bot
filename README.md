# 🌐 ETM Matrix Bias Bot

**ETM Matrix Bias Bot** is a Telegram bias-only bot built for all 28 major & minor Forex pairs, Gold (`XAUUSD`), Silver (`XAGUSD`), and Bitcoin (`BTCUSD`) using the TwelveData API.

It implements the **ETM The Matrix Full** methodology:
1. **Matrix Level Detection**: A Shape, V Shape, Bullish OCL, Bearish OCL.
2. **Disrespect Logic & Classification**: FRESH, FLIPPED (RBS / SBR), and QM (Quasimodo).
3. **Bias Model 1**: HTF Key Level Rejection (Reusable).
4. **Bias Model 2**: 3-Candle Sweep (One-Time Use with `swept_levels.json`).
5. **Confluence Engine**: Identifies "REJECTION ONLY", "SWEEP ONLY - 3-Candle BRS/BRB", and "BOTH CONFLUENCE 🔥".
6. **Lower Timeframe Confirmation & Flexible Window**: Evaluates H4 breakouts for Daily bias and Daily breakouts for Weekly bias across Candle 2 / Candle 3 windows.
7. **Execution & Rate Optimization**: Optimized for TwelveData Free Plan (800 calls/day, 8 calls/min) with a 1.2s delay and automatic rate limiting.
8. **Automated Dark-Themed Matplotlib Charts**: Generates and dispatches annotated chart snapshots directly to Telegram.

---

## 📋 Tracked Symbols (31 Total)

```python
SYMBOLS = [
    # Forex Majors & Crosses
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURNZD", "EURCHF",
    "GBPJPY", "GBPAUD", "GBPCAD", "GBPNZD", "GBPCHF",
    "AUDJPY", "AUDCAD", "AUDNZD", "AUDCHF",
    "CADJPY", "CADCHF",
    "CHFJPY",
    "NZDJPY", "NZDCAD", "NZDCHF",
    # Commodities & Crypto
    "XAUUSD", "XAGUSD", "BTCUSD"
]
```

---

## 🧠 Core Methodology & Rules

### 1. Level Detection (The Matrix Full)
Levels are detected on **Daily**, **Weekly**, and **H4** timeframes:
- **A Shape (Resistance)**: Bullish candle close == next bearish candle open at SAME HIGH.
- **V Shape (Support)**: Bearish candle close == next bullish candle open at SAME LOW.
- **Bullish OCL (Support)**: Bullish candle close == next bullish candle open at same level (greens stacking).
- **Bearish OCL (Resistance)**: Bearish candle close == next bearish candle open at same level (reds stacking).

#### Tolerances:
- **Gold & Silver (`XAUUSD`, `XAGUSD`)**: `0.15`
- **Bitcoin (`BTCUSD`)**: `15.0`
- **Forex**: `0.00015 * price`

#### Disrespect & Classification:
- **Count 0 (FRESH)**:
  - Fresh A = Resistance
  - Fresh V = Support
  - Fresh Bull OCL = Support
  - Fresh Bear OCL = Resistance
- **Count 1 (FLIPPED)**:
  - A → **RBS** (Resistance Becomes Support, buys)
  - V → **SBR** (Support Becomes Resistance, sells)
  - Bull OCL → **SBR**
  - Bear OCL → **RBS**
- **Count ≥ 2 (QM)**:
  - **Quasimodo** acts in **BOTH** directions.
- All levels are saved to `data/levels.json`.

---

### 2. Bias Model 1: HTF Key Level Rejection (Reusable)
- **Bullish Rejection**: Candle `low < support level` (Fresh V, Fresh Bull OCL, RBS, QM) AND `close > support level`.
- **Bearish Rejection**: Candle `high > resistance level` (Fresh A, Fresh Bear OCL, SBR, QM) AND `close < resistance level`.
- Reusable across multiple candles.

---

### 3. Bias Model 2: 3-Candle Sweep (One-Time Use)
- **BULLISH SWEEP**:
  - `Candle 1` = Previous victim (`High1`, `Low1`)
  - `Candle 2` = Sweeper candle:
    1. `Low2 < Low1` (sweeps low)
    2. `High2 < High1` (high preserved)
    3. `Close2 > Low1` AND `Close2 < High1` (closes inside Candle 1 range)
  - `Candle 3` = Entry candle
  - `Candle 1 Low` is recorded in `data/swept_levels.json` and **never reused**.
- **BEARISH SWEEP**:
  - `Candle 2`: `High2 > High1`, `Low2 > Low1`, and `Close2` inside Candle 1 range.
  - `Candle 1 High` is recorded in `data/swept_levels.json` and **never reused**.

---

### 4. Confluence
- Rejection only: `"REJECTION ONLY"` (Priority: Normal)
- Sweep only: `"SWEEP ONLY - 3-Candle BRS/BRB"` (Priority: Normal)
- Both: `"BOTH CONFLUENCE 🔥"` (Priority: HIGH)

---

### 5. Lower TF Confirmation & Flexible Window
- **Weekly Bias**: Searches up to 50 Daily candles before Candle 1 for nearest structural resistance (A/BearOCL for bull) or support (V/BullOCL for bear).
- **Daily Bias**: Searches up to 50 H4 candles before Candle 1.
- **Flexible Window for Sweeps**:
  - Monitored between Candle 2 open and Candle 3 close:
  - If breakout occurred during Candle 2: `Breakout during Candle2 - Enter Candle3 at open`
  - If breakout occurred during Candle 3: `Breakout during Candle3 - Enter now`
- **For Rejection Only**:
  - Monitors the next 10 LTF candles following rejection close.

---

### 6. Alert Format
```
[EURUSD] 🟢 DAILY BULLISH BIAS - BOTH CONFLUENCE 🔥
Level: Fresh V at 1.07900
Sweep: Candle1 Low swept 1.07800, High preserved 1.09000, Close inside 1.08300
Confirmation: H4 A Breakout at 1.08500 (during Candle2)
Entry: Candle3 - at open
Priority: HIGH if BOTH
```
Includes an automated TradingView-styled candlestick chart snapshot attached with levels, victim range, sweeper, and breakout markers.

---

## ⚙️ Installation & Setup

### 1. Requirements
- Python 3.10+
- Dependencies: `matplotlib`, `requests`, `python-dotenv`

```bash
python -m pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

```ini
TWELVEDATA_API_KEY=your_twelvedata_api_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_or_channel_here
API_CALL_DELAY=1.2
```

---

## 🚀 Running the Bot

### Run Continuous Scheduler
Runs 24/7 matching the execution schedule:
- **Weekly scan**: 00:05 UTC (all 31 symbols Weekly TF)
- **Daily scan**: 00:10 UTC (all 31 symbols Daily TF)
- **H4 scan**: Every 4 hours (all 31 symbols H4 TF)

```bash
python bot.py
```

### Trigger Immediate Scans
```bash
# Scan Weekly timeframe
python bot.py --scan weekly

# Scan Daily timeframe
python bot.py --scan daily

# Scan H4 timeframe
python bot.py --scan h4

# Scan All timeframes in sequence
python bot.py --scan all
```

### Run Self-Test Suite & Status
```bash
# Full end-to-end verification suite with chart generation
python bot.py --test

# Unit tests covering all 10 specifications
python test_suite.py

# View status of active and swept levels
python bot.py --status
```

---

## 📁 Project Structure

```
.
├── config.py             # 31 symbols, tolerances, TwelveData formatting & limits
├── levels.py             # ETM The Matrix Level Detector (A, V, OCL, disrespect count, QM)
├── bias_models.py        # Model 1 (Rejection), Model 2 (3-Candle Sweep), Confluence
├── confirmation.py       # LTF Breakout engine with flexible window (Candle 2 / 3)
├── chart_generator.py    # Dark-mode Matplotlib candlestick chart renderer
├── telegram_notifier.py  # Formatted Telegram alert dispatcher & photo sender
├── data_provider.py      # TwelveData REST client with rate limiting & mock fallback
├── bot.py                # Main runner, scheduler, CLI commands, and self-test
├── test_suite.py         # 10 comprehensive unittest test cases
├── .env.example          # Environment variables template
├── data/
│   ├── levels.json       # Persisted Matrix levels per symbol
│   ├── swept_levels.json # Single-use swept levels database
│   └── alerts_history.json
└── charts/               # Generated candlestick chart snapshots
```
