============================================================
       SOLUSDT 量化交易机器人 v8.3 — 项目成果说明
============================================================

一、项目概述
------------------------------------------------------------
基于币安交易所 API 的 SOLUSDT 自动化杠杆交易系统。
采用"布林带均值回归 + DeepSeek AI 动态风控"混合策略，
支持多空双向短线交易，5倍杠杆，MTF 多周期上下文，
SQLite 持久化存储。

二、核心成果
------------------------------------------------------------

【1. 交易机器人 (MainProgramme.py) v8.3-sqlite】
  - 自动连接币安杠杆账户，每5分钟扫描K线信号
  - 技术指标：EMA(5/13/30)、RSI(7)、MACD(8/17/9)、BB(14,2.0)、ATR(7)
  - 入场逻辑：布林带回归 + 成交量/EMA/BB宽度/RSI背离过滤 → DeepSeek AI 二次审批
  - 出场逻辑：AI动态止盈止损 + 硬止损(-1.0%) + 120min负收益强平
  - DeepSeek AI：入场审批、出场持仓/反手决策、30分钟复评、市场情绪
  - MTF 多周期：15m+1h 趋势上下文传给AI（可选本地过滤）
  - SQLite 双写：交易、持仓快照、净值快照、AI决策、风控状态
  - IOC 限价执行（可选）、滑点统计、Feature Flag 灰度开关
  - 5x杠杆，基础仓位48%，AI可调至70%

【2. 资产监控仪表盘 (monitor.py + data_fetcher.py + dashboard.html)】
  - HTTP服务 + 独立HTML前端（Chart.js可视化）
  - 实时显示：总资产(USD/CNY)、保证金率、SOL价格、24h涨跌
  - 资产负债条形图、交易记录（含AI入场/出场说明）、滑点统计
  - SOL 5分钟K线图、3天净值趋势图
  - 四账户资产明细（资金/现货/杠杆/合约）
  - 市场情绪：DeepSeek 每小时生成通俗分析
  - SQLite 统计面板：交易统计、AI决策质量
  - 访问记录（管理员）、i18n 三语言切换（简中/繁中/EN）
  - 20秒自动刷新

【3. 工具与辅助】
  - backtest.py：回测引擎，支持纯规则/MTF模拟/参数网格优化
  - storage.py：SQLite持久化层（trades.db，5张表）
  - calculator.py：盈亏计算、滑点追踪、手续费估算
  - migrate_json_to_sqlite.py：历史JSON数据导入
  - config.py：Feature Flag 集中管理
  - cf-worker.js：Cloudflare Workers 币安API中继

【4. 策略文档】
  - SOLUSDT_STRATEGY.md：完整策略说明（v8.3最新）
  - DEEPSEEK_RISK_FILTER.md：DeepSeek风控机制说明（英文）
  - SOLUSDT_Trading_Strategy_zh/en.md：策略文档（双语）
  - 策略说明-白话版_zh.md / strategy_explained_en.md：新手友好版（双语）
  - 配置说明_zh.md / setup_guide_en.md：从零配置指南（双语）

三、技术栈
------------------------------------------------------------
  - Python 3.x
  - ccxt（币安API封装）
  - numpy（技术指标计算）
  - sqlite3（标准库，无额外依赖）
  - Chart.js（前端图表）
  - DeepSeek API（AI风控）

四、使用方法
------------------------------------------------------------
  1. pip install ccxt numpy requests
  2. 复制 config.example.json 为 config.json，填入API密钥和DeepSeek密钥
  3. 中国大陆用户配置 cf_worker（Cloudflare Worker中继）或 proxy
  4. python MainProgramme.py        # 启动交易
  5. python monitor.py              # 仪表盘 → http://localhost:8888
  6. python MainProgramme.py --close-all  # 紧急平仓

五、项目结构
------------------------------------------------------------
  github/
  ├── MainProgramme.py              # 交易机器人 (v8.3)
  ├── monitor.py                    # 仪表盘HTTP服务
  ├── data_fetcher.py               # 仪表盘数据抓取
  ├── dashboard.html                # 仪表盘前端
  ├── calculator.py                 # 盈亏与滑点计算
  ├── indicator.py                  # 技术指标（回测用）
  ├── storage.py                    # SQLite持久化
  ├── backtest.py                   # 回测引擎
  ├── migrate_json_to_sqlite.py     # JSON→SQLite迁移
  ├── config.py                     # Feature Flag配置
  ├── config.example.json           # 配置模板
  ├── SOLUSDT_STRATEGY.md           # 完整策略说明
  ├── DEEPSEEK_RISK_FILTER.md       # AI风控说明
  ├── SOLUSDT_SHOWCASE.html         # 可视化介绍页
  ├── README.md                     # README（中英双语合一）
  ├── 项目成果说明_zh.md            # 本文件
  ├── project_summary_en.md         # English summary
  ├── SOLUSDT_Trading_Strategy_*.md # 策略文档（双语）
  ├── 策略说明-白话版_zh.md          # 中文白话
  ├── strategy_explained_en.md      # English plain
  ├── 配置说明_zh.md                # 中文配置指南
  ├── setup_guide_en.md             # English setup guide
  └── .gitignore                    # 忽略规则

六、注意事项
------------------------------------------------------------
  - 杠杆交易有爆仓风险，请充分理解策略后再使用
  - API密钥请关闭提现权限，仅开交易权限
  - DeepSeek API Key 需单独申请（https://platform.deepseek.com）
  - 中国大陆用户需代理访问 api.binance.com
  - 本策略为短线激进型，不构成投资建议
