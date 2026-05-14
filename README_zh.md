# SOLUSDT 量化交易机器人

基于币安杠杆账户的 SOL/USDT 自动交易系统，多空双向短线策略。

## 快速开始

```bash
pip install ccxt numpy requests
cp config.example.json config.json   # 编辑填入API密钥
python MainProgramme.py                           # 启动交易
python monitor.py                     # 仪表盘 → http://localhost:8888
```

## 策略

5分钟K线 · 3倍杠杆 · EMA+RSI+MACD信号 · -1.5%止损 +3%止盈 · 动态保证金率风控

## 文件

| 文件 | 说明 |
|------|------|
| `MainProgramme.py` | 交易机器人 |
| `monitor.py` | 监控仪表盘 |
| `config.example.json` | 配置模板 |
| `SOLUSDT_Trading_Strategy_zh.txt` | 完整策略文档 (中文) |
| `SOLUSDT_Trading_Strategy_en.txt` | Strategy spec (English) |
| `策略说明-白话版_zh.md` | 新手友好版解释 (中文) |
| `strategy_explained_en.md` | Plain-English walkthrough |
| `项目成果说明_zh.txt` | 项目成果总结 (中文) |
| `project_summary_en.txt` | Project summary (English) |
