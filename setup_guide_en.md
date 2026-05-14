# Setup Guide — From Zero to Running the SOLUSDT Trading Bot

This guide is for **complete beginners**. It walks through every step from creating a Binance account to running the bot.

---

## Step 1: Binance Account & API Setup

### 1.1 Register

Go to [binance.com](https://www.binance.com) and create an account. Complete identity verification (KYC).

> If the site is blocked in your region, use a reliable VPN first.

### 1.2 Enable Margin Trading

Binance web → top-left menu → **Margin** → enable **Cross Margin** account.

### 1.3 Transfer USDT to Margin

Binance App → Wallet → Transfer → from **Funding** to **Cross Margin**. Choose USDT.

> Recommended starting amount: $30~$50 USDT for testing.

### 1.4 Create API Key

**⚠️ Your API key is like your account password. Never share it!**

1. Binance web → top-right avatar → **API Management**
2. Click **Create API**
3. Choose **System-generated** (not third-party)
4. Name it (e.g., "SOL_bot")
5. **Permissions** (critical):
   - ✅ Enable Spot & Margin Trading
   - ✅ Enable Futures (optional, for balance reading)
   - ✅ Enable Wallet (optional, for funding balance)
   - ❌ **Do NOT** enable withdrawals
   - ❌ **Do NOT** enable transfers (unless you want automatic fund transfers)
6. Complete security verification (email/SMS/2FA)
7. You'll get an **API Key** and **Secret Key**. **Copy them immediately** — the Secret Key cannot be viewed again after closing.

---

## Step 2: Install Python

### 2.1 Download

1. Go to [python.org](https://www.python.org/downloads/)
2. Download the latest Python (3.10+)
3. During installation, **check** ✅ "Add Python to PATH"
4. Click Install and wait

### 2.2 Verify

Open **Command Prompt** (Win+R → type `cmd` → Enter):

```bash
python --version
```

Should show `Python 3.x.x`.

---

## Step 3: Download the Program

### 3.1 Get the Code

Download this project as a ZIP from GitHub and extract it, or clone the repository.

### 3.2 Navigate to the Folder

In Command Prompt:

```bash
cd C:\Users\YourName\Desktop\github
```

### 3.3 Install Dependencies

```bash
pip install ccxt numpy requests
```

Wait for installation (~1-2 minutes).

---

## Step 4: Configuration

### 4.1 Create Config File

Copy `config.example.json` and rename it to `config.json`.

### 4.2 Edit config.json

Open `config.json` with Notepad and fill in:

```json
{
    "api_key": "your_api_key_here",
    "secret": "your_secret_key_here",
    "testnet": false,
    "proxy": "http://127.0.0.1:7890",
    "usd_cny_rate": 7.25,
    "initial_equity": 30
}
```

| Field | Description | Required |
|-------|-------------|:---:|
| `api_key` | Binance API Key | ✅ |
| `secret` | Binance Secret Key | ✅ |
| `proxy` | HTTP proxy address | See below |
| `usd_cny_rate` | USD→CNY exchange rate | No |
| `initial_equity` | Starting capital (USD) for P&L tracking | No |

### 4.3 Proxy Configuration

If `api.binance.com` is blocked in your region, you need a proxy.

1. Open your proxy software (Clash/v2rayN/etc.)
2. Find the HTTP proxy port:

| Proxy Software | Default Port |
|----------------|--------------|
| Clash / Clash Verge | 7890 or 7897 |
| v2rayN | 10809 |
| Shadowsocks | 1080 |

3. Put the port in `config.json`:
```json
"proxy": "http://127.0.0.1:7890"
```

> If you can access Binance without a proxy, set `proxy` to `""`.

---

## Step 5: Run

### 5.1 Start the Trading Bot

```bash
python MainProgramme.py
```

On startup it will:
- Display balances across all accounts
- Sync any existing positions
- Enter the strategy loop (checks every 5 minutes)

To close all positions:
```bash
python MainProgramme.py --close-all
```

### 5.2 Start the Dashboard

Open a **second** Command Prompt window:

```bash
python monitor.py
```

Open your browser and go to **http://localhost:8888** to view real-time assets and trade history.

---

## Step 6: Verify Everything Works

### 6.1 Check Console Output

A healthy startup looks like:
```
[INFO] 使用代理: http://127.0.0.1:7897    ← Proxy connected
[INFO] API就绪 | 杠杆交易模式               ← API connected
[INFO] 杠杆账户: 30.57 ← 交易用            ← Balance read correctly
[INFO] [12:03:46] SOL=96.12 | 净值=30.2 |  ← Strategy running
```

### 6.2 Check Dashboard

Open `http://localhost:8888`. You should see:
- Total equity
- Margin account balance
- SOL price
- Margin level

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| `Read timed out` | Proxy not running or wrong port | Check proxy software, verify port |
| `Connection refused` | Wrong proxy address | Verify `127.0.0.1:port` is correct |
| `Insufficient balance` | Margin account balance too low | Transfer more USDT to margin |
| `Invalid API key` | Key mistyped or deleted | Re-create API key |
| `Permission denied` | Missing API permissions | Enable "Spot & Margin Trading" |
| Dashboard blank | Old process blocking port | Ctrl+C to stop old process, restart |
| Dashboard numbers frozen | Data still loading | Wait 30-60 seconds (proxy is slow) |

---

## Security Reminders

1. **Never share your Secret Key** — anyone with it can trade your account
2. **Do NOT enable withdrawals** — API keys only need trading permissions
3. **Set IP whitelist** — restrict API access to your IP in Binance API settings
4. **Start small** — test with minimal funds before scaling up
5. **Check logs regularly** — watch for unusual trades or errors
