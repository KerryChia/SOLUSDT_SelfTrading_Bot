# SOLUSDT Quantitative Trading Bot

[中文](README_zh.md) | [English](README_en.md)

Binance margin-based SOL/USDT automated trading system. Long/short scalping strategy with dynamic risk control.

## Quick Start

```bash
pip install ccxt numpy requests
cp config.example.json config.json   # Edit with your API keys
python MainProgramme.py              # Start trading
python monitor.py                    # Dashboard → http://localhost:8888
```

## Docs

| Document | Lang |
|----------|------|
| [Project Summary](project_summary_en.txt) / [项目成果说明](项目成果说明_zh.txt) | EN / ZH |
| [Strategy Details](SOLUSDT_Trading_Strategy_en.txt) / [策略文档](SOLUSDT_Trading_Strategy_zh.txt) | EN / ZH |
| [Strategy Explained (Plain)](strategy_explained_en.md) / [策略白话版](策略说明-白话版_zh.md) | EN / ZH |

## Strategy at a Glance

5-min candles · 3x leverage · EMA+RSI+MACD signals · -1.5% SL +3% TP · Dynamic margin-level guardrails
