# 配置说明 — 从零开始运行 SOLUSDT 交易机器人

本文档面向**零基础用户**，手把手教你从无到有完成配置。

---

## 第一步：注册币安并开通 API

### 1.1 注册币安账户

访问 [binance.com](https://www.binance.com) 注册账户，完成身份认证（KYC）。

### 1.2 开通杠杆交易

进入币安网页版 → 左上角菜单 → **杠杆交易** → 开通**全仓杠杆**账户。

### 1.3 向杠杆账户转入 USDT

币安 App → 钱包 → 划转 → 从**资金账户**划转 USDT 到**杠杆账户**。

> 建议初始转入 $30~$50 USDT 进行测试。

### 1.4 创建币安 API 密钥

**⚠️ 密钥等于你的账户密码，绝对不能泄露！**

1. 币安网页版 → 右上角头像 → **API 管理**
2. 点击 **创建 API**
3. 选择 **系统生成**（不要选第三方）
4. 给密钥起个名字（如 "SOL_bot"）
5. **权限设置**：只勾选"允许现货及杠杆交易"，**不要勾选"允许提现"**
6. **IP 白名单**：如用 Cloudflare Worker 中继则不限IP，本地测试填你当前的IP
7. 保存好 `API Key` 和 `Secret Key`（Secret Key 只显示一次）

### 1.5 申请 DeepSeek API 密钥（AI 风控需要）

1. 访问 [platform.deepseek.com](https://platform.deepseek.com)
2. 注册账户，充值少量余额（如 ¥10，API 调用很便宜）
3. 在 **API Keys** 页面创建 Key，保存备用

> 如果暂时不接入 DeepSeek AI，可在配置中将 `deepseek_enabled` 设为 `false`，程序仍可正常运行（仅靠本地规则判断）。

---

## 第二步：部署 Cloudflare Worker（推荐，解决国内网络问题）

如果你在中国大陆，币安 API (`api.binance.com`) 无法直接访问。除了本地代理外，**推荐部署一个免费的 Cloudflare Worker 作为中继**，这样服务器无论在哪里都能稳定访问币安。

### 2.1 注册 Cloudflare

1. 访问 [dash.cloudflare.com](https://dash.cloudflare.com) 注册账户
2. 不需要有自己的域名，Cloudflare 会分配 `*.workers.dev` 子域名

### 2.2 创建 Worker

1. 左侧菜单 → **Workers & Pages** → **创建应用程序** → **创建 Worker**
2. 给 Worker 起个名字（如 `binance-relay`）
3. 点击 **编辑代码**，把下面代码粘贴进去：

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

4. 点击 **部署**
5. 部署成功后你会得到一个 URL，类似 `https://binance-relay.你的用户名.workers.dev`
6. 把这个 URL 填入 `config.json` 的 `cf_worker` 字段

### 2.3 验证 Worker

在浏览器打开 `https://你的worker域名/api/v3/time`，如果返回类似 `{"serverTime":1700000000000}` 的 JSON，说明 Worker 部署成功。

> 如果你有本地代理且不需要 Worker，把 `cf_worker` 设为空字符串 `""`，改用 `proxy` 字段即可。

---

## 第三步：安装 Python 环境

### 3.1 下载 Python

1. 打开 [python.org](https://www.python.org/downloads/)
2. 下载最新版 Python（3.10+）
3. 安装时**务必勾选** ✅ "Add Python to PATH"

### 3.2 验证安装

打开**命令提示符**（Win+R → 输入 `cmd`），输入：

```bash
python --version
```

显示 `Python 3.x.x` 即成功。

---

## 第四步：下载程序并安装依赖

### 4.1 获取代码

从 GitHub 下载本项目 ZIP 并解压到任意位置。

### 4.2 安装依赖

在命令提示符中进入项目文件夹，执行：

```bash
pip install ccxt numpy requests
```

---

## 第五步：配置程序

### 5.1 创建配置文件

将 `config.example.json` 复制一份，重命名为 `config.json`。

### 5.2 编辑 config.json

用记事本打开 `config.json`，填入：

```json
{
    "api_key": "你的币安API_KEY",
    "secret": "你的币安SECRET_KEY",
    "proxy": "",
    "cf_worker": "https://binance-relay.你的用户名.workers.dev",
    "deepseek_api_key": "你的DeepSeek_API_KEY",
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

| 字段 | 说明 | 必填 |
|------|------|:---:|
| `api_key` | 币安 API Key | ✅ |
| `secret` | 币安 Secret Key | ✅ |
| `proxy` | HTTP 代理地址（与 cf_worker 二选一） | 视情况 |
| `cf_worker` | Cloudflare Worker 中继地址（与 proxy 二选一） | 视情况 |
| `deepseek_api_key` | DeepSeek API Key（不用 AI 可留空，设 enabled=false） | 否 |
| `deepseek_enabled` | 是否启用 DeepSeek AI | 否 |
| `dashboard_password` | 仪表盘访问密码（留空则无需密码） | 否 |
| `dashboard_admin_password` | 仪表盘管理员密码（可查看访问记录） | 否 |
| `usd_cny_rate` | 美元兑人民币汇率 | 否 |
| `initial_equity` | 初始本金 USD（用于计算总盈亏） | 否 |
| `feature_flags` | 功能开关，见注释 | 否 |

> **proxy 和 cf_worker 二选一**：如果部署了 Cloudflare Worker，proxy 留空，只填 cf_worker 即可。如果用本地代理，反之。

---

## 第六步：运行程序

### 6.1 启动交易机器人

```bash
python MainProgramme.py
```

启动后会自动显示各账户余额、同步持仓、进入策略循环。

紧急平仓：
```bash
python MainProgramme.py --close-all
```

### 6.2 启动监控仪表盘

打开**另一个**命令提示符窗口：

```bash
python monitor.py
```

浏览器打开 **http://localhost:8888** 查看实时资产和交易记录。

---

## 第七步：部署到服务器（可选）

把你的服务器也部署一套，实现 7×24 运行：

1. 买一台云服务器（腾讯云/阿里云轻量应用服务器即可，2核2G足够）
2. 上传代码到服务器
3. 安装 Python 和依赖
4. 配置 `config.json`（此时不需要 proxy，直接用 cf_worker）
5. 用 `nohup python MainProgramme.py &` 后台运行交易
6. 用 `nohup python monitor.py &` 后台运行仪表盘
7. 在服务器安全组开放仪表盘端口（默认 8888）

---

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `Read timed out` | 网络不通 | 检查 proxy 或 cf_worker 配置 |
| `Connection refused` | 代理地址错误 | 确认代理端口正确 |
| `Insufficient balance` | 杠杆账户余额不足 | 向杠杆账户转入更多 USDT |
| `Invalid API key` | 密钥填错或已删除 | 重新创建 API 密钥 |
| `Permission denied` | API 权限不足 | 检查是否勾选"允许现货及杠杆交易" |
| 仪表盘空白 | 端口被占用 | Ctrl+C 停掉旧进程再重启 |
| Worker 返回 403 | IP 限制 | 币安可能封了 Cloudflare IP，可换区域或改用代理 |

---

## 安全提醒

1. **永远不要分享 API Secret Key**
2. **不要勾选"允许提现"** — API 密钥只需要交易权限
3. **设仪表盘密码** — 如果在公网部署，务必设置 `dashboard_password`
4. **先小资金测试** — 确认流程跑通后再增加投入
5. **定期检查日志** — 关注异常交易和错误信息
