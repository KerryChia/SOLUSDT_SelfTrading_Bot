"""
SOLUSDT 激进短线交易机器人 v5
只交易杠杆账户，仓位按杠杆余额计算，不做跨账户划转
"""

import os, sys, json, time, logging, traceback
from datetime import datetime, timedelta
from typing import Optional, Tuple

import ccxt
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("SOLUSDT")

SYMBOL = "SOL/USDT"
TIMEFRAME = "5m"

EMA_FAST, EMA_MID, EMA_SLOW = 5, 13, 30
RSI_PERIOD = 7
MACD_FAST, MACD_SLOW, MACD_SIG = 8, 17, 9
BB_PERIOD, BB_STD = 14, 2.0
ATR_PERIOD, VOL_MA = 7, 10

STOP_LOSS_PCT = 0.015
TAKE_PROFIT_PCT = 0.03
TRAILING_ACTIVATE = 0.02
TRAILING_DIST = 0.012
TIME_STOP_MIN = 120
ATR_STOP_MULT = 1.2
MAX_DAILY_LOSS_PCT = 0.15
MAX_DAILY_TRADES = 999  # 不限次数
MAX_CONSEC_LOSS = 5
PAUSE_MINUTES = 30

META_KEYS = {"info", "free", "used", "total", "timestamp", "datetime",
             "debt", "borrowed", "interest", "net", "currency", "free_margin",
             "used_margin", "equity", "unrealized_pnl", "margin_ratio", "position"}


def get_cfg(equity: float) -> dict:
    if equity < 50:
        return {"leverage": 3, "cap_use": 0.78, "label": "激进起步"}
    elif equity < 100:
        return {"leverage": 3, "cap_use": 0.75, "label": "滚雪球中"}
    elif equity < 300:
        return {"leverage": 3, "cap_use": 0.65, "label": "稳步增长"}
    elif equity < 500:
        return {"leverage": 2, "cap_use": 0.55, "label": "降杠防回撤"}
    else:
        return {"leverage": 2, "cap_use": 0.40, "label": "保守复利"}


# ============================================================
# 技术指标
# ============================================================

def ema(data: np.ndarray, period: int) -> np.ndarray:
    result = np.full_like(data, np.nan, dtype=np.float64)
    if len(data) < period:
        return result
    m = 2.0 / (period + 1)
    result[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = (data[i] - result[i - 1]) * m + result[i - 1]
    return result


def rsi(close: np.ndarray, period: int = 7) -> np.ndarray:
    result = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period + 1:
        return result
    delta = np.diff(close)
    gain, loss = np.where(delta > 0, delta, 0.0), np.where(delta < 0, -delta, 0.0)
    ag, al = np.mean(gain[:period]), np.mean(loss[:period])
    result[period] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    for i in range(period + 1, len(close)):
        ag = (ag * (period - 1) + gain[i - 1]) / period
        al = (al * (period - 1) + loss[i - 1]) / period
        result[i] = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    return result


def macd(close: np.ndarray, fast: int = 8, slow: int = 17, signal: int = 9):
    ef, es = ema(close, fast), ema(close, slow)
    ml = ef - es
    sl = ema(ml, signal)
    return ml, sl, ml - sl


def bb(close: np.ndarray, period: int = 14, std: float = 2.0):
    mid = sma(close, period)
    up = np.full_like(close, np.nan, dtype=np.float64)
    lo = np.full_like(close, np.nan, dtype=np.float64)
    for i in range(period - 1, len(close)):
        s = np.std(close[i - period + 1:i + 1])
        up[i] = mid[i] + std * s
        lo[i] = mid[i] - std * s
    return mid, up, lo


def sma(data: np.ndarray, period: int) -> np.ndarray:
    r = np.full_like(data, np.nan, dtype=np.float64)
    if len(data) < period:
        return r
    for i in range(period - 1, len(data)):
        r[i] = np.mean(data[i - period + 1:i + 1])
    return r


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 7) -> np.ndarray:
    r = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period + 1:
        return r
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    r[period] = np.mean(tr[:period])
    for i in range(period + 1, len(close)):
        r[i] = (r[i - 1] * (period - 1) + tr[i - 1]) / period
    return r


# ============================================================
# API — 只交易杠杆账户
# ============================================================

class BinanceAPI:

    def __init__(self, api_key: str, secret: str, proxy: str = ""):
        base = {
            "apiKey": api_key, "secret": secret,
            "enableRateLimit": True, "timeout": 60000,
        }
        if proxy:
            base["proxies"] = {"http": proxy, "https": proxy}
            logger.info(f"使用代理: {proxy}")

        self.spot = ccxt.binance({**base, "options": {"defaultType": "spot"}})
        self.margin = ccxt.binance({**base, "options": {"defaultType": "margin"}})
        self.futures = ccxt.binance({**base, "options": {"defaultType": "future"}})

        # 带重试的加载（代理可能不稳）
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

        self.market = self.spot.market(SYMBOL)
        logger.info("API就绪 | 杠杆交易模式")

    # ---- 余额读取 ----

    def _is_asset(self, key: str, info) -> bool:
        if key in META_KEYS:
            return False
        if not isinstance(info, dict):
            return False
        return "free" in info or "total" in info

    def get_margin_usdt(self) -> float:
        """杠杆账户可用USDT（优先free，fallback到total）"""
        try:
            b = self.margin.fetch_balance()
            info = b.get("USDT")
            if isinstance(info, dict):
                free = float(info.get("free", 0) or 0)
                total = float(info.get("total", 0) or 0)
                used = float(info.get("used", 0) or 0)
                logger.info(f"[DEBUG] 杠杆USDT: free={free:.4f} total={total:.4f} used={used:.4f}")
                result = free if free > 0 else total
                if result > 0:
                    return result
        except Exception:
            pass
        return self.get_margin_total_usdt()

    def get_margin_total_usdt(self) -> float:
        """杠杆账户USDT总额(含已占用)"""
        try:
            b = self.margin.fetch_balance()
            info = b.get("USDT")
            if isinstance(info, dict):
                return float(info.get("total", 0) or 0)
        except Exception:
            pass
        return 0.0

    def get_margin_sol(self) -> float:
        try:
            b = self.margin.fetch_balance()
            info = b.get("SOL")
            if isinstance(info, dict):
                return float(info.get("total", 0) or 0)
        except Exception:
            pass
        return 0.0

    def get_margin_sol_borrowed(self) -> float:
        try:
            b = self.margin.fetch_balance()
            info = b.get("SOL")
            if isinstance(info, dict):
                return float(info.get("debt", info.get("borrowed", 0)) or 0)
        except Exception:
            pass
        return 0.0

    def get_margin_equity(self) -> float:
        """杠杆账户净资产(USDT) = 所有资产的净值折合USDT"""
        try:
            b = self.margin.fetch_balance()
            total = 0.0
            for asset, info in b.items():
                if not self._is_asset(asset, info):
                    continue
                asset_total = float(info.get("total", 0) or 0)
                borrowed = float(info.get("debt", info.get("borrowed", 0)) or 0)
                net = asset_total - borrowed
                if abs(net) < 0.0001:
                    continue
                if asset == "USDT":
                    total += net
                else:
                    try:
                        t = self.spot.fetch_ticker(f"{asset}/USDT")
                        total += net * float(t["last"])
                    except Exception:
                        pass
            return total
        except Exception:
            return 0.0

    def get_margin_level(self) -> float:
        """保证金率 = 总资产/总负债 × 100%。越高越安全，<120%危险"""
        try:
            b = self.margin.fetch_balance()
            total_asset = 0.0
            total_debt = 0.0
            for asset, info in b.items():
                if not self._is_asset(asset, info):
                    continue
                asset_total = float(info.get("total", 0) or 0)
                borrowed = float(info.get("debt", info.get("borrowed", 0)) or 0)
                if asset == "USDT":
                    total_asset += asset_total
                    total_debt += borrowed
                else:
                    try:
                        t = self.spot.fetch_ticker(f"{asset}/USDT")
                        price = float(t["last"])
                        total_asset += asset_total * price
                        total_debt += borrowed * price
                    except Exception:
                        pass
            if total_debt <= 0:
                return 999.0  # 无借款，极其安全
            return (total_asset / total_debt) * 100.0
        except Exception:
            return 0.0

    def get_all_snapshot(self) -> dict:
        """四账户余额快照（仅展示，不用于交易）"""
        snap = {"funding": 0.0, "spot": 0.0, "margin": 0.0, "futures": 0.0}

        # 杠杆
        try:
            snap["margin"] = self.get_margin_total_usdt()
        except Exception:
            pass

        # 现货
        try:
            b = self.spot.fetch_balance()
            snap["spot"] = float((b.get("USDT") or {}).get("total", 0) or 0)
        except Exception:
            pass

        # 资金
        try:
            resp = self.spot.sapiGetAssetGetFundingAsset()
            for item in resp:
                if item.get("asset") == "USDT":
                    snap["funding"] = float(item.get("free", 0) or 0)
        except Exception:
            pass

        # 合约
        try:
            b = self.futures.fetch_balance()
            info = b.get("USDT")
            if isinstance(info, dict):
                snap["futures"] = float(info.get("total", info.get("free", 0)) or 0)
        except Exception:
            pass

        return snap

    # ---- 交易 ----

    def fetch_klines(self, limit: int = 100) -> dict:
        ohlcv = self.spot.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=limit)
        return {
            "close": np.array([c[4] for c in ohlcv], dtype=np.float64),
            "open": np.array([c[1] for c in ohlcv], dtype=np.float64),
            "high": np.array([c[2] for c in ohlcv], dtype=np.float64),
            "low": np.array([c[3] for c in ohlcv], dtype=np.float64),
            "volume": np.array([c[5] for c in ohlcv], dtype=np.float64),
            "timestamp": [c[0] for c in ohlcv],
        }

    def fetch_ticker(self):
        return self.spot.fetch_ticker(SYMBOL)

    @staticmethod
    def extract_fill_price(order: dict, fallback: float) -> float:
        """从各种格式的订单响应中提取成交均价"""
        # sapiPostMarginOrder 返回的字段
        cum_qty = float(order.get("cummulativeQuoteQty", 0) or 0)
        exe_qty = float(order.get("executedQty", 0) or 0)
        if exe_qty > 0 and cum_qty > 0:
            return cum_qty / exe_qty
        # fills 数组
        fills = order.get("fills", [])
        if fills and isinstance(fills, list) and len(fills) > 0:
            prices = [float(f.get("price", 0) or 0) for f in fills]
            if prices and sum(prices) > 0:
                return sum(prices) / len(prices)
        # ccxt 标准字段
        for key in ["average", "price"]:
            v = order.get(key)
            if v is not None and float(v) > 0:
                return float(v)
        return fallback

    def round_amount(self, amount: float) -> float:
        step = self.market["precision"]["amount"]
        # ccxt 的 precision 是步长(如0.001)，不是小数位数
        decimals = max(0, int(round(-np.log10(step)))) if step < 1 else 0
        return max(0, round(amount, decimals))

    def _margin_order(self, side: str, amount: float) -> dict:
        """原生margin下单，尝试多种sideEffectType"""
        qty = self.round_amount(amount)
        base = {"symbol": "SOLUSDT", "side": side, "type": "MARKET", "quantity": str(qty)}
        # 买卖分别尝试不同的 sideEffectType
        if side == "BUY":
            effect_types = ["NO_SIDE_EFFECT", "MARGIN_BUY"]
        else:
            effect_types = ["AUTO_REPAY", "AUTO_BORROW_REPAY", "NO_SIDE_EFFECT"]

        last_err = None
        for effect in effect_types:
            params = {**base, "sideEffectType": effect}
            for method_name in ["sapiPostMarginOrder", "sapi_post_margin_order"]:
                fn = getattr(self.margin, method_name, None)
                if not fn:
                    continue
                try:
                    o = fn(params)
                    logger.info(f"下单成功({method_name} sideEffect={effect}): {qty} SOL")
                    return o
                except Exception as e:
                    last_err = e
                    err_str = str(e)[:120]
                    logger.warning(f"  {effect}失败: {err_str}")
                    if "insufficient" not in str(e).lower():
                        break

        # fallback1: 尝试用账户实际余额（可能因手续费略有差异）
        if side == "SELL":
            actual_sol = self.get_margin_sol()
            if 0 < actual_sol < qty:
                actual_qty = self.round_amount(actual_sol)
                logger.warning(f"持仓量{qty}与实际余额{actual_sol}不符，尝试用实际余额{actual_qty}")
                return self._margin_order(side, actual_qty)
        elif side == "BUY":
            actual_usdt = self.get_margin_usdt()
            if actual_usdt > 0:
                ticker = self.fetch_ticker()
                alt_qty = self.round_amount(actual_usdt * 0.99 / ticker["last"])
                if 0 < alt_qty < qty:
                    logger.warning(f"尝试缩减买入量至{alt_qty}")
                    return self._margin_order(side, alt_qty)

        # fallback2: ccxt create_order
        logger.warning(f"所有effect均失败，fallback到ccxt... last_err: {str(last_err)[:200] if last_err else 'none'}")
        o = self.margin.create_order(SYMBOL, "market", side.lower(), qty)
        fill = o.get("average", o.get("price"))
        logger.info(f"ccxt下单成交: {qty} SOL @ {fill}")
        return o

    def margin_buy(self, amount: float) -> dict:
        logger.info(f">>> 杠杆买入 {amount} SOL")
        return self._margin_order("BUY", amount)

    def margin_sell(self, amount: float) -> dict:
        logger.info(f">>> 杠杆卖出 {amount} SOL")
        return self._margin_order("SELL", amount)


# ============================================================
# 策略引擎
# ============================================================

class StrategyEngine:

    def compute(self, klines: dict) -> dict:
        c, h, l, v = klines["close"], klines["high"], klines["low"], klines["volume"]
        ml, sl, hist = macd(c, MACD_FAST, MACD_SLOW, MACD_SIG)
        bm, bu, bl = bb(c, BB_PERIOD, BB_STD)
        return {
            "ema_f": ema(c, EMA_FAST), "ema_m": ema(c, EMA_MID),
            "ema_s": ema(c, EMA_SLOW), "rsi": rsi(c, RSI_PERIOD),
            "macd_l": ml, "sig_l": sl, "hist": hist,
            "bb_m": bm, "bb_u": bu, "bb_l": bl,
            "atr": atr(h, l, c, ATR_PERIOD),
            "vol_ma": sma(v, VOL_MA),
        }

    @staticmethod
    def last(arr: np.ndarray, off: int = 0) -> float:
        v = arr[~np.isnan(arr)]
        return v[-1 - off] if len(v) > off else np.nan

    @staticmethod
    def prev(arr: np.ndarray, off: int = 1) -> float:
        return StrategyEngine.last(arr, off)

    def check_long(self, ind: dict, klines: dict) -> Tuple[bool, str]:
        c, vol = klines["close"], klines["volume"]
        e5, e13 = self.last(ind["ema_f"]), self.last(ind["ema_m"])
        r, rp = self.last(ind["rsi"]), self.prev(ind["rsi"])
        hn, hp = self.last(ind["hist"]), self.prev(ind["hist"])
        ml, sl = self.last(ind["macd_l"]), self.last(ind["sig_l"])
        mlp, slp = self.prev(ind["macd_l"]), self.prev(ind["sig_l"])
        bbl = self.last(ind["bb_l"])
        vn, vm = vol[-1], self.last(ind["vol_ma"])
        trend = e5 > e13
        rsi_ok = (rp < 35 and r > rp) or (40 <= r <= 55)
        macd_ok = (hp < 0 < hn) or (mlp < slp and ml > sl)
        bb_ok = c[-1] <= bbl * 1.01
        vol_ok = vm > 0 and vn >= vm * 1.1
        s = sum([trend, rsi_ok, macd_ok, bb_ok, vol_ok])
        if trend and (rsi_ok or macd_ok):
            return True, f"做多 s={s}/5"
        return False, f"做多未达标 s={s}/5"

    def check_short(self, ind: dict, klines: dict) -> Tuple[bool, str]:
        c, vol = klines["close"], klines["volume"]
        e5, e13 = self.last(ind["ema_f"]), self.last(ind["ema_m"])
        r, rp = self.last(ind["rsi"]), self.prev(ind["rsi"])
        hn, hp = self.last(ind["hist"]), self.prev(ind["hist"])
        ml, sl = self.last(ind["macd_l"]), self.last(ind["sig_l"])
        mlp, slp = self.prev(ind["macd_l"]), self.prev(ind["sig_l"])
        bbu = self.last(ind["bb_u"])
        vn, vm = vol[-1], self.last(ind["vol_ma"])
        trend = e5 < e13
        rsi_ok = (rp > 65 and r < rp) or (45 <= r <= 60)
        macd_ok = (hp > 0 > hn) or (mlp > slp and ml < sl)
        bb_ok = c[-1] >= bbu * 0.99
        vol_ok = vm > 0 and vn >= vm * 1.1
        s = sum([trend, rsi_ok, macd_ok, bb_ok, vol_ok])
        if trend and (rsi_ok or macd_ok):
            return True, f"做空 s={s}/5"
        return False, f"做空未达标 s={s}/5"

    def check_long_exit(self, ind: dict, entry: float, cp: float,
                        highest: float, entry_time: datetime) -> Tuple[bool, str]:
        hold = (datetime.now() - entry_time).total_seconds() / 60
        if cp <= entry * (1 - STOP_LOSS_PCT):
            return True, "止损-1.5%"
        a = self.last(ind["atr"])
        if not np.isnan(a) and cp <= entry - ATR_STOP_MULT * a:
            return True, "ATR止损"
        if hold >= TIME_STOP_MIN:
            return True, f"时间止损({hold:.0f}min)"
        if (highest - entry) / entry >= TRAILING_ACTIVATE:
            if cp <= highest * (1 - TRAILING_DIST):
                return True, "移动止损"
        if (cp - entry) / entry >= TAKE_PROFIT_PCT:
            return True, "止盈+3%"
        if self.last(ind["ema_f"]) < self.last(ind["ema_m"]):
            return True, "EMA死叉"
        if self.last(ind["rsi"]) > 82:
            return True, "RSI超买"
        return False, ""

    def check_short_exit(self, ind: dict, entry: float, cp: float,
                         lowest: float, entry_time: datetime) -> Tuple[bool, str]:
        hold = (datetime.now() - entry_time).total_seconds() / 60
        if cp >= entry * (1 + STOP_LOSS_PCT):
            return True, "止损+1.5%"
        a = self.last(ind["atr"])
        if not np.isnan(a) and cp >= entry + ATR_STOP_MULT * a:
            return True, "ATR止损"
        if hold >= TIME_STOP_MIN:
            return True, f"时间止损({hold:.0f}min)"
        if (entry - lowest) / entry >= TRAILING_ACTIVATE:
            if cp >= lowest * (1 + TRAILING_DIST):
                return True, "移动止损"
        if (entry - cp) / entry >= TAKE_PROFIT_PCT:
            return True, "止盈+3%"
        if self.last(ind["ema_f"]) > self.last(ind["ema_m"]):
            return True, "EMA金叉"
        if self.last(ind["rsi"]) < 18:
            return True, "RSI超卖"
        return False, ""


# ============================================================
class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.consec_losses = 0
        self.date = datetime.now().date()
        self.pause_until: Optional[datetime] = None
        self.start_equity: Optional[float] = None

    def _reset(self):
        t = datetime.now().date()
        if t != self.date:
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.date = t

    def can_trade(self, equity: float) -> Tuple[bool, str]:
        self._reset()
        if self.start_equity is None:
            self.start_equity = equity
        if self.pause_until and datetime.now() < self.pause_until:
            r = (self.pause_until - datetime.now()).seconds // 60
            return False, f"暂停剩{r}min"
        if self.daily_pnl < -self.start_equity * MAX_DAILY_LOSS_PCT:
            return False, "日亏超限"
        if self.daily_trades >= MAX_DAILY_TRADES:
            return False, "日交易满"
        return True, ""

    def record(self, pnl: float):
        self.daily_trades += 1
        self.daily_pnl += pnl
        if pnl < 0:
            self.consec_losses += 1
            if self.consec_losses >= MAX_CONSEC_LOSS:
                self.pause_until = datetime.now() + timedelta(minutes=PAUSE_MINUTES)
                logger.warning(f"连亏{MAX_CONSEC_LOSS}笔,暂停{PAUSE_MINUTES}min")
        else:
            self.consec_losses = 0
        logger.info(f"当日: {self.daily_trades}笔 PnL={self.daily_pnl:.2f}")

    @staticmethod
    def near_hour() -> bool:
        m = datetime.now().minute
        return m < 2 or m >= 58


# ============================================================
class TradingBot:
    def __init__(self, api_key: str, secret: str, proxy: str = ""):
        self.api = BinanceAPI(api_key, secret, proxy)
        self.strategy = StrategyEngine()
        self.risk = RiskManager()
        self.position: Optional[dict] = None
        self.highest = 0.0
        self.lowest = 1e9
        self.running = True
        self._pos_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "position.json")
        self._trade_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.json")

    def _write_position_file(self):
        """写入持仓文件供monitor读取"""
        try:
            if self.position:
                with open(self._pos_file, "w") as f:
                    json.dump({
                        "side": self.position["side"],
                        "amount": self.position["amount"],
                        "entry_price": self.position["entry_price"],
                        "entry_time": self.position["entry_time"].isoformat(),
                    }, f)
            else:
                if os.path.exists(self._pos_file):
                    os.remove(self._pos_file)
        except Exception:
            pass

    def _log_trade(self, action: str, side: str, amount: float, price: float, pnl: float = 0):
        """追加交易记录到trades.json"""
        try:
            trades = []
            if os.path.exists(self._trade_file):
                with open(self._trade_file) as f:
                    trades = json.load(f)
            trades.append({
                "time": datetime.now().strftime("%m/%d %H:%M:%S"),
                "action": action,  # "开仓" or "平仓"
                "side": side,
                "amount": round(amount, 4),
                "price": round(price, 4),
                "pnl": round(pnl, 4) if pnl else None,
            })
            # 只保留最近50条
            trades = trades[-50:]
            with open(self._trade_file, "w") as f:
                json.dump(trades, f, ensure_ascii=False)
        except Exception:
            pass

    def run(self, close_all: bool = False):
        logger.info("=" * 60)
        logger.info("SOLUSDT v6 | 杠杆交易 | 启动不操作")
        logger.info("=" * 60)

        self._startup_report()
        self._check_margin_funds()
        self._sync_position()

        # --close-all: 平掉所有现有持仓后退出
        if close_all and self.position:
            logger.info("--- 平仓模式: 关闭所有持仓 ---")
            ticker = self.api.fetch_ticker()
            self._close(ticker["last"], "手动平仓")
            logger.info("持仓已清空，退出。")
            return
        elif close_all:
            logger.info("无持仓，无需平仓。")
            return

        if self.position:
            logger.warning(f"检测到现有持仓({self.position['side']} {self.position['amount']}SOL)，将纳入策略管理")
            logger.warning("注意：入场价为当前市价(近似)，P&L可能不准确")

        last_ts = 0
        while self.running:
            try:
                klines = self.api.fetch_klines(limit=100)
                cur_ts = klines["timestamp"][-1]
                if cur_ts == last_ts:
                    time.sleep(8)
                    continue
                last_ts = cur_ts
                self._tick(klines)
            except KeyboardInterrupt:
                self.running = False
            except ccxt.NetworkError as e:
                logger.error(f"网络: {e}")
                time.sleep(20)
            except ccxt.RateLimitExceeded as e:
                logger.error(f"限频: {e}")
                time.sleep(45)
            except Exception as e:
                logger.error(f"{e}\n{traceback.format_exc()}")
                time.sleep(20)
        logger.info("已停止")

    def _startup_report(self):
        s = self.api.get_all_snapshot()
        total = sum(s.values())
        logger.info("--- 各账户 USDT 余额 ---")
        logger.info(f"  资金账户: {s['funding']:.2f}")
        logger.info(f"  现货账户: {s['spot']:.2f}")
        logger.info(f"  杠杆账户: {s['margin']:.2f}  ← 交易用")
        logger.info(f"  合约账户: {s['futures']:.2f}")
        logger.info(f"  合计:     {total:.2f}")
        logger.info(f"  交易可用(杠杆): {s['margin']:.2f}")

    def _check_margin_funds(self):
        """检查杠杆账户是否有足够资金"""
        margin_u = self.api.get_margin_usdt()
        if margin_u < 5:
            logger.warning("=" * 50)
            logger.warning(f"杠杆账户可用余额不足: ${margin_u:.2f}")
            logger.warning("请手动将资金划转到杠杆账户：")
            logger.warning("  币安APP → 钱包 → 资金账户 → 划转 → 杠杆账户")
            logger.warning("  币安APP → 钱包 → 现货账户 → 划转 → 杠杆账户")
            logger.warning("  币安APP → 合约 → 划转 → 杠杆账户")
            logger.warning("=" * 50)

    def _startup_test_trade(self):
        """用杠杆账户余额中的一小部分做测试交易"""
        # 先清理可能残留的持仓（用精确余额，不取整）
        existing_sol = self.api.get_margin_sol()
        if existing_sol > 0.001:
            logger.info(f"清理残留持仓: {existing_sol} SOL")
            try:
                self.api._margin_order("SELL", existing_sol)
                time.sleep(2)
            except Exception as e:
                logger.warning(f"清理失败: {e}")

        free = self.api.get_margin_usdt()
        logger.info(f"测试交易: 杠杆可用={free:.4f} USDT")
        if free < 5:
            logger.warning(f"杠杆可用USDT不足(${free:.2f})，跳过测试交易")
            return

        ticker = self.api.fetch_ticker()
        cp = ticker["last"]

        # 杠杆最低交易额$10，测试用$11，不超过余额40%
        test_usdt = min(11.0, free * 0.4)
        test_amount = test_usdt / cp
        logger.info(f"测试交易: test_usdt={test_usdt:.4f} cp={cp:.4f} raw_amount={test_amount:.6f}")
        test_amount = self.api.round_amount(test_amount)
        logger.info(f"测试交易: rounded_amount={test_amount:.6f} (step={self.api.market['precision']['amount']})")

        if test_amount < 0.01:
            logger.warning(f"测试交易量过小 ({test_amount:.6f} < 0.01)")
            return

        logger.info("=" * 40)
        logger.info(f"*** 测试交易: 买入 {test_amount} SOL (约${test_usdt:.2f}) ***")

        try:
            bo = self.api.margin_buy(test_amount)
            # 获取实际成交数量（sapiPostMarginOrder 返回的字段）
            actual_qty = float(bo.get("executedQty", bo.get("quantity", test_amount)))
            actual_qty = self.api.round_amount(actual_qty)
            logger.info(f"买入成功! 实际成交 {actual_qty} SOL，立即卖出...")
            time.sleep(2)
            so = self.api.margin_sell(actual_qty)
            bf = self.api.extract_fill_price(bo, cp)
            sf = self.api.extract_fill_price(so, cp)
            pnl = (sf - bf) * test_amount
            logger.info(f"测试完成 PnL={pnl:.4f}U (买{bf:.4f} 卖{sf:.4f})")
            logger.info("*** 交易链路正常，进入策略循环 ***")
        except Exception as e:
            logger.error(f"测试交易失败: {e}")

        logger.info("=" * 40)

    def _sync_position(self):
        sol = self.api.get_margin_sol()
        bor = self.api.get_margin_sol_borrowed()
        if sol > 0.001:
            cp = self.api.fetch_ticker()["last"]
            self.position = {"side": "long", "amount": sol,
                             "entry_price": cp, "entry_time": datetime.now()}
            self.highest = cp
            self._write_position_file()
            logger.info(f"现有持仓: 多头 {sol} SOL")
        elif bor > 0.001:
            cp = self.api.fetch_ticker()["last"]
            self.position = {"side": "short", "amount": bor,
                             "entry_price": cp, "entry_time": datetime.now()}
            self.lowest = cp
            self._write_position_file()
            logger.info(f"现有持仓: 空头 {bor} SOL")
        else:
            self.position = None
            self._write_position_file()

    def _tick(self, klines: dict):
        close = klines["close"]
        cp = close[-1]
        ind = self.strategy.compute(klines)

        # 仓位计算仅基于杠杆账户余额
        margin_equity = self.api.get_margin_equity()
        margin_level = self.api.get_margin_level()
        cfg = get_cfg(margin_equity)

        logger.info(
            f"[{datetime.now().strftime('%H:%M:%S')}] SOL={cp:.4f} | "
            f"净值={margin_equity:.2f} | 保证金率={margin_level:.0f}% | "
            f"RSI={self.strategy.last(ind['rsi']):.0f}"
        )

        if self.position:
            self._update_extremes(cp)
            self._check_exit(ind, cp)
            return

        if len(close) >= 2:
            gap = abs(cp - close[-2]) / close[-2]
            if gap > 0.02:
                logger.warning(f"跳空{gap*100:.1f}%")
                return

        ok, reason = self.risk.can_trade(margin_equity)
        if not ok:
            logger.info(f"风控: {reason}")
            return

        lo, lm = self.strategy.check_long(ind, klines)
        if lo:
            logger.info(f"信号: {lm}")
            self._open("long", cp, margin_equity, cfg)
            return

        so, sm = self.strategy.check_short(ind, klines)
        if so:
            logger.info(f"信号: {sm}")
            self._open("short", cp, margin_equity, cfg)

    def _update_extremes(self, cp: float):
        if not self.position:
            return
        if self.position["side"] == "long" and cp > self.highest:
            self.highest = cp
        elif self.position["side"] == "short" and cp < self.lowest:
            self.lowest = cp

    def _check_exit(self, ind: dict, cp: float):
        pos = self.position
        if pos["side"] == "long":
            ex, reason = self.strategy.check_long_exit(
                ind, pos["entry_price"], cp, self.highest, pos["entry_time"])
        else:
            ex, reason = self.strategy.check_short_exit(
                ind, pos["entry_price"], cp, self.lowest, pos["entry_time"])

        # 保证金率底线保护（动态：仓位越大底线越高）
        if not ex:
            ml = self.api.get_margin_level()
            eq = self.api.get_margin_equity()
            pos_val = abs(pos["amount"]) * cp
            usage = pos_val / max(eq, 1)
            if usage < 0.3:
                floor = 150
            elif usage < 0.7:
                floor = 138
            else:
                floor = 130
            if ml < floor:
                ex, reason = True, f"保证金率{ml:.0f}%<底线{floor}%(仓位{usage*100:.0f}%)"

        if ex:
            logger.info(f"出场: {reason}")
            self._close(cp, reason)

    def _open(self, side: str, cp: float, _margin_eq: float, cfg: dict):
        # 动态保证金率阈值：仓位越大，要求越高
        margin_level = self.api.get_margin_level()
        free = self.api.get_margin_usdt()
        margin_equity = self.api.get_margin_equity()

        # 仓位使用率 = 计划占用资金 / 净资产
        planned_usage = (free * cfg["cap_use"]) / max(margin_equity, 1)
        if planned_usage < 0.3:
            MARGIN_MIN = 150
        elif planned_usage < 0.7:
            MARGIN_MIN = 138
        else:
            MARGIN_MIN = 130

        if margin_level < MARGIN_MIN:
            logger.warning(f"保证金率{margin_level:.0f}% < 底线{MARGIN_MIN}%(仓位使用{planned_usage*100:.0f}%)，不开新仓")
            return

        trade_cap = free * cfg["cap_use"]

        # 保证金率低于200%时按比例缩减仓位
        if margin_level < 200:
            trade_cap *= margin_level / 200
            logger.info(f"保证金率偏低({margin_level:.0f}%)，仓位缩减至{trade_cap:.1f}U")

        if trade_cap < 3:
            logger.warning(f"杠杆可用资金不足 (free={free:.2f}, need>{trade_cap:.2f})")
            return

        amount = trade_cap / cp
        amount = self.api.round_amount(amount)
        if amount <= 0:
            return
        if amount * cp < 10:
            logger.warning(f"交易金额${amount*cp:.2f}低于最低$10，跳过")
            return

        logger.info(f"开{side}: {amount}SOL @~{cp:.4f} | 占用{trade_cap:.1f}U/{free:.1f}U可用 | 保证金率{margin_level:.0f}%")

        try:
            o = self.api.margin_buy(amount) if side == "long" else self.api.margin_sell(amount)
            fill = self.api.extract_fill_price(o, cp)
            self.position = {"side": side, "amount": amount,
                             "entry_price": fill, "entry_time": datetime.now()}
            self.highest = fill
            self.lowest = fill
            self._write_position_file()
            self._log_trade("开仓", side, amount, fill)
            logger.info(f"开仓成功: {side} {amount}SOL @{fill:.4f}")
        except Exception as e:
            logger.error(f"开仓失败: {e}")

    def _close(self, cp: float, reason: str):
        pos = self.position
        if not pos:
            return
        amount = self.api.round_amount(pos["amount"])
        entry = pos["entry_price"]
        side = pos["side"]
        try:
            o = self.api.margin_sell(amount) if side == "long" else self.api.margin_buy(amount)
            fill = self.api.extract_fill_price(o, cp)
            pnl = (fill - entry) * amount if side == "long" else (entry - fill) * amount

            # 平空仓后显式还币，清除SOL借款
            if side == "short":
                try:
                    sol_debt = self.api.get_margin_sol_borrowed()
                    if sol_debt > 0.001:
                        repay_amt = min(sol_debt, self.api.get_margin_sol())
                        if repay_amt > 0.001:
                            self.api.margin.sapiPostMarginRepay({
                                "asset": "SOL",
                                "amount": str(self.api.round_amount(repay_amt)),
                            })
                            logger.info(f"已归还 {repay_amt:.4f} SOL 借款")
                except Exception as e:
                    logger.warning(f"还币失败: {e}")

            logger.info(f"平仓: {side} {amount}SOL PnL={pnl:.2f}U ({pnl/(entry*amount)*100:.2f}%) {reason}")
            self._log_trade("平仓", side, amount, fill, pnl)
            self.risk.record(pnl)
            self.position = None
            self.highest = 0.0
            self.lowest = 1e9
            self._write_position_file()
        except Exception as e:
            logger.error(f"平仓失败: {e}")


def load_config(path: str = "config.json") -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {"api_key": os.environ.get("BINANCE_API_KEY", ""),
            "secret": os.environ.get("BINANCE_SECRET", ""),
            "proxy": os.environ.get("BINANCE_PROXY", os.environ.get("HTTPS_PROXY", ""))}


def main():
    cfg = load_config()
    ak = cfg.get("api_key", "")
    sk = cfg.get("secret", "")
    proxy = cfg.get("proxy", "")
    if not ak or not sk:
        print("请配置API密钥")
        return

    close_all = "--close-all" in sys.argv
    if close_all:
        print("模式: 平掉所有持仓后退出")

    TradingBot(ak, sk, proxy).run(close_all=close_all)


if __name__ == "__main__":
    try:
        import ccxt, numpy
    except ImportError:
        print("pip install ccxt numpy")
        sys.exit(1)
    main()
