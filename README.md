# SOLUSDT 量化交易机器人 v8.3 &nbsp;|&nbsp; SOLUSDT Quantitative Trading Bot v8.3

基于币安杠杆账户的 SOL/USDT 自动交易系统。布林带回归短线策略，接入 DeepSeek AI 动态风控，MTF 多周期上下文，SQLite 持久化存储。

*Binance margin-based SOL/USDT automated trading system. Bollinger band mean-reversion scalping with DeepSeek AI risk filtering, MTF multi-timeframe context, and SQLite persistence.*

---

## 快速开始 / Quick Start

```bash
pip install ccxt numpy requests
cp config.example.json config.json   # 编辑填入API和DeepSeek密钥 / Edit with your keys
python MainProgramme.py              # 启动交易 / Start trading
python monitor.py                    # 仪表盘 → Dashboard → http://localhost:8888
```

> **新手点这里 / New here?** &nbsp; [配置说明（中文）](配置说明_zh.md) &nbsp;|&nbsp; [Setup Guide (English)](setup_guide_en.md)  
> 从注册币安、申请 API、部署 Cloudflare Worker 中继，到服务器后台运行——每一步都有详细截图说明。  
> *Step-by-step from Binance registration, API setup, Cloudflare Worker relay, to server deployment.*

---

## 策略 / Strategy

**ZH** — 5分钟布林带回归 · 5倍杠杆 · DeepSeek AI 入场/出场/复评/反手 · EMA+RSI+MACD+BB+ATR 信号质量过滤 · AI 动态仓位 10%~70% · 硬止损 -1.0% · 120min 负收益强平 · SQLite 双写持久化

**EN** — 5-min Bollinger band mean-reversion · 5x leverage · DeepSeek AI entry/exit/review/reverse · EMA+RSI+MACD+BB+ATR quality gates · AI dynamic cap_use 10%–70% · Hard stop -1.0% · 120 min negative force close · SQLite dual-write persistence

---

## 文档 / Docs

| 文档 Document | 语言 Lang |
|---------------|-----------|
| [项目成果说明](项目成果说明_zh.md) / [Project Summary](project_summary_en.md) | ZH / EN |
| [策略文档](SOLUSDT_Trading_Strategy_zh.md) / [Strategy Spec](SOLUSDT_Trading_Strategy_en.md) | ZH / EN |
| [策略白话版](策略说明-白话版_zh.md) / [Strategy Explained](strategy_explained_en.md) | ZH / EN |
| [配置说明](配置说明_zh.md) / [Setup Guide](setup_guide_en.md) | ZH / EN |
| [完整策略说明 v8.3](SOLUSDT_STRATEGY.md) | ZH |
| [DeepSeek Risk Filter](DEEPSEEK_RISK_FILTER.md) | EN |
| [可视化策略介绍](SOLUSDT_SHOWCASE.html) | ZH |

---

## 文件 / Files

| 文件 File | 说明 | Description |
|-----------|------|-------------|
| `MainProgramme.py` | 交易机器人 (v8.3-sqlite) | Trading bot |
| `monitor.py` | 仪表盘 HTTP 服务 | Dashboard HTTP handler |
| `data_fetcher.py` | 仪表盘数据抓取 | Dashboard data fetcher |
| `dashboard.html` | 仪表盘前端 | Dashboard frontend |
| `calculator.py` | 盈亏与滑点计算 | PnL & slippage calculation |
| `indicator.py` | 技术指标 (回测用) | Technical indicators |
| `storage.py` | SQLite 持久化存储 | SQLite persistence layer |
| `backtest.py` | 回测引擎 | Backtesting engine |
| `migrate_json_to_sqlite.py` | JSON→SQLite 迁移 | Data migration script |
| `config.py` | Feature Flag 配置 | Feature flag config |
| `config.example.json` | 配置模板 | Configuration template |

---

> **⚠️ 风险提示 / Risk Warning** — 杠杆交易有爆仓风险。API 密钥请关闭提现权限。本策略为短线激进型，不构成投资建议。  
> *Leveraged trading carries liquidation risk. Disable withdrawals on API keys. Aggressive short-term strategy; not investment advice.*
