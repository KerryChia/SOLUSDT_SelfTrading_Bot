# SOLUSDT Trading Strategy Explained (Plain English v8.3)

## In One Sentence

This bot does one thing: **figures out if SOL is going up or down, follows the trend for short bursts, takes profit quickly, and cuts losses even quicker.** Before every trade, it also asks DeepSeek AI: "Is this a good opportunity?"

---

## Two-Layer Decision System

The bot now has two layers:

1. **Local rules**: Technical indicators (Bollinger Bands, RSI, EMA, volume, etc.) find trade candidates
2. **DeepSeek AI**: When a candidate signal appears, key data is sent to AI for a second opinion — whether to trade and how much capital to use

AI is NOT called every 50 seconds — only when local rules think there's a real opportunity. AI also cannot override hard risk controls (-1.0% loss force close, 120min negative force close).

---

## What is RSI? At What Levels Does the Bot Trade?

### RSI (Relative Strength Index)

RSI is a number from 0 to 100 that tells you whether SOL has **risen too much** or **fallen too much** recently.

Think of bouncing a basketball: if it bounces too high (high RSI), it's probably coming back down. If it hits the floor hard (low RSI), it's probably bouncing back up.

| RSI Value | Meaning | What the bot does |
|-----------|---------|-------------------|
| **0 ~ 30** | Oversold | Prepare to buy (wait for bounce) |
| **30 ~ 50** | Weak, pulling back | ✅ **Good zone to go long** |
| **50 ~ 70** | Strong, rising | ✅ Good zone to go short |
| **70 ~ 100** | Overbought | Prepare to sell/short |
| **> 82** | Extremely overbought | 🚨 Close long immediately |
| **< 18** | Extremely oversold | 🚨 Close short immediately |

**Specific triggers:**

- **Long candidate**: RSI rising, <= 55, low touches BB lower band
- **Short candidate**: RSI falling, >= 45, high touches BB upper band

---

## How Does the Bot Determine "Trend"?

Three lines called **EMA** (Exponential Moving Average) — "average recent price":

- **EMA5**: average of last 5 candles (fastest)
- **EMA13**: average of last 13 candles (medium)
- **EMA30**: average of last 30 candles (slowest)

**Simple rule:**

- EMA5 **above** EMA13 **above** EMA30 → trend is **up** → only go long
- EMA5 **below** EMA13 **below** EMA30 → trend is **down** → only go short

---

## Entry Conditions (Signal Quality Gates)

All conditions must be met for a candidate signal:

| # | Condition | For Long | For Short |
|---|-----------|----------|-----------|
| 1 | **BB position** | Low touches lower band | High touches upper band |
| 2 | **Candle type** | Bullish (close > open) | Bearish (close < open) |
| 3 | **RSI direction** | RSI rising | RSI falling |
| 4 | **Volume** | > MA * 1.5 | > MA * 1.5 |
| 5 | **EMA trend** | EMA5 > EMA13 > EMA30 | EMA5 < EMA13 < EMA30 |
| 6 | **BB width** | >= 0.6% | >= 0.6% |
| 7 | **Volatility** | ATR >= 0.045% | ATR >= 0.045% |

After local signal passes, DeepSeek AI makes a second decision: approve, skip, or adjust cap_use (10%-70%).

---

## DeepSeek AI Exit Decisions

When soft exit conditions trigger (take-profit, stop-loss, trailing stop, momentum weakening, etc.), the bot asks AI three questions:

- **Close**: AI says sell → bot sells.
- **Hold**: AI says keep it → bot holds with new TP/SL thresholds.
- **Reverse**: AI says close current + open opposite side immediately.

Hard controls that AI cannot override:
- -1.0% loss → force close, no questions asked.
- 120 min negative → force close.
- Margin level too low → force close.

---

## When Does It Sell? (Exit Rules)

### Hard Exits (AI cannot prevent)

| Condition | Action |
|-----------|--------|
| Floating loss >= -1.0% | Force close immediately |
| Position 2+ hours, still negative | Force close immediately |
| Margin level below floor | Force close immediately |

### Soft Exits (AI-reviewed)

| Trigger | What happens |
|---------|-------------|
| Profit >= 1.5%+ | AI decides: close or hold |
| Loss hits stop line | AI decides: close or hold |
| Profit pullback | Trailing stop activates |
| MACD/RSI weakening | AI reviews |
| Idle 30 min | AI auto-review |
| AI set new thresholds | Re-checked after 30 min |

---

## Position Sizing

- Leverage: **5x**
- Base capital use: **48%** (AI adjusts 10%–70%)
- Target notional: equity × 5 × cap_use
- Only 1 position at a time

---

## Risk Controls

| Rule | Value | Notes |
|------|-------|-------|
| Hard stop-loss | -1.0% per trade | AI cannot waive |
| Negative force close | 120 min | AI cannot waive |
| Max daily loss | 15% of start equity | Halts until next day |
| Consecutive loss pause | 5 losses → pause 30 min | Prevents emotional trading |
| AI quality gate | 5 consecutive approve losses | AI cap limited back to 48% |
| Margin floor | tiered by position size | 150%→138%→130% |

---

## Summary

> **Local rules find opportunities. DeepSeek AI decides whether to take them. Trend must be right before entering. Lose 1.0% → get out, no debate. AI helps hold winners and decide reversals, but hard stops are untouchable. Surviving matters more than speed.**
