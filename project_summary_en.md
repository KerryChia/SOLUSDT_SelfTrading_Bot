============================================================
    SOLUSDT Quantitative Trading Bot — Project Summary
============================================================

1. Overview
------------------------------------------------------------
An automated leveraged trading system for SOL/USDT on Binance.
Uses a hybrid "trend-following + mean-reversion" strategy,
supporting both long and short scalping with 3x leverage
and strict dynamic risk controls.


2. Key Deliverables
------------------------------------------------------------

[1. Trading Bot (MainProgramme.py)]
  - Connects to Binance margin account; scans 5-min candles for signals
  - Indicators: EMA(5/13/30), RSI(7), MACD(8/17/9), Bollinger Bands, ATR
  - Entry: trend confirmation + RSI pullback + MACD momentum + volume confirmation
  - Exit: hard stop-loss (-1.5%), ATR trailing stop, trailing stop, take-profit (+3%), time stop (2h)
  - Dynamic position sizing: reduced when margin level is low
  - Margin-level guard: no entry <128%, forced liquidation if <128%
  - Tiered thresholds: position usage 20%→150% floor, 60%→138%, 90%→130%
  - Adaptive phases: equity <$50 aggressive, $50-100 snowball, $100+ steady, $500+ conservative

[2. Asset Monitor Dashboard (monitor.py)]
  - HTTP server (port 8888), view in browser
  - Real-time: total equity (USD/CNY), margin account net, margin level, SOL price
  - Asset-liability horizontal bar chart (positive right = assets, negative left = shorts)
  - Trade history table (time, action, direction, quantity, price, P&L)
  - Daily P&L & total P&L (configurable initial equity)
  - SOL 5-min candlestick chart
  - 3-day equity trend line chart (dual-axis USD/CNY)
  - Asset breakdown (4 accounts: Funding/Spot/Margin/Futures)
  - Strategy parameters live display
  - 10-second auto-refresh, Chart.js visualization

[3. Documentation]
  - SOLUSDT_Trading_Strategy_en.md: complete strategy spec
  - strategy_explained_en.md: beginner-friendly explanation


3. Tech Stack
------------------------------------------------------------
  - Python 3.14
  - ccxt (Binance API wrapper)
  - numpy (technical indicator computation)
  - Chart.js (frontend charts)
  - Built-in HTTP Server (zero extra dependencies)


4. Usage
------------------------------------------------------------
  1. pip install ccxt numpy requests
  2. Copy config.example.json to config.json, fill in API keys
  3. Mainland China users: configure proxy field
  4. python MainProgramme.py          # Start trading
  5. python monitor.py                # Dashboard → http://localhost:8888
  6. python MainProgramme.py --close-all  # Emergency close all positions


5. Live Trading Results
------------------------------------------------------------
  Initial capital: $30 USD (~¥206 CNY)
  Runtime: May 11, 2026 to present
  Instrument: SOL/USDT (margin)
  Total trades: 30+
  Achievements:
    - Full automated long/short open and close
    - Stop-loss and take-profit auto-execution
    - Dynamic margin-level risk control
    - Real-time asset monitoring dashboard
  Status: strategy running in production


6. Project Structure
------------------------------------------------------------
  github/
  ├── MainProgramme.py               # Trading bot
  ├── monitor.py                      # Dashboard
  ├── config.example.json             # Config template
  ├── SOLUSDT_Trading_Strategy_en.md # Strategy spec (EN)
  ├── SOLUSDT_Trading_Strategy_zh.md # Strategy spec (ZH)
  ├── strategy_explained_en.md        # Plain-English explanation
  ├── 策略说明-白话版_zh.md             # Chinese beginner explanation
  ├── project_summary_en.md          # This file (EN)
  ├── 项目成果说明_zh.md              # Project summary (ZH)
  ├── README.md                       # Main README
  ├── README_en.md                    # English README
  ├── README_zh.md                    # Chinese README
  └── .gitignore                      # Git ignore rules


7. Important Notes
------------------------------------------------------------
  - Leveraged trading carries liquidation risk; understand the strategy first
  - API keys should only have trading permissions; disable withdrawal
  - Mainland China users need a proxy to access api.binance.com
  - This is an aggressive short-term strategy; not investment advice
