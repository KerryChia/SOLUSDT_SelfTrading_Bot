# DeepSeek Risk Filter

DeepSeek is now used as a second-pass AI decision layer for both entry and soft exit decisions.

## Entry

The local strategy must first produce a candidate `long` or `short` signal. Only then does the bot call DeepSeek with compact key data:

- latest 12 five-minute candles;
- candidate side and local signal reason;
- RSI, EMA, Bollinger Bands, Bollinger width, ATR, volume ratio, MACD histogram;
- optional 15m and 1h trend context;
- recent 3 AI-approved trade results;
- margin equity, margin level, daily trades, daily PnL, consecutive losses;
- base `cap_use` and maximum allowed `cap_use`.

DeepSeek can:

- approve the candidate signal;
- skip the candidate signal;
- adjust this trade's `cap_use` between `10%` and `70%`;
- provide a short reason shown in the dashboard.

It cannot reverse `long` into `short`, or `short` into `long`.

Before DeepSeek is called, the local candidate must pass signal quality gates: volume must exceed `VOL_MA * 1.5`, EMA trend must match the side, and Bollinger width must be wide enough. After 3 recent AI skips, local signal thresholds are temporarily tightened.

## Exit

When the local strategy reaches a soft exit condition, the bot asks DeepSeek again.

DeepSeek can:

- return `close`, so the bot exits the position;
- return `hold`, so the bot keeps the position open;
- set `next_take_profit_pct` and `next_stop_loss_pct` for the next AI check;
- provide a reason shown in the dashboard.

The AI can extend holding time and override normal take-profit or stop-loss thresholds, but it cannot override hard risk controls. In the current version, AI hold is ignored once a trade reaches `-1.0%` floating loss or remains negative after `120` minutes.

The bot retries DeepSeek up to 2 times with exponential backoff. If DeepSeek fails, it uses the latest cached decision for the same side when available; otherwise entry falls back to local approval and exit falls back to local close.

AI decision quality is tracked. If AI-approved entries lose 5 times in a row, the maximum AI cap_use is limited back to the base `48%`.

## Hard Risk Controls

The bot still forces exit or blocks trading when hard limits are hit:

- hard max loss: `1.0%`;
- negative position held for `120` minutes;
- margin-level floor;
- daily loss limit;
- consecutive loss pause;
- exchange minimum order amount.

## Cost And Cloudflare Usage

DeepSeek requests go directly from the server to DeepSeek API. They do not use the Cloudflare Binance relay (if configured), so they do not increase Cloudflare Workers request usage.

Calls are event-triggered:

- one call when a local entry candidate appears;
- one call each time a soft exit threshold is reached;
- one review call when a position has been open for `30` minutes, then every `30` minutes if AI keeps holding.

They are not fixed-interval calls for every bot loop.
