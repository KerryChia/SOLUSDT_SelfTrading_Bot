============================================================
    SOLUSDT Quantitative Trading Bot v8.3 — Project Summary
============================================================

1. Overview
------------------------------------------------------------
An automated leveraged trading system for SOL/USDT on Binance.
Uses "Bollinger band mean-reversion + DeepSeek AI dynamic risk control"
hybrid strategy, supporting long/short scalping with 5x leverage,
MTF multi-timeframe context, and SQLite persistent storage.

2. Key Deliverables
------------------------------------------------------------

[1. Trading Bot (MainProgramme.py) v8.3-sqlite]
  - Connects to Binance margin account; scans 5-min candles
  - Indicators: EMA(5/13/30), RSI(7), MACD(8/17/9), BB(14,2.0), ATR(7)
  - Entry: BB reversion signal → volume/EMA/BB width/RSI divergence filters → DeepSeek AI approval
  - Exit: AI dynamic TP/SL + hard stop (-1.0%) + 120min negative force close
  - DeepSeek AI: entry approval, exit hold/reverse decisions, 30-min review, market sentiment
  - MTF: 15m+1h trend context for AI (optional local filter)
  - SQLite dual-write: trades, position snapshots, equity, AI decisions, risk state
  - IOC limit execution (optional), slippage tracking, Feature Flag system
  - 5x leverage, base cap_use 48%, AI adjustable to 70%

[2. Asset Monitor Dashboard]
  - HTTP server + standalone HTML frontend (Chart.js)
  - Real-time: total equity (USD/CNY), margin level, SOL price, 24h change
  - Asset-liability bar chart, trade history with AI notes, slippage stats
  - SOL 5-min candlestick chart, 3-day equity trend chart
  - 4-account asset breakdown (Funding/Spot/Margin/Futures)
  - Market sentiment: hourly DeepSeek plain-language analysis
  - SQLite stats panel: trade stats, AI decision quality
  - Access log (admin only), i18n (zh-CN/zh-TW/EN)

[3. Tools & Utilities]
  - backtest.py: backtesting engine with rules/AI-sim/MTF/grid optimization
  - storage.py: SQLite persistence (trades.db, 5 tables)
  - calculator.py: PnL calc, slippage tracking, fee estimation
  - migrate_json_to_sqlite.py: legacy JSON → SQLite import
  - config.py: centralized Feature Flag management
  - cf-worker.js: Cloudflare Workers Binance API relay

[4. Documentation]
  - SOLUSDT_STRATEGY.md: full strategy spec (Chinese, v8.3)
  - DEEPSEEK_RISK_FILTER.md: AI risk filter explanation
  - Bilingual strategy docs, plain-language walkthroughs, setup guides

3. Tech Stack
------------------------------------------------------------
  - Python 3.x
  - ccxt (Binance API wrapper)
  - numpy (technical indicator computation)
  - sqlite3 (stdlib, zero extra dependencies)
  - Chart.js (frontend charts)
  - DeepSeek API (AI risk filtering)

4. Usage
------------------------------------------------------------
  1. pip install ccxt numpy requests
  2. Copy config.example.json to config.json, fill in API keys & DeepSeek key
  3. Configure cf_worker (Cloudflare Worker relay) or proxy if needed
  4. python MainProgramme.py        # Start trading
  5. python monitor.py              # Dashboard → http://localhost:8888
  6. python MainProgramme.py --close-all  # Emergency close

5. Project Structure
------------------------------------------------------------
  github/
  ├── MainProgramme.py              # Trading bot (v8.3)
  ├── monitor.py / data_fetcher.py  # Dashboard backend
  ├── dashboard.html                # Dashboard frontend
  ├── calculator.py / indicator.py  # Math & indicators
  ├── storage.py                    # SQLite persistence
  ├── backtest.py                   # Backtesting engine
  ├── migrate_json_to_sqlite.py     # Data migration
  ├── config.py                     # Feature flags
  ├── config.example.json           # Config template
  ├── SOLUSDT_STRATEGY.md           # Full strategy spec
  ├── DEEPSEEK_RISK_FILTER.md       # AI risk filter docs
  ├── SOLUSDT_SHOWCASE.html         # Visual showcase
  ├── README.md (trilingual)        # Main README
  ├── project_summary_en.md         # This file
  ├── 项目成果说明_zh.md             # Chinese summary
  ├── Bilingual strategy docs       # EN/ZH strategy & plain guides
  ├── Bilingual setup guides        # EN/ZH from-scratch setup
  └── .gitignore

6. Important Notes
------------------------------------------------------------
  - Leveraged trading carries liquidation risk; understand thoroughly first
  - API keys: trading permissions only; disable withdrawal
  - DeepSeek API key required (apply at https://platform.deepseek.com)
  - Mainland China users need proxy for api.binance.com
  - Aggressive short-term strategy; not investment advice
