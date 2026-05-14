# SOLUSDT Quantitative Trading Bot

Binance margin-based SOL/USDT automated trading system. Long/short scalping strategy with dynamic risk control.

## Quick Start

```bash
pip install ccxt numpy requests
cp config.example.json config.json   # Edit with your API keys
python MainProgramme.py              # Start trading
python monitor.py                    # Dashboard → http://localhost:8888
```

## Strategy

5-min candles · 3x leverage · EMA+RSI+MACD signals · -1.5% stop-loss +3% take-profit · Dynamic margin-level guardrails

## Files

| File | Description |
|------|-------------|
| `MainProgramme.py` | Trading bot |
| `monitor.py` | Asset monitoring dashboard |
| `config.example.json` | Configuration template |
| `SOLUSDT_Trading_Strategy_en.txt` | Full strategy spec |
| `strategy_explained_en.md` | Beginner-friendly strategy walkthrough |
| `project_summary_en.txt` | Project results summary |
