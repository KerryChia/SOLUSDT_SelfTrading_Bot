============================================================
    SOLUSDT Aggressive Short-Term Trading Strategy
    Snowball: $33 → $100 in 3 Weeks
============================================================

Objective: Grow $33 to $100 within 3 weeks (~200% return)
Style: High-frequency scalping + 3x leverage + bi-directional


1. Core Logic
------------------------------------------------------------
Trade 5-minute candle swings: ride the trend, get in and out fast.
Leverage amplifies each trade's return; compounding does the rest.
Cut losses ruthlessly — small losses are better than bag-holding.


2. Technical Indicators
------------------------------------------------------------
| Indicator    | Parameters    | Purpose                     |
|-------------|---------------|-----------------------------|
| EMA         | 5, 13, 30     | Short-term trend (agile)    |
| RSI         | 7             | Rapid overbought/oversold   |
| MACD        | 8, 17, 9      | Fast momentum shifts        |
| Bollinger   | 14, 2         | Short-term volatility bands |
| ATR         | 7             | Short-term true range       |
| Volume MA   | 10            | Volume spike confirmation   |

Timeframe: 5-minute candles


3. Entry Conditions (the more the better)
------------------------------------------------------------

[Long Entry]
  1. EMA5 > EMA13 (short-term bullish)
  2. RSI(7) recovering from oversold (<30) or in 40~55 range
  3. MACD histogram turns positive (or golden cross)
  4. Price bouncing near lower Bollinger Band
  5. Volume > 10-period average

  Minimum: Condition 1 + (2 or 3)

[Short Entry]
  1. EMA5 < EMA13 (short-term bearish)
  2. RSI(7) dropping from overbought (>70) or in 45~60 range
  3. MACD histogram turns negative (or death cross)
  4. Price pulling back near upper Bollinger Band
  5. Volume > 10-period average

  Minimum: Condition 1 + (2 or 3)


4. Exit Conditions
------------------------------------------------------------

[Long Exit]
  Stop-loss: -1.5% from entry, or -1.2x ATR(7), whichever is tighter
  Take-profit: +3% (close entire position)
  Trailing stop: activates after +2% profit, exits on 1.2% pullback
  Reversal signal: EMA5 < EMA13 or RSI > 80

[Short Exit]
  Stop-loss: +1.5% from entry, or +1.2x ATR(7), whichever is tighter
  Take-profit: +3% (close entire position)
  Trailing stop: activates after +2% profit, exits on 1.2% pullback
  Reversal signal: EMA5 > EMA13 or RSI < 20

[Time Stop]
  If no SL/TP triggered within 2 hours → market close (free up capital)


5. Position Sizing
------------------------------------------------------------
  Leverage: 3x
  Capital per trade: 70%-80% of net equity
  Only 1 position at a time (all-in, no diversification)


6. Risk Controls
------------------------------------------------------------
  1. Max daily loss 12% → halt until next day
  2. Max 8 trades per day
  3. 4 consecutive losses → pause 1 hour
  4. Every trade MUST have a stop-loss
  5. No trading 0:00-6:00 Beijing time (low liquidity)
  6. No trading 15 min around major news events
  7. Margin usage < 80% (liquidation protection)


7. Compounding Path (Estimated)
------------------------------------------------------------
  Start: $33, 3x leverage, 75% capital per trade
  Target per win: +3% (SOL price move) → ~+6.75% account gain (leveraged)
  Stop per loss: -1.5% → ~-3.4% account loss (leveraged)

  50% win rate, 4 trades/day:
    Daily net ≈ 2 wins x $2.2 - 2 losses x $1.1 = $2.2
    21 days ≈ $33 + $46 = $79

  55% win rate:
    Daily net ≈ 2.2 wins x $2.2 - 1.8 losses x $1.1 = $2.86
    21 days ≈ $33 + $60 = $93

  Realistic path: after 5-10 days capital reaches $50+,
  position size increases, compounding accelerates.
  Optimistic: 14-21 days to $100
  Conservative: 21-30 days to $80-100


8. Risk Warnings
------------------------------------------------------------
  1. Aggressive short-term strategy; 3x leverage amplifies drawdowns
  2. Win rate is critical — don't chase, wait for the signal
  3. Stop-loss is the bottom line — never move it further away
  4. Monitor margin level — >8% adverse move may trigger liquidation
  5. $33 is small capital; consecutive losses can wipe it out quickly
  6. High-frequency trading incurs significant fees; use BNB for discounts
