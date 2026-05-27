# Setup Guide — From Zero to Running the SOLUSDT Trading Bot

This guide is for **complete beginners**. It walks through every step from creating a Binance account to running the bot on a server.

---

## Step 1: Binance Account & API Setup

### 1.1 Register

Go to [binance.com](https://www.binance.com) and create an account. Complete identity verification (KYC).

### 1.2 Enable Margin Trading

Binance web → top-left menu → **Margin** → enable **Cross Margin** account.

### 1.3 Transfer USDT to Margin

Binance App → Wallet → Transfer → from **Funding** to **Cross Margin**. Choose USDT.

> Recommended starting amount: $30~$50 USDT for testing.

### 1.4 Create Binance API Key

**⚠️ Your API key is like your account password. Never share it!**

1. Binance web → top-right avatar → **API Management**
2. Click **Create API** → **System-generated**
3. Name it (e.g., "SOL_bot")
4. **Permissions**: enable "Spot & Margin Trading" only, **disable "Withdrawals"**
5. **IP whitelist**: if using Cloudflare Worker relay, no restriction needed; for local test, add your IP
6. Save your `API Key` and `Secret Key` immediately

### 1.5 Get DeepSeek API Key (for AI risk filtering)

1. Visit [platform.deepseek.com](https://platform.deepseek.com)
2. Register an account, top up a small balance (e.g., $1)
3. Go to **API Keys** and create a key

> If you don't want DeepSeek AI yet, set `deepseek_enabled` to `false` in config. The bot will run with local rules only.

---

## Step 2: Deploy Cloudflare Worker (recommended for China users)

If `api.binance.com` is blocked in your region, deploy a free Cloudflare Worker as a relay. This ensures stable access from any server location.

### 2.1 Sign Up

Go to [dash.cloudflare.com](https://dash.cloudflare.com) and create an account. No custom domain needed.

### 2.2 Create the Worker

1. Left menu → **Workers & Pages** → **Create application** → **Create Worker**
2. Name it (e.g., `binance-relay`)
3. Click **Edit code** and paste:

```javascript
// Cloudflare Worker - Binance API Relay
const BINANCE_API = 'https://api.binance.com';

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const targetUrl = BINANCE_API + url.pathname + url.search;

    const headers = new Headers(request.headers);
    headers.set('Host', 'api.binance.com');

    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? await request.text() : undefined,
    });

    const responseHeaders = new Headers(response.headers);
    responseHeaders.set('Access-Control-Allow-Origin', '*');
    responseHeaders.set('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS');
    responseHeaders.set('Access-Control-Allow-Headers', '*');

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  }
};
```

4. Click **Deploy**
5. You'll get a URL like `https://binance-relay.yourname.workers.dev`
6. Put this URL in `config.json` → `cf_worker` field

### 2.3 Verify the Worker

Open `https://your-worker-url/api/v3/time` in browser. If you see `{"serverTime":1700000000000}`, it works.

> If you have a local proxy and don't need the Worker, leave `cf_worker` empty and use `proxy` instead.

---

## Step 3: Install Python

### 3.1 Download

Go to [python.org](https://www.python.org/downloads/), download Python 3.10+, install with ✅ "Add Python to PATH".

### 3.2 Verify

```bash
python --version
```

Should show `Python 3.x.x`.

---

## Step 4: Get the Code & Install Dependencies

### 4.1 Download

Download this project as ZIP from GitHub and extract it.

### 4.2 Install

```bash
cd path/to/github
pip install ccxt numpy requests
```

---

## Step 5: Configure

### 5.1 Create Config

Copy `config.example.json` and rename to `config.json`.

### 5.2 Edit config.json

```json
{
    "api_key": "your_binance_api_key",
    "secret": "your_binance_secret",
    "proxy": "",
    "cf_worker": "https://binance-relay.yourname.workers.dev",
    "deepseek_api_key": "your_deepseek_key",
    "deepseek_enabled": true,
    "deepseek_model": "deepseek-chat",
    "dashboard_password": "",
    "dashboard_admin_password": "",
    "usd_cny_rate": 7.25,
    "initial_equity": 30,
    "initial_equity_cny": 0,
    "db_keep_days": 90,
    "feature_flags": {
        "signal_volume_filter": true,
        "signal_ema_trend_filter": true,
        "signal_bb_width_filter": true,
        "signal_rsi_divergence": true,
        "multi_timeframe_ai_context": true,
        "multi_timeframe_filter": false,
        "limit_ioc_orders": false,
        "sqlite_storage": false,
        "sqlite_read_enabled": false
    }
}
```

| Field | Description | Required |
|-------|-------------|:---:|
| `api_key` | Binance API Key | ✅ |
| `secret` | Binance Secret Key | ✅ |
| `proxy` | HTTP proxy (mutually exclusive with cf_worker) | Conditional |
| `cf_worker` | Cloudflare Worker relay URL (mutually exclusive with proxy) | Conditional |
| `deepseek_api_key` | DeepSeek API Key (leave empty if disabled) | No |
| `deepseek_enabled` | Enable DeepSeek AI filtering | No |
| `dashboard_password` | Dashboard access password (empty = no password) | No |
| `dashboard_admin_password` | Admin password (can view access logs) | No |
| `usd_cny_rate` | USD to CNY exchange rate | No |
| `initial_equity` | Starting capital in USD (for P&L tracking) | No |
| `feature_flags` | Feature toggles, see comments | No |

> **proxy vs cf_worker**: use one or the other. If you deployed a Cloudflare Worker, keep proxy empty and fill cf_worker.

---

## Step 6: Run

### 6.1 Start the Bot

```bash
python MainProgramme.py
```

Emergency close all positions:
```bash
python MainProgramme.py --close-all
```

### 6.2 Start the Dashboard

Open a second terminal:

```bash
python monitor.py
```

Open **http://localhost:8888** in browser.

---

## Step 7: Deploy to a Server (optional)

For 24/7 operation, deploy to a cloud server:

1. Rent a lightweight cloud server (2-core 2GB RAM is enough)
2. Upload the code to the server
3. Install Python and dependencies
4. Configure `config.json` (no proxy needed, use cf_worker directly)
5. Background run bot: `nohup python MainProgramme.py &`
6. Background run dashboard: `nohup python monitor.py &`
7. Open port 8888 in server firewall for dashboard access

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| `Read timed out` | Network blocked | Check proxy or cf_worker config |
| `Connection refused` | Wrong proxy address | Verify proxy port |
| `Insufficient balance` | Margin balance too low | Transfer more USDT to margin |
| `Invalid API key` | Key mistyped or deleted | Re-create API key |
| `Permission denied` | Missing API permissions | Enable "Spot & Margin Trading" |
| Dashboard blank | Port in use | Ctrl+C old process, restart |
| Worker returns 403 | IP blocked by Binance | Switch Cloudflare region or use local proxy |

---

## Security Reminders

1. **Never share your Secret Key**
2. **Disable withdrawals** — API keys only need trading permissions
3. **Set dashboard password** — if exposing to the internet, always set a password
4. **Start small** — test with minimal funds first
5. **Check logs regularly** — watch for unusual trades or errors
