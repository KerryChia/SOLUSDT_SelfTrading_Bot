============================================================
    SOLUSDT Aggressive Short-Term Trading Strategy v8.3
    Bollinger Band Reversion + DeepSeek AI Dynamic Risk
============================================================

Objective: Short-term scalping on Binance margin account.
Style: 5-min Bollinger band mean-reversion + 5x leverage + DeepSeek AI filter.

1. Core Logic
------------------------------------------------------------
Local strategy produces long/short candidate signals based on Bollinger
band touches, RSI turns, EMA trend, volume spikes, and MACD momentum.
DeepSeek AI then acts as a second-pass filter for entry approval, exit
hold/reverse decisions, and periodic position review. Hard risk controls
(-1.0% max loss, 120min negative force close) cannot be overridden by AI.

2. Technical Indicators
------------------------------------------------------------
| Indicator    | Parameters    | Purpose                     |
|-------------|---------------|-----------------------------|
| EMA         | 5, 13, 30     | Short-term trend            |
| RSI         | 7             | Overbought/oversold         |
| MACD        | 8, 17, 9      | Fast momentum shifts        |
| Bollinger   | 14, 2.0       | Volatility bands            |
| ATR         | 7             | True range                  |
| Volume MA   | 10            | Volume spike confirmation   |

Timeframe: 5-min candles. MTF context: 15m + 1h (AI-only by default).

3. Entry Conditions (local signal quality gates)
------------------------------------------------------------

[Long Candidate]
  1. ATR >= 0.045% of price (market is active)
  2. Low touches BB lower band (BB_lower * 1.003)
  3. Current candle is bullish (close > open)
  4. RSI rising, RSI <= 55
  5. Volume >= VOL_MA * 1.5
  6. EMA trend: EMA5 > EMA13 > EMA30
  7. BB width >= 0.6%
  8. RSI bullish divergence (bonus)

[Short Candidate]
  1. ATR >= 0.045% of price
  2. High touches BB upper band (BB_upper * 0.997)
  3. Current candle is bearish (close < open)
  4. RSI falling, RSI >= 45
  5. Volume >= VOL_MA * 1.5
  6. EMA trend: EMA5 < EMA13 < EMA30
  7. BB width >= 0.6%
  8. RSI bearish divergence (bonus)

After 3 recent AI skips, thresholds are temporarily tightened (strict mode).

4. DeepSeek AI Layer
------------------------------------------------------------

[Entry AI]
Receives: last 12 candles, indicators, account state, MTF context, recent results.
Can: approve with cap_use 10%-70%, or skip.
Cannot: reverse long↔short.

[Exit AI]
Triggered by: soft exit conditions, 30-min review, dynamic threshold hits.
Can: close, hold (with new TP/SL thresholds), or reverse position.
Cannot: override -1.0% hard stop or 120min negative force close.

[AI Quality Gate]
If AI-approved entries lose 5 times in a row → AI max cap_use limited to base 48%.

5. Exit Conditions
------------------------------------------------------------
Hard exits (always enforced):
  - Floating loss >= -1.0% → force close
  - Negative for 120 min → force close
  - Margin level below dynamic floor → force close

Soft exits (AI-reviewed):
  - Take-profit trigger: >= 1.5% (or ATR-dynamic up to 3.5%)
  - Stop-loss trigger: <= -1.0% (or ATR-dynamic min 0.6%)
  - Trailing stop: activates at 1.2%, pullback 0.45%
  - Profit retrace protection
  - MACD/RSI momentum weakening
  - 30-min idle review
  - AI plan threshold re-check every 30 min

6. Position Sizing
------------------------------------------------------------
  Leverage: 5x
  Base capital use: 48% (AI can adjust 10% - 70%)
  Target notional: equity * 5 * cap_use
  Margin-level guard: usage<30%→150% floor, <70%→138%, else→130%
  Only 1 position at a time.

7. Risk Controls
------------------------------------------------------------
  1. Hard max loss per trade: -1.0% (AI cannot waive)
  2. Negative position force close: 120 min
  3. Max daily loss: 15% of start equity → halt
  4. 5 consecutive losses → pause 30 min
  5. Dynamic margin-level floor
  6. AI consecutive approve loss cap (5 → limit to base cap_use)
  7. SQLite dual-write persistence (JSON + SQLite)
  8. Slippage tracking: signal price vs fill price

8. MTF Multi-Timeframe
------------------------------------------------------------
  15m and 1h trends computed from EMA30 + MACD histogram.
  MTF context sent to DeepSeek by default.
  MTF local filter (optional): bearish MTF rejects longs, bullish rejects shorts.
  MTF neutral: base cap_use halved (48% → 24%).

9. New in v8.3
------------------------------------------------------------
  - SQLite persistent storage (trades.db) with 5 tables
  - JSON + SQLite dual-write, gradual migration
  - Trade statistics & AI quality dashboards
  - migrate_json_to_sqlite.py for legacy data import
  - Feature flag system (config.py) for gradual rollout

10. Risk Warnings
------------------------------------------------------------
  1. 5x leverage amplifies drawdowns significantly
  2. DeepSeek AI is an assistant, not a guarantee — always use stop-losses
  3. API keys should have trading permissions only; disable withdrawal
  4. Mainland China users need a proxy or Cloudflare Worker relay
  5. This is an aggressive short-term strategy; not investment advice
