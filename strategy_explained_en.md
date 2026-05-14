# SOLUSDT Trading Strategy Explained (Plain English)

## In One Sentence

This bot does one thing: **figures out if SOL is going up or down, follows the trend for short bursts, takes profit quickly, and cuts losses even quicker.** It checks every 5 minutes and trades up to 8 times a day.

---

## What is RSI? At What Levels Does the Bot Trade?

### RSI (Relative Strength Index)

RSI is a number from 0 to 100 that tells you whether SOL has **risen too much** or **fallen too much** recently.

Think of bouncing a basketball: if it bounces too high (high RSI), it's probably coming back down. If it hits the floor hard (low RSI), it's probably bouncing back up.

| RSI Value | Meaning | What the bot does |
|-----------|---------|-------------------|
| **0 ~ 30** | Oversold — has fallen too far | Prepare to buy (wait for a bounce) |
| **30 ~ 50** | Weak, pulling back | ✅ **Good zone to go long** |
| **50 ~ 70** | Strong, rising | ✅ Good zone to go short |
| **70 ~ 100** | Overbought — has risen too far | Prepare to sell/short |
| **> 82** | Extremely overbought | 🚨 Close long immediately |
| **< 18** | Extremely oversold | 🚨 Close short immediately |

**Specific triggers:**

- **Go long (buy)**: RSI between **35~55**, or just recovering from below 35
- **Go short (sell)**: RSI between **45~65**, or just dropping from above 65

---

## How Does the Bot Determine "Trend"?

The bot uses three lines called **EMA** (Exponential Moving Average) — essentially "average recent price."

- **EMA5**: average of last 5 candles (short-term, fastest to react)
- **EMA13**: average of last 13 candles (medium-term)
- **EMA30**: average of last 30 candles (long-term, slowest)

**Simple rule:**

- EMA5 **above** EMA13 → short-term trend is **up** → only go long
- EMA5 **below** EMA13 → short-term trend is **down** → only go short

Think of it like a road sign: arrow pointing up = only go north; arrow pointing down = only go south.

---

## The Five Entry Conditions (What Does s=3/5 Mean?)

Each decision checks 5 conditions. Each satisfied condition = 1 point. `s=3/5` in the log means "3 out of 5 conditions met."

| # | Condition | For Long | For Short |
|---|-----------|----------|-----------|
| 1 | **Trend** | EMA5 > EMA13 (up) | EMA5 < EMA13 (down) |
| 2 | **RSI position** | 35~55 (pullback done) | 45~65 (bounce done) |
| 3 | **MACD momentum** | Histogram flips positive | Histogram flips negative |
| 4 | **Bollinger Band** | Price near lower band (cheap) | Price near upper band (expensive) |
| 5 | **Volume** | Above average by 10% | Above average by 10% |

**Minimum**: Condition 1 (trend) must be met, plus either condition 2 or 3.

---

## When Does It Sell? (Exit Rules)

Six exit methods, in priority order:

### 🛑 Priority 1: Stop-Loss

| Type | Rule | Example (bought at $100) |
|------|------|--------------------------|
| **Hard SL** | Price drops 1.5% → sell | $100 → below $98.50, sell immediately |
| **ATR SL** | Dynamic stop based on volatility | Adjusts with market turbulence |

### ⏰ Priority 2: Time Stop

Position open for 2 hours with no profit → close at market. Don't let capital sit idle.

### 📈 Priority 3: Trailing Stop

Once profit exceeds **2%**, activate "profit protection." If price pulls back **1.2%** from the highest point, sell immediately.

| Price | Peak | Trail line | Result |
|-------|------|------------|--------|
| $100 → $103 | $103 | $101.76 | Holding |
| $100 → $103 → $101.70 | $103 | $101.76 | 🚨 Sell! Profit = 1.7% |

### 💰 Priority 4: Take-Profit

Price reaches **+3%** → sell and lock in.

### 📉 Priority 5: Trend Reversal

EMA5 crosses below EMA13 (trend flips bearish) → sell. Or RSI > 82 (overheated) → sell.

---

## What Is "Shorting"? (Plain Explanation)

Shorting = "borrow the asset, sell it, buy it back cheaper later."

1. You think SOL will drop
2. You **borrow** 1 SOL from the exchange (current price $100), sell it immediately → you get $100
3. SOL drops to $95
4. You buy back 1 SOL for $95 and return it to the exchange
5. You profit **$5** ($100 - $95)

But if SOL rises to $105, you'd have to buy back at $105 and lose $5.

The bot mirrors longs and shorts — same logic, just reversed.

---

## Risk Controls

| Rule | Value | Notes |
|------|-------|-------|
| Max daily trades | 8 | Avoids overtrading |
| Max daily loss | 12% of equity | Halts until next day |
| Consecutive loss pause | 4 losses → pause 30 min | Prevents emotional trading |
| No overnight trading | Beijing 0:00-6:00 | Low liquidity = fake signals |
| No gap chasing | Price gap > 2% | Don't chase spikes |
| Leverage | 3x (<$500 equity) | Amplifies gains AND losses |

---

## Summary

> **EMA tells direction. RSI tells timing. Lose 1.5% → get out. Gain 3% → take it. Trade less, survive longer.**
