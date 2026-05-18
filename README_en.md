# SOLUSDT Quantitative Trading Bot

Binance margin-based SOL/USDT automated trading system. Long/short scalping strategy with dynamic risk control.

## Quick Start

```bash
pip install ccxt numpy requests
cp config.example.json config.json   # Edit with your API keys
python launcher.py --debug           # Control panel + two debug terminals
python MainProgramme.py              # Start trading
python monitor.py                    # Dashboard → http://localhost:8888
```

On Windows, you can also double-click `启动.bat` to open the debug launcher.

## Strategy

5-min candles · 3x leverage · EMA+RSI+MACD signals · -1.5% stop-loss +3% take-profit · Dynamic margin-level guardrails

## Files

| File | Description |
|------|-------------|
| `MainProgramme.py` | Trading bot |
| `monitor.py` | Asset monitoring dashboard |
| `launcher.py` | Control panel for starting/stopping bot and monitor |
| `启动.bat` | Windows double-click launcher, starts debug mode |
| `config.example.json` | Configuration template |
| `SOLUSDT_Trading_Strategy_en.md` | Full strategy spec |
| `strategy_explained_en.md` | Beginner-friendly strategy walkthrough |
| `project_summary_en.md` | Project results summary |
| `setup_guide_en.md` | Setup guide for beginners |
| `配置说明_zh.md` | Chinese setup guide |
