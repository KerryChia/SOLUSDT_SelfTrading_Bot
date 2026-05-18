"""
SOLUSDT 资产监控仪表盘 v4
修复：做空负资产 | 图表不重绘 | 显示开仓均价
"""

import os, sys, json, time, logging, threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import ccxt
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Monitor")

def resolve_port(default: int = 8888) -> int:
    env_port = os.environ.get("SOLUSDT_MONITOR_PORT")
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                pass
        if arg.startswith("--port="):
            try:
                return int(arg.split("=", 1)[1])
            except ValueError:
                pass
    return default


PORT = resolve_port()
SYMBOL = "SOL/USDT"
META_KEYS = {"info", "free", "used", "total", "timestamp", "datetime",
             "debt", "borrowed", "interest", "net", "currency", "free_margin",
             "used_margin", "equity", "unrealized_pnl", "margin_ratio", "position"}


class DataFetcher:
    def __init__(self, api_key: str, secret: str, proxy: str = "", config: dict = None):
        base = {"apiKey": api_key, "secret": secret, "enableRateLimit": True, "timeout": 30000}
        self.proxy = proxy
        if proxy:
            base["proxies"] = {"http": proxy, "https": proxy}
            logger.info(f"使用代理: {proxy}")

        self.spot = ccxt.binance({**base, "options": {"defaultType": "spot"}})
        self.margin = ccxt.binance({**base, "options": {"defaultType": "margin"}})
        self.futures = ccxt.binance({**base, "options": {"defaultType": "future"}})
        for name, ex in [("spot", self.spot), ("margin", self.margin), ("futures", self.futures)]:
            for attempt in range(5):
                try:
                    ex.load_markets()
                    break
                except Exception as e:
                    if attempt < 4:
                        wait = (attempt + 1) * 5
                        logger.warning(f"{name}加载失败(尝试{attempt+1}/5): {e}，{wait}秒后重试...")
                        time.sleep(wait)
                    else:
                        raise
        self._cache = {}
        self._lock = threading.Lock()

        self._cny_rate = float(config.get("usd_cny_rate", 7.25) if config else 7.25)
        self._initial_equity = float(config.get("initial_equity", 0) if config else 0)
        self._history_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
        self._position_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "position.json")
        self._trade_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.json")
        self._last_snapshot_hour = -1
        self._load_history()

    def _fetch_cny_rate(self):
        try:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
            r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=8, proxies=proxies)
            self._cny_rate = float(r.json()["rates"]["CNY"])
        except Exception:
            pass

    def _load_history(self):
        try:
            self._history = json.load(open(self._history_file)) if os.path.exists(self._history_file) else []
        except Exception:
            self._history = []

    def _save_snapshot(self, equity: float):
        now = datetime.now()
        hb = now.hour  # 每小时一个节点
        if hb == self._last_snapshot_hour:
            return
        self._last_snapshot_hour = hb
        cutoff = time.time() - 3 * 86400
        self._history = [h for h in self._history if h["t"] / 1000 > cutoff]
        # 避免同一时段重复
        if not any(h for h in self._history if
                   h["t"] > (time.time() - 86400) * 1000 and
                   datetime.fromtimestamp(h["t"] / 1000).hour == hb):
            self._history.append({"t": int(time.time() * 1000), "v": round(equity, 2)})
        try:
            json.dump(self._history, open(self._history_file, "w"))
        except Exception:
            pass

    def _read_position_file(self) -> dict:
        """读取 bot 写入的持仓文件"""
        try:
            if os.path.exists(self._position_file):
                with open(self._position_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _read_trades_file(self) -> list:
        try:
            if os.path.exists(self._trade_file):
                with open(self._trade_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    @staticmethod
    def _is_asset(key: str, info) -> bool:
        if key in META_KEYS:
            return False
        if not isinstance(info, dict):
            return False
        return "free" in info or "total" in info

    def _extract(self, ex, label: str, prices: dict) -> list:
        rows = []
        try:
            b = ex.fetch_balance()
        except Exception:
            return rows
        for asset, info in b.items():
            if not self._is_asset(asset, info):
                continue
            total = float(info.get("total", 0) or 0)
            free = float(info.get("free", 0) or 0)
            borrowed = float(info.get("debt", info.get("borrowed", 0)) or 0)
            # DEBUG: 打印SOL的详细数据
            if asset == "SOL" and label == "杠杆账户":
                logger.info(f"[DEBUG] {label} SOL: total={total:.6f} free={free:.6f} borrowed={borrowed:.6f} net={total-borrowed:.6f}")
            if total <= 0 and borrowed <= 0:
                continue
            price = prices.get(asset, 0)
            net = total - borrowed  # 做空时 net 为负
            value = net * price
            rows.append(dict(asset=asset, total=total, free=free,
                             borrowed=borrowed, net=net, price=price,
                             value=value, account=label))
        return rows

    def _get_funding(self) -> list:
        rows = []
        try:
            resp = self.spot.sapiGetAssetGetFundingAsset()
            for item in resp:
                free = float(item.get("free", 0) or 0)
                locked = float(item.get("locked", 0) or 0)
                total = free + locked
                if total <= 0:
                    continue
                rows.append(dict(asset=item.get("asset", ""), total=total, free=free,
                                 borrowed=0, net=total, price=0, value=0, account="资金账户"))
        except Exception:
            pass
        return rows

    def refresh(self):
        data = {}
        try:
            self._fetch_cny_rate()

            # SOL行情
            sol_t = self.spot.fetch_ticker(SYMBOL)
            sol_price = float(sol_t["last"])
            prices = {"USDT": 1.0, "SOL": sol_price}
            try:
                prices["BTC"] = float(self.spot.fetch_ticker("BTC/USDT")["last"])
            except Exception:
                prices["BTC"] = 0

            # 读取持仓信息 + 交易历史 (bot写入的)
            pos_info = self._read_position_file()
            trade_history = self._read_trades_file()

            # 四个账户
            funding_a = self._get_funding()
            spot_a = self._extract(self.spot, "现货账户", prices)
            margin_a = self._extract(self.margin, "杠杆账户", prices)
            futures_a = self._extract(self.futures, "合约账户", prices)

            # 补充未知资产价格
            for a in funding_a + spot_a + margin_a + futures_a:
                if a["asset"] not in prices:
                    try:
                        prices[a["asset"]] = float(self.spot.fetch_ticker(f"{a['asset']}/USDT")["last"])
                    except Exception:
                        prices[a["asset"]] = 0

            # 重新算value
            for lst in [funding_a, spot_a, margin_a, futures_a]:
                for a in lst:
                    a["price"] = prices.get(a["asset"], 0)
                    a["value"] = a["net"] * a["price"]

            funding_total = sum(a["value"] for a in funding_a)
            spot_total = sum(a["value"] for a in spot_a)
            margin_total = sum(a["value"] for a in margin_a)
            futures_total = sum(a["value"] for a in futures_a)
            total_equity = funding_total + spot_total + margin_total + futures_total

            self._save_snapshot(total_equity)

            # K线
            klines = self.spot.fetch_ohlcv(SYMBOL, timeframe="5m", limit=50)
            price_history = [{"t": k[0], "o": k[1], "h": k[2], "l": k[3], "c": k[4]} for k in klines]

            # 保证金率
            margin_level = 999.0
            margin_asset = 0.0
            margin_debt = 0.0
            for a in margin_a:
                if a["value"] > 0:
                    margin_asset += a["total"] * a["price"]
                margin_debt += a["borrowed"] * a["price"]
            if margin_debt > 0:
                margin_level = (margin_asset / margin_debt) * 100

            # 今日盈亏 = 当前净值 - 今日首个快照净值
            today_start = None
            today_start_ts = time.time() - 86400
            for h in sorted(self._history, key=lambda x: x["t"]):
                if h["t"] / 1000 > today_start_ts:
                    today_start = h["v"]
                    break
            daily_pnl = total_equity - today_start if today_start else 0
            daily_pnl_pct = (daily_pnl / today_start * 100) if today_start and today_start > 0 else 0

            # 总盈亏 = 当前净值 - 初始净值
            total_pnl = total_equity - self._initial_equity if self._initial_equity > 0 else 0
            total_pnl_pct = (total_pnl / self._initial_equity * 100) if self._initial_equity > 0 else 0

            data = {
                "funding_total": funding_total, "funding_assets": funding_a,
                "spot_total": spot_total, "spot_assets": spot_a,
                "margin_total": margin_total, "margin_assets": margin_a,
                "futures_total": futures_total, "futures_assets": futures_a,
                "total_equity": total_equity,
                "margin_level": round(margin_level, 1),
                "daily_pnl": round(daily_pnl, 2),
                "daily_pnl_pct": round(daily_pnl_pct, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl_pct, 1),
                "initial_equity": self._initial_equity,
                "cny_rate": self._cny_rate,
                "total_cny": round(total_equity * self._cny_rate, 2),
                "equity_history": list(self._history),
                "position": pos_info,
                "trade_history": trade_history,
                "sol_price": sol_price,
                "sol_change_24h": float(sol_t.get("percentage", 0) or 0),
                "price_history": price_history,
                "updated_at": datetime.now().strftime("%H:%M:%S"),
            }
            with self._lock:
                self._cache = data
        except Exception as e:
            logger.error(f"刷新失败: {e}")

    def get(self) -> dict:
        with self._lock:
            return dict(self._cache)


# ============================================================
DASHBOARD_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SOLUSDT 交易仪表盘</title>
<script async src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
 onerror="var s=document.createElement('script');s.src='https://unpkg.com/chart.js@4.4.0/dist/chart.umd.min.js';document.head.appendChild(s);"></script>
<style>
  :root {
    --bg:#f5f6fa; --card:#fff; --border:#e2e5ee; --text:#2d3436; --sub:#636e72;
    --blue:#3867d6; --green:#20bf6b; --red:#eb3b5a; --yellow:#f7b731; --purple:#8854d0;
    --shadow:0 2px 12px rgba(0,0,0,.05);
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh;padding:20px}
  .header{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;background:var(--card);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);margin-bottom:16px}
  .header h1{font-size:22px}.header h1 span{color:var(--blue)}
  .status{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--sub)}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
  .phase{padding:4px 14px;border-radius:20px;font-size:12px;font-weight:700;background:#fff3cd;color:#856404}
  .pos-tag{padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;margin-left:8px}
  .pos-long{background:#e8f5e9;color:#20bf6b}
  .pos-short{background:#fde8e8;color:#eb3b5a}

  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:16px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 20px;box-shadow:var(--shadow)}
  .card .label{font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
  .card .value{font-size:26px;font-weight:700;margin:4px 0}
  .card .sub{font-size:12px;color:var(--sub)}

  .row2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}
  @media(max-width:820px){.row2{grid-template-columns:1fr}}
  .panel{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px 22px;box-shadow:var(--shadow)}
  .panel h3{font-size:15px;margin-bottom:14px;font-weight:600}

  .acct-section{margin-bottom:14px}
  .acct-section h4{font-size:13px;margin-bottom:6px;padding-bottom:4px;border-bottom:2px solid #e2e5ee}
  .acct-section h4.funding{color:#f7b731;border-color:#f7b731}
  .acct-section h4.spot{color:var(--blue);border-color:var(--blue)}
  .acct-section h4.margin{color:var(--purple);border-color:var(--purple)}
  .acct-section h4.futures{color:var(--red);border-color:var(--red)}

  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
  th{color:var(--sub);font-weight:500;font-size:11px;text-transform:uppercase}
  .tag{display:inline-block;padding:2px 10px;border-radius:14px;font-size:11px;font-weight:600}
  .tag-fund{background:#fff8e1;color:#b8860b}
  .tag-spot{background:#e8f0fe;color:var(--blue)}
  .tag-margin{background:#f3e8ff;color:var(--purple)}
  .tag-fut{background:#ffe8e8;color:var(--red)}
  .val-neg{color:var(--red);font-weight:600}
  .footer{text-align:center;padding:14px;color:#b2bec3;font-size:12px}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>SOLUSDT <span>交易仪表盘</span></h1>
    <span style="font-size:13px;color:var(--sub)" id="positionLabel"></span>
  </div>
  <div style="text-align:right">
    <div class="status"><span class="dot"></span><span id="updateTime">--</span></div>
    <span class="phase" id="phaseLabel">--</span>
  </div>
</div>

<!-- 2×4 卡片 -->
<div class="cards" style="grid-template-columns:repeat(4,1fr)">
  <div class="card">
    <div class="label">总资产 (净)</div>
    <div class="value" style="color:var(--blue)" id="totalEquity">--</div>
    <div class="sub">≈ ¥<span id="totalCNY">--</span></div>
  </div>
  <div class="card">
    <div class="label">杠杆账户 (净)</div>
    <div class="value" style="color:var(--purple)" id="marginTotal">--</div>
    <div class="sub" id="marginDetail">--</div>
  </div>
  <div class="card">
    <div class="label">SOLUSDT 价格</div>
    <div class="value" id="solPrice">--</div>
    <div class="sub" id="solChange">--</div>
  </div>
  <div class="card">
    <div class="label">保证金率</div>
    <div class="value" id="marginLevel">--</div>
    <div class="sub">底线 <span id="marginFloor">--</span></div>
  </div>
  <div class="card">
    <div class="label">今日盈亏</div>
    <div class="value" id="dailyPnl">--</div>
    <div class="sub" id="dailyPnlPct">--</div>
  </div>
  <div class="card">
    <div class="label">总盈亏 (初始$30)</div>
    <div class="value" id="totalPnl">--</div>
    <div class="sub" id="totalPnlPct">--</div>
  </div>
  <div class="card">
    <div class="label">当前持仓</div>
    <div class="value" style="font-size:20px" id="posCard">无</div>
    <div class="sub" id="posCardSub">--</div>
  </div>
  <div class="card">
    <div class="label">今日交易</div>
    <div class="value" style="font-size:22px" id="tradeCount">0 笔</div>
    <div class="sub" id="lastTrade">--</div>
  </div>
</div>

<!-- 中间：左(资产负债+策略) | 右(交易记录) -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px">
  <div style="display:flex;flex-direction:column;gap:14px">
    <div class="panel">
      <h3>资产负债</h3>
      <div style="max-height:250px"><canvas id="pieChart"></canvas></div>
    </div>
    <div class="panel">
      <h3>策略 & 持仓</h3>
      <div id="strategyInfo" style="font-size:13px;line-height:2">加载中...</div>
    </div>
  </div>
  <div class="panel">
    <h3>交易记录</h3>
    <div id="tradeHistory" style="max-height:530px;overflow-y:auto">
      <table><tr><td style="color:var(--sub)">暂无交易记录</td></tr></table>
    </div>
  </div>
</div>

<div class="row2">
  <div class="panel">
    <h3>SOLUSDT 5分钟K线</h3>
    <div style="height:240px"><canvas id="priceChart"></canvas></div>
  </div>
  <div class="panel">
    <h3>资产明细</h3>
    <div id="assetDetail" style="max-height:240px;overflow-y:auto">加载中...</div>
  </div>
</div>

<div class="row2">
  <div class="panel" style="grid-column:1/-1">
    <h3>总资产趋势 (3天 · 1小时间隔) | ¥<span id="trendCNY">--</span></h3>
    <div style="height:260px"><canvas id="trendChart"></canvas></div>
  </div>
</div>

<div class="footer">SOLUSDT Monitor | 10秒刷新 | $1=¥<span id="cnyRate">--</span> | <span id="footerTime">--</span></div>

<script>
let pieChart=null,priceChart=null,trendChart=null;

async function fetchData(){
  try{const r=await fetch('/api/data');return await r.json()}catch(e){return null}
}

function updateUI(d){
  if(!d)return;
  document.getElementById('updateTime').textContent='更新 '+d.updated_at;
  document.getElementById('footerTime').textContent=d.updated_at;

  const eq=d.total_equity||0;
  const cny=d.cny_rate||7.25;
  document.getElementById('totalEquity').textContent='$'+eq.toFixed(2);
  document.getElementById('totalCNY').textContent=(eq*cny).toFixed(2);
  document.getElementById('cnyRate').textContent=cny.toFixed(4);
  document.getElementById('trendCNY').textContent=(eq*cny).toFixed(2);

  // 杠杆
  const mt=d.margin_total||0;
  document.getElementById('marginTotal').textContent='$'+mt.toFixed(2);
  const ma=d.margin_assets||[];
  const mUSDT=ma.find(a=>a.asset==='USDT');
  const mSOL=ma.find(a=>a.asset==='SOL');
  let mDetail='';
  if(mUSDT) mDetail+='USDT $'+mUSDT.net.toFixed(2)+' ';
  if(mSOL){const s=mSOL.net;mDetail+='SOL '+(s>=0?'+':'')+s.toFixed(4)+(s<0?' (做空负债)':'');}
  document.getElementById('marginDetail').textContent=mDetail||'--';

  // SOL价格
  document.getElementById('solPrice').textContent='$'+(d.sol_price||0).toFixed(3);
  const chg=d.sol_change_24h||0;
  const ce=document.getElementById('solChange');
  ce.textContent=(chg>=0?'+':'')+chg.toFixed(2)+'% 24H';
  ce.style.color=chg>=0?'var(--green)':'var(--red)';

  // 阶段
  let ph='激进起步';
  if(eq>=500)ph='保守复利';else if(eq>=300)ph='降杠防回撤';else if(eq>=100)ph='稳步增长';else if(eq>=50)ph='滚雪球中';
  // 保证金率 + 动态底线
  const ml=d.margin_level||999;
  const mlEl=document.getElementById('marginLevel');
  mlEl.textContent=ml.toFixed(0)+'%';
  mlEl.style.color=ml<130?'var(--red)':ml<180?'var(--yellow)':'var(--green)';
  const posData=d.position||{};
  const posVal=posData.side?Math.abs(posData.amount||0)*(d.sol_price||0):0;
  const safeEq=eq||1;
  const usage=posVal/safeEq;
  const floor=usage<0.3?150:usage<0.7?138:130;
  document.getElementById('marginFloor').textContent=floor+'%';

  // 今日盈亏
  const dp=d.daily_pnl||0;
  const dpEl=document.getElementById('dailyPnl');
  dpEl.textContent=(dp>=0?'+':'')+'$'+dp.toFixed(2);
  dpEl.style.color=dp>=0?'var(--green)':'var(--red)';
  document.getElementById('dailyPnlPct').textContent=(dp>=0?'+':'')+(d.daily_pnl_pct||0).toFixed(2)+'%';

  // 总盈亏
  const tp=d.total_pnl||0;
  const tpEl=document.getElementById('totalPnl');
  tpEl.textContent=(tp>=0?'+':'')+'$'+tp.toFixed(2);
  tpEl.style.color=tp>=0?'var(--green)':'var(--red)';
  document.getElementById('totalPnlPct').textContent=(tp>=0?'+':'')+(d.total_pnl_pct||0).toFixed(1)+'%';

  document.getElementById('phaseLabel').textContent=ph;

  // 持仓标签 + 持仓卡片 + 交易计数
  const pos=d.position||{};
  const posCard=document.getElementById('posCard');
  const posCardSub=document.getElementById('posCardSub');
  if(pos.side){
    const tag=document.createElement('span');
    tag.className='pos-tag '+(pos.side==='long'?'pos-long':'pos-short');
    tag.textContent=(pos.side==='long'?'多头 '+pos.amount+' SOL':'空头 '+pos.amount+' SOL')+
      (pos.entry_price?' @ $'+Number(pos.entry_price).toFixed(2):'');
    document.getElementById('positionLabel').innerHTML='';
    document.getElementById('positionLabel').appendChild(tag);
    // 持仓卡片
    posCard.textContent=(pos.side==='long'?'多':'空')+' '+parseFloat(pos.amount).toFixed(3)+' SOL';
    posCard.style.color=pos.side==='long'?'var(--green)':'var(--red)';
    const curP=d.sol_price||0;
    const ep=parseFloat(pos.entry_price||curP);
    const upnl=pos.side==='long'?((curP-ep)/ep*100):((ep-curP)/ep*100);
    posCardSub.textContent='均价$'+ep.toFixed(2)+' | 浮'+(upnl>=0?'+':'')+upnl.toFixed(1)+'%';
  }else{
    document.getElementById('positionLabel').textContent='无持仓';
    posCard.textContent='无持仓';
    posCard.style.color='var(--sub)';
    posCardSub.textContent='--';
  }

  // 今日交易计数
  const trades=d.trade_history||[];
  const today=new Date().toDateString();
  const todayTrades=trades.filter(t=>new Date(t.time).toDateString()===today);
  document.getElementById('tradeCount').textContent=todayTrades.length+' 笔';
  const lastT=trades.length>0?trades[trades.length-1]:null;
  document.getElementById('lastTrade').textContent=lastT?lastT.action+' '+lastT.side+' '+lastT.amount+'SOL':'--';

  // 资产明细
  buildAssetDetail(d);
  buildTradeHistory(d);
  // 策略
  buildStrategy(d);

  if(typeof Chart==='undefined'){
    ['pieChart','priceChart','trendChart'].forEach(id=>{
      const c=document.getElementById(id);
      if(c){c.style.display='none';
      const p=c.parentElement;
      const msg=p.querySelector('.chart-error');
      if(!msg){const m=document.createElement('p');m.className='chart-error';m.style.cssText='color:#b2bec3;text-align:center;padding:40px';m.textContent='图表组件加载中...';p.appendChild(m)}
      }
    });
  }else{
    try{updateBarChart(d)}catch(e){console.error(e)}
    try{updatePriceChart(d)}catch(e){console.error(e)}
    try{updateTrendChart(d)}catch(e){console.error(e)}
  }
}

function buildAssetDetail(d){
  const groups=[
    {id:'funding',title:'资金账户',cls:'funding',tag:'tag-fund'},
    {id:'spot',title:'现货账户',cls:'spot',tag:'tag-spot'},
    {id:'margin',title:'杠杆账户',cls:'margin',tag:'tag-margin'},
    {id:'futures',title:'合约账户',cls:'futures',tag:'tag-fut'}
  ];
  let html='';
  groups.forEach(g=>{
    const assets=d[g.id+'_assets']||[];
    if(!assets.length)return;
    html+='<div class="acct-section"><h4 class="'+g.cls+'">'+g.title+'</h4><table>'+
      '<tr><th>资产</th><th>净数量</th><th>估值(USDT)</th><th>借入</th></tr>';
    assets.forEach(a=>{
      const netClass=a.net<0?' val-neg':'';
      html+='<tr>'+
        '<td><span class="tag '+g.tag+'">'+a.asset+'</span></td>'+
        '<td class="'+netClass+'">'+(a.net||0).toFixed(6)+'</td>'+
        '<td class="'+netClass+'">$'+a.value.toFixed(3)+'</td>'+
        '<td>'+(a.borrowed>0?'<span style="color:var(--red)">'+a.borrowed.toFixed(6)+'</span>':'0')+'</td>'+
        '</tr>';
    });
    html+='</table></div>';
  });
  document.getElementById('assetDetail').innerHTML=html||'<p style="color:var(--sub);text-align:center;padding:20px">暂无资产</p>';
}

function buildTradeHistory(d){
  const trades=d.trade_history||[];
  if(!trades.length){
    document.getElementById('tradeHistory').innerHTML='<table><tr><td style="color:var(--sub)">暂无交易记录</td></tr></table>';
    return;
  }
  let html='<table><tr><th>时间</th><th>动作</th><th>方向</th><th>数量</th><th>价格</th><th>盈亏</th></tr>';
  trades.slice().reverse().slice(0,30).forEach(t=>{
    const pnlVal=parseFloat(t.pnl||0);
    const pnlStr=t.pnl!=null?'<span style="'+(pnlVal>=0?'color:var(--green)':'color:var(--red)')+'">'+(pnlVal>=0?'+':'')+pnlVal.toFixed(2)+'U</span>':'-';
    html+='<tr>'+
      '<td>'+t.time+'</td>'+
      '<td style="font-weight:600;color:'+(t.action==='开仓'?'var(--green)':'var(--red)')+'">'+t.action+'</td>'+
      '<td>'+(t.side==='long'?'多':'空')+'</td>'+
      '<td>'+parseFloat(t.amount).toFixed(3)+'</td>'+
      '<td>$'+parseFloat(t.price).toFixed(2)+'</td>'+
      '<td>'+pnlStr+'</td>'+
      '</tr>';
  });
  html+='</table>';
  document.getElementById('tradeHistory').innerHTML=html;
}

function buildStrategy(d){
  const eq=d.total_equity||0;
  const pos=d.position||{};
  const capUse=eq<50?78:eq<100?75:eq<300?65:eq<500?55:40;
  const lev=eq<500?3:2;
  const tradeAmt=eq*(capUse/100);
  const solP=d.sol_price||0;
  const posAmt=solP>0?(tradeAmt/solP).toFixed(3):'--';

  let posHtml='';
  if(pos.side){
    const entryP=Number(pos.entry_price||0).toFixed(3);
    const curP=d.sol_price||0;
    const pnl=pos.side==='long'?((curP-entryP)/entryP*100).toFixed(2):((entryP-curP)/entryP*100).toFixed(2);
    const pnlClass=pnl>=0?'color:var(--green)':'color:var(--red)';
    posHtml='<tr><td>当前持仓</td><td><span class="pos-tag '+(pos.side==='long'?'pos-long':'pos-short')+'">'+
      (pos.side==='long'?'多头':'空头')+' '+pos.amount+' SOL</span></td></tr>'+
      '<tr><td>开仓均价</td><td><strong>$'+entryP+'</strong></td></tr>'+
      '<tr><td>当前盈亏</td><td><strong style="'+pnlClass+'">'+(pnl>=0?'+':'')+pnl+'%</strong></td></tr>';
  }

  document.getElementById('strategyInfo').innerHTML='<table>'+
    (posHtml||'<tr><td>当前持仓</td><td>无</td></tr>')+
    '<tr><td>阶段 / 杠杆</td><td><span class="phase">策略</span> '+lev+'x</td></tr>'+
    '<tr><td>资金使用率</td><td>'+capUse+'%</td></tr>'+
    '<tr><td>预估仓位</td><td>$'+tradeAmt.toFixed(2)+' (≈'+posAmt+' SOL)</td></tr>'+
    '<tr><td>止损 / 止盈</td><td><span style="color:var(--red)">-1.5%</span> / <span style="color:var(--green)">+3%</span></td></tr>'+
    '</table>';
}

// ---- 图表：更新不重绘，避免闪烁 ----

function updateBarChart(d){
  const ctx=document.getElementById('pieChart').getContext('2d');
  const labels=[],values=[],colors=[];
  const groups=[
    {id:'funding',label:'资金',palette:['#f7b731','#fed330','#fae07c']},
    {id:'spot',label:'现货',palette:['#3867d6','#4b7bec','#778ca3','#a5b1c2']},
    {id:'margin',label:'杠杆',palette:['#8854d0','#a55eea','#be9fe1']},
    {id:'futures',label:'合约',palette:['#eb3b5a','#fc5c65','#f8a5a5']}
  ];
  groups.forEach(g=>{
    (d[g.id+'_assets']||[]).forEach((a,i)=>{
      if(Math.abs(a.value)>0.001){
        labels.push(g.label+'-'+a.asset);
        values.push(a.value);  // 正值向右，负值向左
        colors.push(a.value<0?'#eb3b5a':g.palette[i%g.palette.length]);
      }
    });
  });

  // 找到最大绝对值，用于对称刻度
  const maxAbs=Math.max(1, ...values.map(v=>Math.abs(v)));

  if(pieChart){
    pieChart.data.labels=labels;
    pieChart.data.datasets[0].data=values;
    pieChart.data.datasets[0].backgroundColor=colors;
    pieChart.options.scales.x.min=-maxAbs*1.2;
    pieChart.options.scales.x.max=maxAbs*1.2;
    pieChart.update('none');
  }else{
    pieChart=new Chart(ctx,{
      type:'bar',
      data:{labels,datasets:[{data:values,backgroundColor:colors,borderColor:'#fff',borderWidth:1}]},
      options:{
        responsive:true,maintainAspectRatio:true,animation:false,
        indexAxis:'y',
        plugins:{
          legend:{display:false},
          tooltip:{callbacks:{label:function(ctx){return'$'+Number(ctx.raw).toFixed(2);}}}
        },
        scales:{
          x:{
            min:-maxAbs*1.2, max:maxAbs*1.2,
            ticks:{color:'#636e72',font:{size:10},callback:v=>'$'+v.toFixed(0)},
            grid:{color:'#e2e5ee',drawTicks:true},
            title:{display:true,text:'← 做空负债                        做多资产 →',color:'#636e72',font:{size:10}}
          },
          y:{
            ticks:{color:'#2d3436',font:{size:11}},
            grid:{display:false}
          }
        }
      }
    });
  }
}

function updatePriceChart(d){
  const hist=d.price_history||[];
  const labels=hist.map(h=>{
    const dt=new Date(h.t);
    return dt.getHours().toString().padStart(2,'0')+':'+dt.getMinutes().toString().padStart(2,'0');
  });
  const closes=hist.map(h=>h.c);
  const ctx=document.getElementById('priceChart').getContext('2d');
  const isUp=closes.length>=2&&closes[closes.length-1]>=closes[0];

  if(priceChart){
    priceChart.data.labels=labels;
    priceChart.data.datasets[0].data=closes;
    priceChart.data.datasets[0].borderColor=isUp?'#20bf6b':'#eb3b5a';
    priceChart.data.datasets[0].backgroundColor=isUp?'rgba(32,191,107,.08)':'rgba(235,59,90,.08)';
    priceChart.update('none');
  }else{
    priceChart=new Chart(ctx,{
      type:'line',
      data:{labels,datasets:[{label:'SOL',data:closes,
        borderColor:isUp?'#20bf6b':'#eb3b5a',
        backgroundColor:isUp?'rgba(32,191,107,.08)':'rgba(235,59,90,.08)',
        fill:true,borderWidth:1.5,pointRadius:0,tension:0.3}]},
      options:{
        responsive:true,maintainAspectRatio:false,animation:false,
        plugins:{legend:{display:false}},
        scales:{
          x:{ticks:{color:'#636e72',maxTicksLimit:8,font:{size:10}},grid:{color:'#e2e5ee'}},
          y:{ticks:{color:'#636e72',font:{size:10}},grid:{color:'#e2e5ee'}}
        },
        interaction:{intersect:false,mode:'index'}
      }
    });
  }
}

function updateTrendChart(d){
  const history=d.equity_history||[];
  const cnyRate=d.cny_rate||7.25;
  if(history.length<2)return;
  const labels=[],values=[],cnyValues=[];
  history.forEach(h=>{
    const dt=new Date(h.t);
    labels.push((dt.getMonth()+1)+'/'+dt.getDate()+' '+dt.getHours().toString().padStart(2,'0')+':00');
    values.push(h.v);
    cnyValues.push((h.v*cnyRate).toFixed(0));
  });
  const isUp=values.length>=2&&values[values.length-1]>=values[0];
  const ctx=document.getElementById('trendChart').getContext('2d');

  if(trendChart){
    trendChart.data.labels=labels;
    trendChart.data.datasets[0].data=values;
    trendChart.data.datasets[0].borderColor=isUp?'#20bf6b':'#eb3b5a';
    trendChart.data.datasets[1].data=cnyValues;
    trendChart.update('none');
  }else{
    trendChart=new Chart(ctx,{
      type:'line',
      data:{labels,datasets:[
        {label:'USD',data:values,borderColor:isUp?'#20bf6b':'#eb3b5a',
         backgroundColor:isUp?'rgba(32,191,107,.06)':'rgba(235,59,90,.06)',
         fill:true,borderWidth:2,pointRadius:3,
         pointBackgroundColor:isUp?'#20bf6b':'#eb3b5a',tension:0.3,yAxisID:'y'},
        {label:'CNY',data:cnyValues,borderColor:'#3867d6',
         backgroundColor:'rgba(56,103,214,.04)',fill:true,borderWidth:1.5,
         pointRadius:0,tension:0.3,borderDash:[5,3],yAxisID:'y1'}
      ]},
      options:{
        responsive:true,maintainAspectRatio:false,animation:false,
        interaction:{intersect:false,mode:'index'},
        plugins:{
          legend:{position:'top',labels:{usePointStyle:true,padding:20,font:{size:11}}},
          tooltip:{callbacks:{label:function(ctx){return (ctx.dataset.label==='USD'?'$':'¥')+Number(ctx.raw).toFixed(2);}}}
        },
        scales:{
          x:{ticks:{color:'#636e72',maxTicksLimit:12,font:{size:10}},grid:{color:'#e2e5ee'}},
          y:{type:'linear',position:'left',ticks:{color:'#20bf6b',font:{size:10},callback:v=>'$'+v.toFixed(0)},grid:{color:'#e2e5ee'}},
          y1:{type:'linear',position:'right',ticks:{color:'#3867d6',font:{size:10},callback:v=>'¥'+v},grid:{display:false}}
        }
      }
    });
  }
}

window.onerror=function(msg,url,line){document.body.innerHTML='<div style=\"padding:40px;color:red;font-size:16px\"><b>页面错误</b><br>'+msg+'<br>行: '+line+'<br><br>请按F12打开控制台查看更多</div>';};

(async function poll(){
  try{
    const d=await fetchData();
    updateUI(d);
    setInterval(async()=>{try{updateUI(await fetchData())}catch(e){console.error(e)}},10000);
  }catch(e){
    document.body.innerHTML='<div style=\"padding:40px;color:red;font-size:16px\"><b>JS错误</b><br>'+e.message+'<br><br>请按F12打开控制台查看更多</div>';
  }
})();
</script>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    fetcher: DataFetcher = None

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._html()
        elif self.path == "/api/data":
            self._api()
        else:
            self.send_response(404); self.end_headers()

    def _html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

    def _api(self):
        data = self.fetcher.get() if self.fetcher else {}
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass


def bg_refresh(f: DataFetcher):
    while True:
        try:
            f.refresh()
        except Exception as e:
            logger.error(f"刷新失败: {e}")
        time.sleep(10)


def load_config(path: str = "config.json") -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"api_key": os.environ.get("BINANCE_API_KEY", ""),
            "secret": os.environ.get("BINANCE_SECRET", ""),
            "proxy": os.environ.get("BINANCE_PROXY", os.environ.get("HTTPS_PROXY", "")),
            "usd_cny_rate": 7.25}


def main():
    cfg = load_config()
    ak, sk, proxy = cfg.get("api_key", ""), cfg.get("secret", ""), cfg.get("proxy", "")

    # 先启动HTTP服务器，让页面立即可访问
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n{'='*50}")
    print(f"  仪表盘: http://localhost:{PORT}")
    print(f"  Ctrl+C 停止")
    print(f"{'='*50}\n")

    # 后台初始化数据（通过代理较慢）
    def init_fetcher():
        if not ak or not sk:
            logger.warning("未配置API密钥，显示离线模式")
            return
        f = DataFetcher(ak, sk, proxy, cfg)
        f.refresh()
        Handler.fetcher = f
        # 启动定期刷新
        while True:
            try:
                f.refresh()
            except Exception as e:
                logger.error(f"刷新失败: {e}")
            time.sleep(10)

    threading.Thread(target=init_fetcher, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.shutdown()


if __name__ == "__main__":
    try:
        import ccxt
    except ImportError:
        print("pip install ccxt")
        sys.exit(1)
    main()
