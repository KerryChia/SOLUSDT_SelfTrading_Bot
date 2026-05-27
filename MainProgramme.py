"""
SOLUSDT aggressive short-term trading bot v8.3.
Trades only the Binance margin account. Position sizing is based on margin
account equity and does not transfer funds across accounts.
"""

import os, sys, json, time, logging, traceback
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlparse
from urllib import request, error

import ccxt
import numpy as np

from calculator import SlippageTracker, net_pnl_after_fee
from config import RuntimeConfig, build_runtime_config
from storage import Storage

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
KLINE_POLL_SECONDS = 50

EMA_FAST, EMA_MID, EMA_SLOW = 5, 13, 30
RSI_PERIOD = 7
MACD_FAST, MACD_SLOW, MACD_SIG = 8, 17, 9
BB_PERIOD, BB_STD = 14, 2.0
ATR_PERIOD, VOL_MA = 7, 10

STOP_LOSS_PCT = 0.010
TAKE_PROFIT_PCT = 0.015
TRAILING_ACTIVATE = 0.012
TRAILING_DIST = 0.0045
BREAK_EVEN_ACTIVATE = 0.010
BREAK_EVEN_BUFFER = 0.0012
PROFIT_RETRACE_KEEP = 0.42
MIN_PROFIT_EXIT = 0.0035
TIME_STOP_MIN = None
ATR_STOP_MULT = None
MAX_DAILY_LOSS_PCT = 0.15
MAX_DAILY_TRADES = 999  # Unlimited daily trades.
MAX_CONSEC_LOSS = 5
PAUSE_MINUTES = 30
POST_TRADE_COOLDOWN_MIN = 0
LOSS_COOLDOWN_MIN = 0
MIN_EMA_SEP_PCT = 0.0000
MIN_ATR_PCT = 0.00045
MAX_LONG_RSI = 55
MIN_SHORT_RSI = 45
VOL_CONFIRM_MULT = 1.5
BB_LONG_TOUCH = 1.003
BB_SHORT_TOUCH = 0.997
MIN_BB_WIDTH_PCT = 0.006
FAKE_BREAKOUT_LOOKBACK = 3
FAKE_BREAKOUT_STRICT_MULT = 1.25
RSI_DIVERGENCE_LOOKBACK = 18
RSI_DIVERGENCE_MIN_GAP = 3.0
BASE_CAP_USE = 0.48
MAX_AI_CAP_USE = 0.70
MIN_AI_CAP_USE = 0.10
HARD_MAX_LOSS_PCT = 0.010
FORCE_EXIT_NEGATIVE_HOLD_MIN = 120
ATR_TAKE_PROFIT_MULT = 1.5
ATR_STOP_LOSS_MULT = 1.0
MIN_TAKE_PROFIT_PCT = 0.015
MIN_DYNAMIC_STOP_LOSS_PCT = 0.006
AI_REVIEW_AFTER_MIN = 30
AI_REVIEW_INTERVAL_MIN = 30
AI_MAX_RETRIES = 2
AI_RETRY_BASE_DELAY = 0.6
AI_TIMEOUT_WARN_SECONDS = 6.0
DUST_POSITION_SOL = 0.005
DUST_POSITION_USDT = 3.0
FEE_RATE = 0.001
MTF_15M = "15m"
MTF_1H = "1h"
MTF_EMA_FAST = 50
MTF_EMA_SLOW = 200

META_KEYS = {"info", "free", "used", "total", "timestamp", "datetime",
             "debt", "borrowed", "interest", "net", "currency", "free_margin",
             "used_margin", "equity", "unrealized_pnl", "margin_ratio", "position"}


def get_cfg(equity: float) -> dict:
    return {"leverage": 5, "cap_use": BASE_CAP_USE, "max_cap_use": MAX_AI_CAP_USE, "label": "v8.3-sqlite"}


class DeepSeekRiskAdvisor:
    def __init__(self, api_key: str = "", model: str = "deepseek-chat",
                 enabled: bool = True, timeout: int = 8,
                 runtime: RuntimeConfig = None, storage: Storage = None):
        self.api_key = (api_key or "").strip()
        self.model = model or "deepseek-chat"
        self.enabled = bool(enabled and self.api_key)
        self.timeout = int(timeout or 8)
        self.runtime = runtime or build_runtime_config({})
        self.features = self.runtime.features
        self.db = storage
        self._last_response_ms = 0
        self._cache = {}
        self._stats_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_stats.json")
        self._stats = self._load_stats()
        self.system_prompt = (
            "You are a strict crypto trading risk manager for SOLUSDT 5m margin scalping. "
            "Return compact JSON only. For entry you may approve, skip, reduce cap_use, or raise cap_use "
            "up to 0.70. For exit you may hold, close, or close and immediately reverse to the opposite side "
            "when evidence is strong. If holding you must set next_take_profit_pct and next_stop_loss_pct. "
            "If reversing, return action reverse_long or reverse_short plus cap_use 0.10-0.70. "
            "The bot enforces hard exits: never rely on AI hold beyond -1.0% loss or after 120 minutes negative. "
            "Reflect on recent trade results before approving. Prefer skip when signal quality is weak. "
            "Keep reasons short in Chinese."
        )

    def _load_stats(self) -> dict:
        try:
            if os.path.exists(self._stats_file):
                with open(self._stats_file, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        data.setdefault("recent", [])
                        data.setdefault("approve_wins", 0)
                        data.setdefault("approve_losses", 0)
                        data.setdefault("approve_pnl", 0.0)
                        data.setdefault("approve_consec_losses", 0)
                        return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"AI stats load failed: {e}")
        return {"recent": [], "approve_wins": 0, "approve_losses": 0,
                "approve_pnl": 0.0, "approve_consec_losses": 0}

    def _save_stats(self):
        try:
            with open(self._stats_file, "w", encoding="utf-8") as f:
                json.dump(self._stats, f, ensure_ascii=False)
        except OSError as e:
            logger.warning(f"AI stats save failed: {e}")

    def recent_results(self) -> list:
        return list(self._stats.get("recent", []))[-3:]

    def effective_max_cap_use(self, cfg: dict) -> float:
        max_cap = float(cfg.get("max_cap_use", MAX_AI_CAP_USE))
        if self.features.ai_quality_stats and int(self._stats.get("approve_consec_losses", 0) or 0) >= 5:
            max_cap = min(max_cap, BASE_CAP_USE)
        return max(MIN_AI_CAP_USE, min(MAX_AI_CAP_USE, max_cap))

    def record_trade_result(self, ai_entry: dict, pnl: float):
        if not self.features.ai_quality_stats:
            return
        if not isinstance(ai_entry, dict) or ai_entry.get("action") != "approve":
            return
        pnl = float(pnl or 0)
        won = pnl > 0
        if won:
            self._stats["approve_wins"] = int(self._stats.get("approve_wins", 0) or 0) + 1
            self._stats["approve_consec_losses"] = 0
        else:
            self._stats["approve_losses"] = int(self._stats.get("approve_losses", 0) or 0) + 1
            self._stats["approve_consec_losses"] = int(self._stats.get("approve_consec_losses", 0) or 0) + 1
        self._stats["approve_pnl"] = float(self._stats.get("approve_pnl", 0.0) or 0.0) + pnl
        recent = list(self._stats.get("recent", []))
        recent.append({
            "time": datetime.now().strftime("%m/%d %H:%M:%S"),
            "pnl": round(pnl, 4),
            "win": won,
            "reason": str(ai_entry.get("reason", ""))[:80],
        })
        self._stats["recent"] = recent[-20:]
        self._save_stats()
        if int(self._stats.get("approve_consec_losses", 0) or 0) >= 5:
            logger.warning("AI approve quality degraded: 5 consecutive losing approved trades; cap_use max limited to base")

    @staticmethod
    def _clean_float(value, digits: int = 4):
        try:
            if value is None or np.isnan(value):
                return None
            return round(float(value), digits)
        except Exception:
            return None

    def _signature(self, prefix: str, signal_side: str, cp: float, ind: dict, risk: "RiskManager", reason: str = "") -> str:
        rsi_now = self._clean_float(StrategyEngine.last(ind["rsi"]), 1)
        atr_now = self._clean_float(StrategyEngine.last(ind["atr"]) / cp * 100 if cp > 0 else None, 3)
        bucket = int(time.time() // 600)
        return f"{prefix}:{bucket}:{signal_side}:{round(cp, 2)}:{rsi_now}:{atr_now}:{risk.daily_trades}:{risk.consec_losses}:{reason[:30]}"

    def _payload(self, signal_side: str, signal_reason: str, cp: float, klines: dict,
                 ind: dict, equity: float, margin_level: float, cfg: dict,
                 risk: "RiskManager", context: dict = None) -> dict:
        candles = []
        for i in range(max(0, len(klines["close"]) - 12), len(klines["close"])):
            candles.append([
                int(klines["timestamp"][i] // 1000),
                round(float(klines["open"][i]), 3),
                round(float(klines["high"][i]), 3),
                round(float(klines["low"][i]), 3),
                round(float(klines["close"][i]), 3),
                round(float(klines["volume"][i]), 1),
            ])
        bbu = StrategyEngine.last(ind["bb_u"])
        bbl = StrategyEngine.last(ind["bb_l"])
        bbm = StrategyEngine.last(ind["bb_m"])
        atr_v = StrategyEngine.last(ind["atr"])
        vol_ma = StrategyEngine.last(ind["vol_ma"])
        payload = {
            "symbol": "SOLUSDT",
            "timeframe": "5m",
            "candidate": signal_side,
            "signal_reason": signal_reason,
            "price": round(float(cp), 4),
            "account": {
                "equity_usdt": round(float(equity), 3),
                "margin_level_pct": round(float(margin_level), 1),
                "leverage": cfg.get("leverage"),
                "base_cap_use": cfg.get("cap_use"),
                "max_cap_use": cfg.get("max_cap_use", MAX_AI_CAP_USE),
                "daily_trades": risk.daily_trades,
                "daily_pnl_usdt": round(float(risk.daily_pnl), 3),
                "consecutive_losses": risk.consec_losses,
            },
            "indicators": {
                "rsi": self._clean_float(StrategyEngine.last(ind["rsi"]), 1),
                "rsi_prev": self._clean_float(StrategyEngine.prev(ind["rsi"]), 1),
                "ema5": self._clean_float(StrategyEngine.last(ind["ema_f"]), 4),
                "ema13": self._clean_float(StrategyEngine.last(ind["ema_m"]), 4),
                "bb_upper": self._clean_float(bbu, 4),
                "bb_lower": self._clean_float(bbl, 4),
                "bb_width_pct": self._clean_float((bbu - bbl) / bbm * 100 if bbm and not np.isnan(bbm) else None, 3),
                "volume": self._clean_float(klines["volume"][-1], 1),
                "volume_ma": self._clean_float(vol_ma, 1),
                "atr_pct": self._clean_float(atr_v / cp * 100 if cp > 0 else None, 3),
                "macd_hist": self._clean_float(StrategyEngine.last(ind["hist"]), 5),
            },
            "recent_ai_results": self.recent_results(),
            "recent_12_candles": candles,
            "mtf": self._extract_mtf_payload(context),
        }
        if context:
            payload["context"] = context
        return payload

    @staticmethod
    def _extract_mtf_payload(context: dict = None) -> dict:
        if not isinstance(context, dict):
            return {"consensus": "unknown"}
        if "consensus" in context:
            return context
        mtf = context.get("mtf")
        if isinstance(mtf, dict):
            return mtf
        return {"consensus": "unknown"}

    def _request_json(self, user_payload: dict, max_tokens: int = 180) -> dict:
        body = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
            ],
        }
        req = request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        attempts = AI_MAX_RETRIES + 1 if self.features.ai_retries else 1
        last_err = None
        for attempt in range(attempts):
            started = time.time()
            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                elapsed = time.time() - started
                if elapsed >= AI_TIMEOUT_WARN_SECONDS:
                    logger.warning(f"DeepSeek slow response: {elapsed:.2f}s attempt={attempt + 1}")
                else:
                    logger.info(f"DeepSeek response: {elapsed:.2f}s attempt={attempt + 1}")
                self._last_response_ms = int(elapsed * 1000)
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
            except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as e:
                last_err = e
                if attempt + 1 >= attempts:
                    break
                delay = AI_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(f"DeepSeek request failed attempt={attempt + 1}: {e}; retry in {delay:.1f}s")
                time.sleep(delay)
        raise last_err or RuntimeError("DeepSeek request failed")

    def _save_ai_decision(self, call_type: str, side: str, price: float,
                          payload: dict, result: dict):
        if not self.db or not self.db.enabled:
            return
        try:
            self.db.save_ai_decision(
                call_type=call_type,
                side=side,
                price=price,
                action=str(result.get("action", "")),
                cap_use=result.get("cap_use"),
                response_ms=int(self._last_response_ms or 0),
                reason=str(result.get("reason", "")),
                payload=payload,
                result=result,
            )
        except Exception as e:
            logger.warning(f"SQLite AI decision write skipped: {e}")

    def _latest_cached(self, prefix: str, side: str):
        wanted = f"{prefix}:"
        marker = f":{side}:"
        for key, value in reversed(list(self._cache.items())):
            if str(key).startswith(wanted) and marker in str(key):
                return value
        return None

    def advise(self, signal_side: str, signal_reason: str, cp: float, klines: dict,
               ind: dict, equity: float, margin_level: float, cfg: dict,
               risk: "RiskManager", context: dict = None) -> Tuple[bool, dict]:
        if not self.enabled:
            return True, {"action": "approve", "cap_use": cfg.get("cap_use"), "reason": "DeepSeek disabled"}
        sig = self._signature("entry", signal_side, cp, ind, risk, signal_reason)
        if sig in self._cache:
            return self._cache[sig]
        user_payload = self._payload(signal_side, signal_reason, cp, klines, ind, equity, margin_level, cfg, risk, context)
        user_payload["task"] = "entry_decision"
        user_payload["return_schema"] = {"action": "approve|skip", "cap_use": "0.10-0.70", "reason": "short Chinese reason"}
        try:
            decision = self._request_json(user_payload, max_tokens=160)
            action = str(decision.get("action", "skip")).lower()
            cap = float(decision.get("cap_use", cfg.get("cap_use")) or cfg.get("cap_use"))
            cap = max(MIN_AI_CAP_USE, min(self.effective_max_cap_use(cfg), cap))
            allowed = action == "approve" and cap >= MIN_AI_CAP_USE
            result = (allowed, {
                "action": "approve" if allowed else "skip",
                "cap_use": cap,
                "reason": str(decision.get("reason", ""))[:120],
            })
            logger.info(f"DeepSeek entry risk: {result[1]['action']} cap={cap:.2f} reason={result[1]['reason']}")
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as e:
            cached = self._latest_cached("entry", signal_side) if self.features.ai_cache_fallback else None
            if cached:
                allowed, ai = cached
                result = (allowed, {**ai, "reason": f"AI失败，用缓存判断: {ai.get('reason', '')}"[:120], "source": "cache_fallback"})
                logger.warning(f"DeepSeek entry failed; using cached decision: {e}")
            else:
                result = (True, {"action": "approve", "cap_use": cfg.get("cap_use"), "reason": f"DeepSeek entry failed; local fallback approve: {e}"})
                logger.warning(f"DeepSeek entry risk failed; local fallback approve: {e}")
        self._cache[sig] = result
        self._save_ai_decision("entry", signal_side, cp, user_payload, result[1])
        if len(self._cache) > 64:
            self._cache.pop(next(iter(self._cache)))
        return result

    def advise_exit(self, pos: dict, exit_reason: str, cp: float, klines: dict, ind: dict,
                    equity: float, margin_level: float, risk: "RiskManager",
                    context: dict = None) -> dict:
        if not self.enabled:
            return {"action": "close", "reason": "DeepSeek disabled"}
        side = str(pos.get("side", ""))
        entry = float(pos.get("entry_price", 0) or 0)
        amount = float(pos.get("amount", 0) or 0)
        if entry <= 0 or amount <= 0:
            return {"action": "close", "reason": "position invalid"}
        pnl_pct = (cp - entry) / entry if side == "long" else (entry - cp) / entry
        entry_time = pos.get("entry_time")
        if isinstance(entry_time, datetime):
            hold_min = max(0.0, (datetime.now() - entry_time).total_seconds() / 60)
        else:
            hold_min = 0.0
        max_gain = ((float(pos.get("highest", entry) or entry) - entry) / entry if side == "long"
                    else (entry - float(pos.get("lowest", entry) or entry)) / entry)
        cfg = get_cfg(equity)
        payload = self._payload(side, exit_reason, cp, klines, ind, equity, margin_level, cfg, risk, context)
        payload["task"] = "exit_decision"
        payload["position"] = {
            "side": side,
            "entry_price": round(entry, 4),
            "amount_sol": round(amount, 6),
            "pnl_pct": round(pnl_pct * 100, 3),
            "hold_minutes": round(hold_min, 1),
            "max_gain_pct": round(max_gain * 100, 3),
            "exit_trigger": exit_reason,
            "current_plan": pos.get("ai_exit_plan") or {},
        }
        payload["return_schema"] = {
            "action": "close|hold|reverse_long|reverse_short",
            "reason": "short Chinese reason",
            "next_take_profit_pct": "positive decimal, e.g. 0.012",
            "next_stop_loss_pct": "positive decimal, e.g. 0.026",
            "cap_use": "optional for reverse, 0.10-0.70",
        }
        sig = self._signature("exit", side, cp, ind, risk, exit_reason)
        if sig in self._cache:
            return self._cache[sig][1]
        try:
            decision = self._request_json(payload, max_tokens=180)
            action = str(decision.get("action", "close")).lower()
            next_tp = float(decision.get("next_take_profit_pct", TAKE_PROFIT_PCT) or TAKE_PROFIT_PCT)
            next_sl = float(decision.get("next_stop_loss_pct", STOP_LOSS_PCT) or STOP_LOSS_PCT)
            next_tp = max(MIN_TAKE_PROFIT_PCT, min(0.035, next_tp))
            next_sl = max(MIN_DYNAMIC_STOP_LOSS_PCT, min(HARD_MAX_LOSS_PCT, next_sl))
            reverse_side = None
            if action in {"reverse_long", "reverse_short"}:
                reverse_side = "long" if action.endswith("long") else "short"
                if reverse_side == side:
                    reverse_side = None
                    action = "close"
            reverse_cap = float(decision.get("cap_use", cfg.get("cap_use")) or cfg.get("cap_use"))
            reverse_cap = max(MIN_AI_CAP_USE, min(self.effective_max_cap_use(cfg), reverse_cap))
            result = {
                "action": "hold" if action == "hold" else ("reverse" if reverse_side else "close"),
                "reason": str(decision.get("reason", ""))[:160],
                "next_take_profit_pct": next_tp,
                "next_stop_loss_pct": next_sl,
                "pnl_pct": round(pnl_pct * 100, 3),
                "reverse_side": reverse_side,
                "cap_use": reverse_cap,
            }
            logger.info(
                f"DeepSeek出场: {result['action']} reverse={reverse_side or '-'} "
                f"tp={next_tp*100:.2f}% sl={next_sl*100:.2f}% reason={result['reason']}"
            )
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as e:
            cached = self._latest_cached("exit", side) if self.features.ai_cache_fallback else None
            if cached:
                result = {**cached[1], "reason": f"AI出场失败，用缓存判断: {cached[1].get('reason', '')}"[:160], "source": "cache_fallback"}
                logger.warning(f"DeepSeek出场失败，使用缓存判断: {e}")
            else:
                result = {"action": "close", "reason": f"DeepSeek出场失败默认平仓: {e}"}
                logger.warning(f"DeepSeek出场失败，默认平仓: {e}")
        self._cache[sig] = (result.get("action") == "hold", result)
        self._save_ai_decision("exit", side, cp, payload, result)
        if len(self._cache) > 64:
            self._cache.pop(next(iter(self._cache)))
        return result


# ============================================================
# Technical indicators.
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
# Binance API wrapper. Trading uses the margin account only.
# ============================================================

class BinanceAPI:

    def __init__(self, api_key: str, secret: str, proxy: str = "", cf_worker: str = ""):
        base = {
            "apiKey": api_key, "secret": secret,
            "enableRateLimit": True, "timeout": 60000,
        }
        if proxy and not cf_worker:
            base["proxies"] = {"http": proxy, "https": proxy}
            logger.info(f"Using proxy: {proxy}")

        self.spot = ccxt.binance({**base, "options": {"defaultType": "spot"}})
        self.margin = ccxt.binance({**base, "options": {"defaultType": "margin"}})
        self.futures = ccxt.binance({**base, "options": {"defaultType": "future"}})

        if cf_worker:
            self._use_cf_worker(cf_worker)

        # Load markets with retries because the proxy or CF relay can be unstable.
        for name, ex in [("spot", self.spot), ("margin", self.margin), ("futures", self.futures)]:
            for attempt in range(5):
                try:
                    ex.load_markets()
                    break
                except Exception as e:
                    if attempt < 4:
                        wait = (attempt + 1) * 5
                        logger.warning(f"{name} load failed (attempt {attempt + 1}/5): {e}; retry in {wait}s")
                        time.sleep(wait)
                    else:
                        raise

        self.market = self.spot.market(SYMBOL)
        logger.info("API ready | margin trading mode")

    # ---- Balance reads ----

    def _use_cf_worker(self, cf_worker: str):
        cf_worker = cf_worker.rstrip("/")
        for ex in [self.spot, self.margin, self.futures]:
            for key, old in list(ex.urls.get("api", {}).items()):
                parsed = urlparse(old)
                ex.urls["api"][key] = cf_worker + parsed.path
        logger.info(f"Using CF Worker relay: {cf_worker}")

    def _is_asset(self, key: str, info) -> bool:
        if key in META_KEYS:
            return False
        if not isinstance(info, dict):
            return False
        return "free" in info or "total" in info

    def get_margin_usdt(self) -> float:
        """Available USDT in margin account."""
        try:
            b = self.margin.fetch_balance()
            info = b.get("USDT")
            if isinstance(info, dict):
                free = float(info.get("free", 0) or 0)
                total = float(info.get("total", 0) or 0)
                used = float(info.get("used", 0) or 0)
                logger.info(f"[DEBUG] margin USDT: free={free:.4f} total={total:.4f} used={used:.4f}")
                result = free if free > 0 else total
                if result > 0:
                    return result
        except Exception:
            pass
        return self.get_margin_total_usdt()

    def get_margin_total_usdt(self) -> float:
        """Total USDT in margin account, including used margin."""
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

    def get_margin_asset_total(self, asset: str) -> float:
        try:
            b = self.margin.fetch_balance()
            info = b.get(asset)
            if isinstance(info, dict):
                return float(info.get("total", 0) or 0)
        except Exception:
            pass
        return 0.0

    def get_margin_asset_borrowed(self, asset: str) -> float:
        try:
            b = self.margin.fetch_balance()
            info = b.get(asset)
            if isinstance(info, dict):
                return float(info.get("debt", info.get("borrowed", 0)) or 0)
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
        """Margin account net equity in USDT."""
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
        """Margin level percentage. Higher is safer."""
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
                return 999.0
            return (total_asset / total_debt) * 100.0
        except Exception:
            return 0.0

    def get_all_snapshot(self) -> dict:
        """Balance snapshot for display only."""
        snap = {"funding": 0.0, "spot": 0.0, "margin": 0.0, "futures": 0.0}

        # Margin account.
        try:
            snap["margin"] = self.get_margin_total_usdt()
        except Exception:
            pass

        # Spot account.
        try:
            b = self.spot.fetch_balance()
            snap["spot"] = float((b.get("USDT") or {}).get("total", 0) or 0)
        except Exception:
            pass

        # Funding account.
        try:
            resp = self.spot.sapiGetAssetGetFundingAsset()
            for item in resp:
                if item.get("asset") == "USDT":
                    snap["funding"] = float(item.get("free", 0) or 0)
        except Exception:
            pass

        # Futures account.
        try:
            b = self.futures.fetch_balance()
            info = b.get("USDT")
            if isinstance(info, dict):
                snap["futures"] = float(info.get("total", info.get("free", 0)) or 0)
        except Exception:
            pass

        return snap

    # ---- Trading ----

    def fetch_klines(self, limit: int = 100, timeframe: str = TIMEFRAME) -> dict:
        ohlcv = self.spot.fetch_ohlcv(SYMBOL, timeframe=timeframe, limit=limit)
        return {
            "close": np.array([c[4] for c in ohlcv], dtype=np.float64),
            "open": np.array([c[1] for c in ohlcv], dtype=np.float64),
            "high": np.array([c[2] for c in ohlcv], dtype=np.float64),
            "low": np.array([c[3] for c in ohlcv], dtype=np.float64),
            "volume": np.array([c[5] for c in ohlcv], dtype=np.float64),
            "timestamp": [c[0] for c in ohlcv],
        }

    def fetch_klines_multi(self, limit: int = 100) -> dict:
        result = {}
        for tf in [TIMEFRAME, MTF_15M, MTF_1H]:
            try:
                result[tf] = self.fetch_klines(limit=limit, timeframe=tf)
            except Exception as e:
                logger.warning(f"MTF fetch {tf} failed: {e}")
                result[tf] = None
        return result

    def fetch_ticker(self):
        return self.spot.fetch_ticker(SYMBOL)

    @staticmethod
    def extract_fill_price(order: dict, fallback: float) -> float:
        """Extract average fill price from Binance order response."""
        cum_qty = float(order.get("cummulativeQuoteQty", 0) or 0)
        exe_qty = float(order.get("executedQty", 0) or 0)
        if exe_qty > 0 and cum_qty > 0:
            return cum_qty / exe_qty
        # Native Binance fills array.
        fills = order.get("fills", [])
        if fills and isinstance(fills, list) and len(fills) > 0:
            prices = [float(f.get("price", 0) or 0) for f in fills]
            if prices and sum(prices) > 0:
                return sum(prices) / len(prices)
        # Standard ccxt fields.
        for key in ["average", "price"]:
            v = order.get(key)
            if v is not None and float(v) > 0:
                return float(v)
        return fallback

    def round_amount(self, amount: float) -> float:
        step = float(self.market.get("precision", {}).get("amount") or 0.001)
        if step <= 0:
            step = 0.001
        decimals = max(0, int(round(-np.log10(step)))) if step < 1 else 0
        floored = np.floor(max(0.0, float(amount)) / step) * step
        return max(0, round(float(floored), decimals))

    def _margin_order(self, side: str, amount: float, effect_types: list = None,
                      allow_balance_fallback: bool = True,
                      allow_ccxt_fallback: bool = True) -> dict:
        """Place a native margin order, trying multiple sideEffectType values."""
        qty = self.round_amount(amount)
        base = {"symbol": "SOLUSDT", "side": side, "type": "MARKET", "quantity": str(qty)}
        if qty <= 0:
            raise ValueError(f"invalid order amount: {amount}")
        # Buy/sell use different side effect fallback sequences.
        if effect_types is None:
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
                    logger.info(f"order succeeded ({method_name} sideEffect={effect}): {qty} SOL")
                    return o
                except Exception as e:
                    last_err = e
                    err_str = str(e)[:120]
                    logger.warning(f"  {effect} failed: {err_str}")
                    if "insufficient" not in str(e).lower():
                        break

        # Fallback to actual account balance when close quantity is slightly off.
        if allow_balance_fallback and side == "SELL":
            actual_sol = self.get_margin_sol()
            if 0 < actual_sol < qty:
                actual_qty = self.round_amount(actual_sol)
                if actual_qty <= 0 or actual_qty >= qty:
                    raise last_err or ValueError(f"actual SOL balance is insufficient: {actual_sol}")
                logger.warning(f"position qty {qty} differs from actual SOL {actual_sol}; using {actual_qty}")
                return self._margin_order(side, actual_qty, effect_types, allow_balance_fallback=False,
                                          allow_ccxt_fallback=allow_ccxt_fallback)
        elif allow_balance_fallback and side == "BUY":
            actual_usdt = self.get_margin_usdt()
            if actual_usdt > 0:
                ticker = self.fetch_ticker()
                alt_qty = self.round_amount(actual_usdt * 0.99 / ticker["last"])
                if 0 < alt_qty < qty:
                    logger.warning(f"try reduced buy amount: {alt_qty}")
                    return self._margin_order(side, alt_qty, effect_types, allow_balance_fallback=False,
                                              allow_ccxt_fallback=allow_ccxt_fallback)

        if not allow_ccxt_fallback:
            raise last_err or RuntimeError(f"order failed: {side} {qty}")

        # fallback2: ccxt create_order
        logger.warning(f"all sideEffectType attempts failed; fallback to ccxt. last_err: {str(last_err)[:200] if last_err else 'none'}")
        o = self.margin.create_order(SYMBOL, "market", side.lower(), qty)
        fill = o.get("average", o.get("price"))
        logger.info(f"ccxt order filled: {qty} SOL @ {fill}")
        return o

    def margin_order_ioc(self, side: str, amount: float, limit_price: float) -> dict:
        qty = self.round_amount(amount)
        if qty <= 0:
            raise ValueError(f"invalid IOC order amount: {amount}")
        side_l = str(side).lower()
        if side_l not in {"buy", "sell"}:
            raise ValueError(f"invalid IOC side: {side}")
        limit_price = float(limit_price or 0)
        if limit_price <= 0:
            raise ValueError(f"invalid IOC limit price: {limit_price}")
        effects = ["NO_SIDE_EFFECT", "MARGIN_BUY"] if side_l == "buy" else ["AUTO_BORROW_REPAY", "NO_SIDE_EFFECT"]
        last_err = None
        for effect in effects:
            try:
                params = {"timeInForce": "GTC", "sideEffectType": effect}
                logger.info(f">>> margin simulated IOC {side_l} {qty} SOL @ {limit_price:.4f} sideEffect={effect}")
                order = self.margin.create_order(SYMBOL, "limit", side_l, qty, limit_price, params)
                time.sleep(1)
                fetched = order
                order_id = order.get("id") or order.get("orderId")
                if order_id:
                    try:
                        fetched = self.margin.fetch_order(str(order_id), SYMBOL)
                    except Exception as e:
                        logger.warning(f"IOC fetch order failed: {e}")
                filled = float(fetched.get("filled", fetched.get("executedQty", 0)) or 0)
                remaining = float(fetched.get("remaining", max(0.0, qty - filled)) or 0)
                if remaining > 0.000001 and order_id:
                    try:
                        self.margin.cancel_order(str(order_id), SYMBOL)
                        logger.info(f"IOC cancelled remaining {remaining:.6f} SOL")
                    except Exception as e:
                        logger.warning(f"IOC cancel remaining failed: {e}")
                if filled <= 0:
                    raise RuntimeError("IOC order not filled")
                fetched.setdefault("executedQty", filled)
                logger.info(f"IOC filled {filled:.6f}/{qty:.6f} SOL")
                return fetched
            except Exception as e:
                last_err = e
                logger.warning(f"IOC {side_l} failed ({effect}): {str(e)[:160]}")
        raise last_err or RuntimeError("IOC order failed")

    def margin_buy(self, amount: float) -> dict:
        logger.info(f">>> margin buy {amount} SOL")
        return self._margin_order("BUY", amount)

    def margin_sell(self, amount: float) -> dict:
        logger.info(f">>> margin sell {amount} SOL")
        return self._margin_order("SELL", amount)

    def close_long(self, amount: float) -> dict:
        logger.info(f">>> close long by selling {amount} SOL")
        # Close long using held SOL only.
        actual_sol = self.get_margin_sol()
        qty = min(amount, actual_sol)
        return self._margin_order("SELL", qty, effect_types=["AUTO_REPAY", "NO_SIDE_EFFECT"],
                                  allow_balance_fallback=True, allow_ccxt_fallback=False)

    def close_short(self, amount: float) -> dict:
        logger.info(f">>> close short by buying {amount} SOL")
        # Close short by buying back and repaying borrowed SOL.
        return self._margin_order("BUY", amount, effect_types=["AUTO_REPAY", "NO_SIDE_EFFECT", "MARGIN_BUY"],
                                  allow_balance_fallback=True, allow_ccxt_fallback=False)

    def _repay_asset(self, asset: str, amount: float) -> bool:
        amount = float(amount or 0)
        if amount <= 0:
            return False
        if asset == "SOL":
            amt = self.round_amount(amount)
        else:
            amt = round(amount, 6)
        if amt <= 0:
            return False
        params_new = {"asset": asset, "isIsolated": "FALSE", "amount": str(amt), "type": "REPAY"}
        params_old = {"asset": asset, "amount": str(amt)}
        for method_name, params in [
            ("sapiPostMarginBorrowRepay", params_new),
            ("sapi_post_margin_borrow_repay", params_new),
            ("sapiPostMarginRepay", params_old),
            ("sapi_post_margin_repay", params_old),
        ]:
            fn = getattr(self.margin, method_name, None)
            if not fn:
                continue
            try:
                fn(params)
                logger.info(f"manual repay succeeded: {asset} {amt}")
                return True
            except Exception as e:
                logger.warning(f"manual repay failed ({method_name} {asset} {amt}): {str(e)[:160]}")
        return False

    def repay_available_debts(self):
        # Executions and auto-repay can lag briefly; wait before reading balances.
        time.sleep(0.5)
        for asset in ["SOL", "USDT"]:
            debt = self.get_margin_asset_borrowed(asset)
            total = self.get_margin_asset_total(asset)
            repay_amt = min(debt, total)
            if repay_amt > 0.000001:
                self._repay_asset(asset, repay_amt)


# ============================================================
# Strategy engine.
# ============================================================

class StrategyEngine:
    def __init__(self, runtime: RuntimeConfig = None):
        self.runtime = runtime or build_runtime_config({})
        self.features = self.runtime.features

    def compute(self, klines: dict) -> dict:
        c, h, l, v = klines["close"], klines["high"], klines["low"], klines["volume"]
        ml, sl, hist = macd(c, MACD_FAST, MACD_SLOW, MACD_SIG)
        bm, bu, bl = bb(c, BB_PERIOD, BB_STD)
        bb_width = np.divide(
            bu - bl, bm,
            out=np.full_like(bm, np.nan, dtype=np.float64),
            where=~np.isnan(bm) & (bm != 0),
        )
        return {
            "ema_f": ema(c, EMA_FAST), "ema_m": ema(c, EMA_MID),
            "ema_s": ema(c, EMA_SLOW), "rsi": rsi(c, RSI_PERIOD),
            "macd_l": ml, "sig_l": sl, "hist": hist,
            "bb_m": bm, "bb_u": bu, "bb_l": bl, "bb_width": bb_width,
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

    def mtf_trend(self, klines_15m, klines_1h) -> dict:
        def one_tf(klines):
            if not klines:
                return "neutral"
            try:
                c = klines["close"]
                if len(c) < EMA_SLOW + MACD_SLOW + MACD_SIG:
                    return "neutral"
                ema30 = ema(c, EMA_SLOW)
                _, _, hist = macd(c, MACD_FAST, MACD_SLOW, MACD_SIG)
                price = float(c[-1])
                ema_now = self.last(ema30)
                hist_now = self.last(hist)
                if np.isnan(ema_now) or np.isnan(hist_now):
                    return "neutral"
                if price > ema_now and hist_now > 0:
                    return "bull"
                if price < ema_now and hist_now < 0:
                    return "bear"
                return "neutral"
            except Exception as e:
                logger.warning(f"MTF trend calc failed: {e}")
                return "neutral"

        trend_15m = one_tf(klines_15m)
        trend_1h = one_tf(klines_1h)
        aligned_bull = (
            (trend_15m == "bull" and trend_1h == "bull") or
            (trend_15m == "bull" and trend_1h == "neutral") or
            (trend_15m == "neutral" and trend_1h == "bull")
        )
        aligned_bear = (
            (trend_15m == "bear" and trend_1h == "bear") or
            (trend_15m == "bear" and trend_1h == "neutral") or
            (trend_15m == "neutral" and trend_1h == "bear")
        )
        consensus = "bull" if aligned_bull else ("bear" if aligned_bear else "neutral")
        return {
            "15m": trend_15m,
            "1h": trend_1h,
            "aligned_bull": aligned_bull,
            "aligned_bear": aligned_bear,
            "consensus": consensus,
        }

    def _bullish_rsi_divergence(self, klines: dict, ind: dict) -> bool:
        c, rsi_arr = klines["close"], ind["rsi"]
        if len(c) < RSI_DIVERGENCE_LOOKBACK + 3:
            return False
        prev_prices = c[-RSI_DIVERGENCE_LOOKBACK:-3]
        prev_rsi = rsi_arr[-RSI_DIVERGENCE_LOOKBACK:-3]
        valid = ~np.isnan(prev_rsi)
        if not np.any(valid) or np.isnan(rsi_arr[-1]):
            return False
        prev_prices = prev_prices[valid]
        prev_rsi = prev_rsi[valid]
        idx = int(np.argmin(prev_prices))
        return c[-1] <= prev_prices[idx] * 1.001 and rsi_arr[-1] >= prev_rsi[idx] + RSI_DIVERGENCE_MIN_GAP

    def _bearish_rsi_divergence(self, klines: dict, ind: dict) -> bool:
        c, rsi_arr = klines["close"], ind["rsi"]
        if len(c) < RSI_DIVERGENCE_LOOKBACK + 3:
            return False
        prev_prices = c[-RSI_DIVERGENCE_LOOKBACK:-3]
        prev_rsi = rsi_arr[-RSI_DIVERGENCE_LOOKBACK:-3]
        valid = ~np.isnan(prev_rsi)
        if not np.any(valid) or np.isnan(rsi_arr[-1]):
            return False
        prev_prices = prev_prices[valid]
        prev_rsi = prev_rsi[valid]
        idx = int(np.argmax(prev_prices))
        return c[-1] >= prev_prices[idx] * 0.999 and rsi_arr[-1] <= prev_rsi[idx] - RSI_DIVERGENCE_MIN_GAP

    def check_long(self, ind: dict, klines: dict, strict_level: int = 0,
                   mtf_context: dict = None) -> Tuple[bool, str]:
        c, o, l, v = klines["close"], klines["open"], klines["low"], klines["volume"]
        e5, e13 = self.last(ind["ema_f"]), self.last(ind["ema_m"])
        e30 = self.last(ind["ema_s"])
        r, rp = self.last(ind["rsi"]), self.prev(ind["rsi"])
        bbl = self.last(ind["bb_l"])
        bbw = self.last(ind["bb_width"])
        vol_ma = self.last(ind["vol_ma"])
        price = c[-1]
        a = self.last(ind["atr"])
        ema_sep = abs(e5 - e13) / price if price > 0 and not (np.isnan(e5) or np.isnan(e13)) else 0
        atr_pct = a / price if price > 0 and not np.isnan(a) else 0
        if np.isnan(bbl) or np.isnan(r) or np.isnan(rp):
            return False, "LONG wait indicators not ready"
        strict_mult = FAKE_BREAKOUT_STRICT_MULT if strict_level > 0 else 1.0
        bb_touch_limit = BB_LONG_TOUCH - (0.0015 * strict_level)
        bb_touch = l[-1] <= bbl * bb_touch_limit
        candle_back = c[-1] > o[-1]
        div = self.features.signal_rsi_divergence and self._bullish_rsi_divergence(klines, ind)
        rsi_turn = (r > rp and r <= MAX_LONG_RSI) or div
        active = atr_pct >= MIN_ATR_PCT
        vol_ok = True
        if self.features.signal_volume_filter:
            vol_ok = not np.isnan(vol_ma) and v[-1] >= vol_ma * VOL_CONFIRM_MULT * strict_mult
        trend_ok = True
        if self.features.signal_ema_trend_filter:
            trend_ok = not (np.isnan(e5) or np.isnan(e13) or np.isnan(e30)) and e5 > e13 > e30
        bbw_ok = True
        if self.features.signal_bb_width_filter:
            bbw_ok = not np.isnan(bbw) and bbw >= MIN_BB_WIDTH_PCT * strict_mult
        mtf_ok = True
        if self.features.multi_timeframe_filter and mtf_context:
            mtf_ok = mtf_context.get("consensus") != "bear"
        if active and bb_touch and candle_back and rsi_turn and vol_ok and trend_ok and bbw_ok and mtf_ok:
            return True, (
                f"LONG BB回归 atr={atr_pct*100:.3f}% rsi={r:.1f} "
                f"low/bbl={l[-1]/bbl:.4f} sep={ema_sep*100:.3f}% "
                f"vol={v[-1]/vol_ma if vol_ma and not np.isnan(vol_ma) else 0:.2f}x "
                f"bbw={bbw*100 if not np.isnan(bbw) else 0:.2f}% div={div} strict={strict_level}"
            )
        return False, (
            f"LONG wait BB={bb_touch} candle={candle_back} rsiTurn={rsi_turn} "
            f"vol={vol_ok} trend={trend_ok} bbw={bbw_ok} mtf={mtf_ok} atr={atr_pct*100:.3f}%"
        )

    def check_short(self, ind: dict, klines: dict, strict_level: int = 0,
                    mtf_context: dict = None) -> Tuple[bool, str]:
        c, o, h, v = klines["close"], klines["open"], klines["high"], klines["volume"]
        e5, e13 = self.last(ind["ema_f"]), self.last(ind["ema_m"])
        e30 = self.last(ind["ema_s"])
        r, rp = self.last(ind["rsi"]), self.prev(ind["rsi"])
        bbu = self.last(ind["bb_u"])
        bbw = self.last(ind["bb_width"])
        vol_ma = self.last(ind["vol_ma"])
        price = c[-1]
        a = self.last(ind["atr"])
        ema_sep = abs(e5 - e13) / price if price > 0 and not (np.isnan(e5) or np.isnan(e13)) else 0
        atr_pct = a / price if price > 0 and not np.isnan(a) else 0
        if np.isnan(bbu) or np.isnan(r) or np.isnan(rp):
            return False, "SHORT wait indicators not ready"
        strict_mult = FAKE_BREAKOUT_STRICT_MULT if strict_level > 0 else 1.0
        bb_touch_limit = BB_SHORT_TOUCH + (0.0015 * strict_level)
        bb_touch = h[-1] >= bbu * bb_touch_limit
        candle_back = c[-1] < o[-1]
        div = self.features.signal_rsi_divergence and self._bearish_rsi_divergence(klines, ind)
        rsi_turn = (r < rp and r >= MIN_SHORT_RSI) or div
        active = atr_pct >= MIN_ATR_PCT
        vol_ok = True
        if self.features.signal_volume_filter:
            vol_ok = not np.isnan(vol_ma) and v[-1] >= vol_ma * VOL_CONFIRM_MULT * strict_mult
        trend_ok = True
        if self.features.signal_ema_trend_filter:
            trend_ok = not (np.isnan(e5) or np.isnan(e13) or np.isnan(e30)) and e5 < e13 < e30
        bbw_ok = True
        if self.features.signal_bb_width_filter:
            bbw_ok = not np.isnan(bbw) and bbw >= MIN_BB_WIDTH_PCT * strict_mult
        mtf_ok = True
        if self.features.multi_timeframe_filter and mtf_context:
            mtf_ok = mtf_context.get("consensus") != "bull"
        if active and bb_touch and candle_back and rsi_turn and vol_ok and trend_ok and bbw_ok and mtf_ok:
            return True, (
                f"SHORT BB回归 atr={atr_pct*100:.3f}% rsi={r:.1f} "
                f"high/bbu={h[-1]/bbu:.4f} sep={ema_sep*100:.3f}% "
                f"vol={v[-1]/vol_ma if vol_ma and not np.isnan(vol_ma) else 0:.2f}x "
                f"bbw={bbw*100 if not np.isnan(bbw) else 0:.2f}% div={div} strict={strict_level}"
            )
        return False, (
            f"SHORT wait BB={bb_touch} candle={candle_back} rsiTurn={rsi_turn} "
            f"vol={vol_ok} trend={trend_ok} bbw={bbw_ok} mtf={mtf_ok} atr={atr_pct*100:.3f}%"
        )

    def check_long_exit(self, ind: dict, entry: float, cp: float,
                        highest: float, entry_time: datetime) -> Tuple[bool, str]:
        hold = (datetime.now() - entry_time).total_seconds() / 60
        pnl_pct = (cp - entry) / entry if entry > 0 else 0
        max_gain = (highest - entry) / entry if entry > 0 else 0
        ema_bear = self.last(ind["ema_f"]) < self.last(ind["ema_m"])
        macd_bear = self.last(ind["hist"]) < 0 and self.last(ind["macd_l"]) < self.last(ind["sig_l"])
        rsi_now, rsi_prev = self.last(ind["rsi"]), self.prev(ind["rsi"])
        if pnl_pct >= TAKE_PROFIT_PCT:
            return True, f"快速止盈{TAKE_PROFIT_PCT*100:.1f}%"
        if max_gain >= BREAK_EVEN_ACTIVATE and pnl_pct <= BREAK_EVEN_BUFFER:
            return True, "保本止盈"
        if max_gain >= TRAILING_ACTIVATE:
            lock_gain = max(BREAK_EVEN_BUFFER, max_gain * PROFIT_RETRACE_KEEP)
            if pnl_pct <= lock_gain or cp <= highest * (1 - TRAILING_DIST):
                return True, f"盈利回撤保护({max_gain*100:.2f}%->{pnl_pct*100:.2f}%)"
        if pnl_pct >= MIN_PROFIT_EXIT and (macd_bear or (not np.isnan(rsi_prev) and rsi_now < rsi_prev - 10)):
            return True, "盈利动能转弱"
        if cp <= entry * (1 - STOP_LOSS_PCT):
            return True, f"止损-{STOP_LOSS_PCT*100:.1f}%"
        a = self.last(ind["atr"])
        if ATR_STOP_MULT is not None and not np.isnan(a) and cp <= entry - ATR_STOP_MULT * a:
            return True, "ATR止损"
        if TIME_STOP_MIN is not None and hold >= TIME_STOP_MIN:
            return True, f"时间止损({hold:.0f}min)"
        if ema_bear and (pnl_pct <= -0.002 or macd_bear):
            return True, "EMA bear confirmed"
        if self.last(ind["rsi"]) > 82:
            return True, "RSI超买"
        return False, ""

    def check_short_exit(self, ind: dict, entry: float, cp: float,
                         lowest: float, entry_time: datetime) -> Tuple[bool, str]:
        hold = (datetime.now() - entry_time).total_seconds() / 60
        pnl_pct = (entry - cp) / entry if entry > 0 else 0
        max_gain = (entry - lowest) / entry if entry > 0 else 0
        ema_bull = self.last(ind["ema_f"]) > self.last(ind["ema_m"])
        macd_bull = self.last(ind["hist"]) > 0 and self.last(ind["macd_l"]) > self.last(ind["sig_l"])
        rsi_now, rsi_prev = self.last(ind["rsi"]), self.prev(ind["rsi"])
        if pnl_pct >= TAKE_PROFIT_PCT:
            return True, f"快速止盈{TAKE_PROFIT_PCT*100:.1f}%"
        if max_gain >= BREAK_EVEN_ACTIVATE and pnl_pct <= BREAK_EVEN_BUFFER:
            return True, "保本止盈"
        if max_gain >= TRAILING_ACTIVATE:
            lock_gain = max(BREAK_EVEN_BUFFER, max_gain * PROFIT_RETRACE_KEEP)
            if pnl_pct <= lock_gain or cp >= lowest * (1 + TRAILING_DIST):
                return True, f"盈利回撤保护({max_gain*100:.2f}%->{pnl_pct*100:.2f}%)"
        if pnl_pct >= MIN_PROFIT_EXIT and (macd_bull or (not np.isnan(rsi_prev) and rsi_now > rsi_prev + 10)):
            return True, "盈利动能转弱"
        if cp >= entry * (1 + STOP_LOSS_PCT):
            return True, f"止损+{STOP_LOSS_PCT*100:.1f}%"
        a = self.last(ind["atr"])
        if ATR_STOP_MULT is not None and not np.isnan(a) and cp >= entry + ATR_STOP_MULT * a:
            return True, "ATR止损"
        if TIME_STOP_MIN is not None and hold >= TIME_STOP_MIN:
            return True, f"时间止损({hold:.0f}min)"
        if ema_bull and (pnl_pct <= -0.002 or macd_bull):
            return True, "EMA bull confirmed"
        if self.last(ind["rsi"]) < 18:
            return True, "RSI超卖"
        return False, ""


# ============================================================
class RiskManager:
    def __init__(self, storage: Storage = None):
        self._db = storage
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.consec_losses = 0
        self.date = datetime.now().date()
        self.pause_until: Optional[datetime] = None
        self.start_equity: Optional[float] = None
        self._state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "risk_state.json")
        self._load_state()

    def _load_state(self):
        try:
            if not os.path.exists(self._state_file):
                return
            with open(self._state_file, "r") as f:
                data = json.load(f)
            if data.get("date") != self.date.isoformat():
                return
            self.daily_pnl = float(data.get("daily_pnl", 0) or 0)
            self.daily_trades = int(data.get("daily_trades", 0) or 0)
            self.consec_losses = int(data.get("consec_losses", 0) or 0)
            self.start_equity = data.get("start_equity")
            if self.start_equity is not None:
                self.start_equity = float(self.start_equity)
            pause_until = data.get("pause_until")
            if pause_until:
                self.pause_until = datetime.fromisoformat(pause_until)
            logger.info(
                f"Loaded risk state: trades={self.daily_trades}, pnl={self.daily_pnl:.2f}, "
                f"losses={self.consec_losses}"
            )
        except Exception as e:
            logger.warning(f"Load risk state failed: {e}")

    def _save_state(self):
        try:
            with open(self._state_file, "w") as f:
                json.dump({
                    "date": self.date.isoformat(),
                    "daily_pnl": self.daily_pnl,
                    "daily_trades": self.daily_trades,
                    "consec_losses": self.consec_losses,
                    "pause_until": self.pause_until.isoformat() if self.pause_until else None,
                    "start_equity": self.start_equity,
                }, f)
        except Exception as e:
            logger.warning(f"Save risk state failed: {e}")
        if self._db and self._db.enabled:
            self._db.save_risk_state(
                self.date.isoformat(),
                self.daily_pnl,
                self.daily_trades,
                self.consec_losses,
                self.start_equity or 0,
            )

    def _reset(self):
        t = datetime.now().date()
        if t != self.date:
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.date = t
            self.consec_losses = 0
            self.pause_until = None
            self.start_equity = None
            self._save_state()

    def can_trade(self, equity: float) -> Tuple[bool, str]:
        self._reset()
        if self.start_equity is None:
            self.start_equity = equity
            self._save_state()
        if self.pause_until and datetime.now() < self.pause_until:
            r = (self.pause_until - datetime.now()).seconds // 60
            return False, f"paused {r}min"
        if self.daily_pnl < -self.start_equity * MAX_DAILY_LOSS_PCT:
            return False, "daily loss limit"
        if self.daily_trades >= MAX_DAILY_TRADES:
            return False, "daily trade limit"
        return True, ""

    def record(self, pnl: float):
        self.daily_trades += 1
        self.daily_pnl += pnl
        if pnl < 0:
            self.consec_losses += 1
            if self.consec_losses >= MAX_CONSEC_LOSS:
                self.pause_until = datetime.now() + timedelta(minutes=PAUSE_MINUTES)
                logger.warning(f"consecutive losses reached {MAX_CONSEC_LOSS}; pause {PAUSE_MINUTES}min")
        else:
            self.consec_losses = 0
        logger.info(f"today: trades={self.daily_trades} PnL={self.daily_pnl:.2f}")
        self._save_state()

    @staticmethod
    def near_hour() -> bool:
        m = datetime.now().minute
        return m < 2 or m >= 58


# ============================================================
class TradingBot:
    def __init__(self, api_key: str, secret: str, proxy: str = "", cf_worker: str = "",
                 deepseek_api_key: str = "", deepseek_model: str = "deepseek-chat",
                 deepseek_enabled: bool = True, runtime: RuntimeConfig = None):
        self.runtime = runtime or build_runtime_config({})
        self.features = self.runtime.features
        self.db = Storage(enabled=self.features.sqlite_storage, keep_days=self.runtime.db_keep_days)
        self.api = BinanceAPI(api_key, secret, proxy, cf_worker)
        self.strategy = StrategyEngine(self.runtime)
        self.risk = RiskManager(self.db)
        self.advisor = DeepSeekRiskAdvisor(deepseek_api_key, deepseek_model, deepseek_enabled,
                                           runtime=self.runtime, storage=self.db)
        self.position: Optional[dict] = None
        self.highest = 0.0
        self.lowest = 1e9
        self.running = True
        self._pos_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "position.json")
        self._trade_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.json")
        self.cooldown_until: Optional[datetime] = None
        self._recent_ai_skips = []
        self.slippage = SlippageTracker()

    def _write_position_file(self, current_price: float = 0.0,
                             margin_level: float = None, equity: float = None):
        """Write position snapshot for monitor."""
        try:
            if self.position:
                with open(self._pos_file, "w") as f:
                    json.dump({
                        "side": self.position["side"],
                        "amount": self.position["amount"],
                        "entry_price": self.position["entry_price"],
                        "entry_time": self.position["entry_time"].isoformat(),
                        "highest": self.highest,
                        "lowest": self.lowest,
                        "ai_entry": self.position.get("ai_entry"),
                        "ai_exit_plan": self.position.get("ai_exit_plan"),
                        "ai_exit_checks": self.position.get("ai_exit_checks", []),
                        "last_ai_review_time": self.position.get("last_ai_review_time"),
                        "slippage": self.slippage.to_dict(),
                    }, f)
            else:
                if os.path.exists(self._pos_file):
                    os.remove(self._pos_file)
        except Exception:
            pass
        if self.db.enabled:
            try:
                price = current_price or (self.position or {}).get("entry_price") or 0
                self.db.save_position_snapshot(
                    self.position or {},
                    price=float(price or 0),
                    margin_level=margin_level if margin_level is not None else 0,
                    equity=equity if equity is not None else 0,
                )
            except Exception as e:
                logger.warning(f"SQLite position snapshot skipped: {e}")

    def _read_position_file(self) -> dict:
        try:
            if os.path.exists(self._pos_file):
                with open(self._pos_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    @staticmethod
    def _parse_position_time(value):
        if not value:
            return datetime.now()
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return datetime.now()

    def _log_trade(self, action: str, side: str, amount: float, price: float, pnl: float = 0, **meta):
        """Append a trade record to trades.json."""
        try:
            trades = []
            if os.path.exists(self._trade_file):
                with open(self._trade_file) as f:
                    trades = json.load(f)
            record = {
                "time": datetime.now().strftime("%m/%d %H:%M:%S"),
                "action": action,
                "side": side,
                "amount": round(amount, 4),
                "price": round(price, 4),
                "pnl": round(pnl, 4) if pnl else None,
            }
            for key, value in meta.items():
                if value is not None:
                    record[key] = value
            trades.append(record)
            # Keep the latest 50 records.
            trades = trades[-50:]
            with open(self._trade_file, "w") as f:
                json.dump(trades, f, ensure_ascii=False)
            self._log_trade_sqlite(action, side, amount, price, pnl, record, meta)
        except Exception:
            pass

    def _log_trade_sqlite(self, action: str, side: str, amount: float, price: float,
                          pnl: float, record: dict, meta: dict):
        if not self.db.enabled:
            return
        if action not in {"开仓", "平仓"}:
            return
        try:
            pos = self.position or {}
            now = datetime.now()
            entry_time = pos.get("entry_time")
            if isinstance(entry_time, datetime):
                opened_at = entry_time.isoformat()
                hold_minutes = max(0.0, (now - entry_time).total_seconds() / 60)
            else:
                opened_at = now.isoformat()
                hold_minutes = None
            if action == "开仓":
                trade = {
                    "opened_at": opened_at,
                    "closed_at": None,
                    "side": side,
                    "amount_sol": amount,
                    "entry_price": price,
                    "signal_reason": meta.get("signal_reason", ""),
                    "mtf": meta.get("mtf"),
                    "cap_use": meta.get("cap_use"),
                    "ai_entry": meta.get("ai_entry"),
                }
            else:
                trade = {
                    "opened_at": opened_at,
                    "closed_at": now.isoformat(),
                    "side": side,
                    "amount_sol": amount,
                    "entry_price": pos.get("entry_price", 0) or 0,
                    "exit_price": price,
                    "gross_pnl": meta.get("gross_pnl"),
                    "fee_estimate": meta.get("fee_estimate"),
                    "net_pnl": meta.get("net_pnl", pnl),
                    "hold_minutes": hold_minutes,
                    "close_reason": meta.get("close_reason", ""),
                    "signal_reason": meta.get("signal_reason", ""),
                    "mtf": meta.get("mtf"),
                    "cap_use": meta.get("cap_use"),
                    "ai_entry": meta.get("ai_entry"),
                    "ai_exit": meta.get("ai_exit"),
                    "ai_exit_checks": meta.get("ai_exit_checks", []),
                }
            self.db.save_trade(trade)
        except Exception as e:
            logger.warning(f"SQLite trade write skipped: {e}")

    def run(self, close_all: bool = False):
        logger.info("=" * 60)
        logger.info("SOLUSDT v8.3-sqlite | 5x Bollinger mean-reversion | margin trading | startup no-op")
        logger.info("=" * 60)
        for name, value in vars(self.features).items():
            logger.info(f"[CONFIG] {name}={value}")

        self._startup_report()
        self._check_margin_funds()
        self._sync_position()

        # --close-all closes the current position then exits.
        if close_all and self.position:
            logger.info("--- close-all mode: closing current position ---")
            ticker = self.api.fetch_ticker()
            self._close(ticker["last"], "manual close")
            logger.info("position closed; exiting")
            return
        elif close_all:
            logger.info("no position to close")
            return

        if self.position:
            logger.warning(f"existing position detected ({self.position['side']} {self.position['amount']} SOL); strategy will manage it")
            logger.warning("entry price may be synced from current market price; PnL may be approximate")

        last_ts = 0
        logger.info(f"Kline polling interval: {KLINE_POLL_SECONDS}s")
        while self.running:
            try:
                klines = self.api.fetch_klines(limit=100)
                cur_ts = klines["timestamp"][-1]
                if cur_ts == last_ts:
                    time.sleep(KLINE_POLL_SECONDS)
                    continue
                last_ts = cur_ts
                self._tick(klines)
                time.sleep(KLINE_POLL_SECONDS)
            except KeyboardInterrupt:
                self.running = False
            except ccxt.NetworkError as e:
                logger.error(f"缃戠粶: {e}")
                time.sleep(20)
            except ccxt.RateLimitExceeded as e:
                logger.error(f"闄愰: {e}")
                time.sleep(45)
            except Exception as e:
                logger.error(f"{e}\n{traceback.format_exc()}")
                time.sleep(20)
        logger.info("stopped")

    def _startup_report(self):
        s = self.api.get_all_snapshot()
        total = sum(s.values())
        logger.info("--- Account USDT balances ---")
        logger.info(f"  funding: {s['funding']:.2f}")
        logger.info(f"  spot:    {s['spot']:.2f}")
        logger.info(f"  margin:  {s['margin']:.2f}  -> trading account")
        logger.info(f"  futures: {s['futures']:.2f}")
        logger.info(f"  total:   {total:.2f}")
        logger.info(f"  trading available (margin): {s['margin']:.2f}")

    def _check_margin_funds(self):
        """Check whether margin account has enough USDT."""
        margin_u = self.api.get_margin_usdt()
        if margin_u < 5:
            logger.warning("=" * 50)
            logger.warning(f"margin USDT available balance is too low: ${margin_u:.2f}")
            logger.warning("Please manually transfer funds into the Binance margin account:")
            logger.warning("  Binance App -> Wallet -> Funding -> Transfer -> Margin")
            logger.warning("  Binance App -> Wallet -> Spot -> Transfer -> Margin")
            logger.warning("  Binance App -> Futures -> Transfer -> Margin")
            logger.warning("=" * 50)

    def _startup_test_trade(self):
        """Run a tiny startup test trade with margin balance."""
        existing_sol = self.api.get_margin_sol()
        if existing_sol > 0.001:
            logger.info(f"clear residual SOL position: {existing_sol} SOL")
            try:
                self.api._margin_order("SELL", existing_sol)
                time.sleep(2)
            except Exception as e:
                logger.warning(f"clear residual SOL failed: {e}")

        free = self.api.get_margin_usdt()
        logger.info(f"test trade: margin available={free:.4f} USDT")
        if free < 5:
            logger.warning(f"Margin USDT too low (${free:.2f}); skip startup test trade")
            return

        ticker = self.api.fetch_ticker()
        cp = ticker["last"]

        # Binance margin minimum notional is about $10; test with up to $11 or 40% of balance.
        test_usdt = min(11.0, free * 0.4)
        test_amount = test_usdt / cp
        logger.info(f"test trade: test_usdt={test_usdt:.4f} cp={cp:.4f} raw_amount={test_amount:.6f}")
        test_amount = self.api.round_amount(test_amount)
        logger.info(f"test trade: rounded_amount={test_amount:.6f} (step={self.api.market['precision']['amount']})")

        if test_amount < 0.01:
            logger.warning(f"test trade amount is too small ({test_amount:.6f} < 0.01)")
            return

        logger.info("=" * 40)
        logger.info(f"*** test trade: buy {test_amount} SOL (~{test_usdt:.2f} USDT) ***")

        try:
            bo = self.api.margin_buy(test_amount)
            # Use the actual executed amount returned by Binance.
            actual_qty = float(bo.get("executedQty", bo.get("quantity", test_amount)))
            actual_qty = self.api.round_amount(actual_qty)
            logger.info(f"buy succeeded, executed {actual_qty} SOL; selling immediately")
            time.sleep(2)
            so = self.api.margin_sell(actual_qty)
            bf = self.api.extract_fill_price(bo, cp)
            sf = self.api.extract_fill_price(so, cp)
            pnl = (sf - bf) * test_amount
            logger.info(f"test completed PnL={pnl:.4f}U (buy {bf:.4f}, sell {sf:.4f})")
            logger.info("*** trade path is OK; entering strategy loop ***")
        except Exception as e:
            logger.error(f"test trade failed: {e}")

        logger.info("=" * 40)

    def _sync_position(self):
        sol = self.api.get_margin_sol()
        bor = self.api.get_margin_sol_borrowed()
        net_sol = sol - bor
        saved = self._read_position_file()

        def saved_matches(side: str, amount: float) -> bool:
            try:
                saved_side = str(saved.get("side", "")).lower()
                saved_amount = abs(float(saved.get("amount", 0) or 0))
                if saved_side != side or saved_amount <= 0:
                    return False
                return abs(saved_amount - amount) / max(amount, 0.001) <= 0.08
            except Exception:
                return False

        def saved_entry(default_price: float) -> Tuple[float, datetime]:
            try:
                price = float(saved.get("entry_price") or default_price)
            except Exception:
                price = default_price
            return price, self._parse_position_time(saved.get("entry_time"))

        def is_dust_position(amount: float, price: float = 0.0) -> bool:
            amount = abs(float(amount or 0))
            if amount <= 0:
                return True
            if amount < DUST_POSITION_SOL:
                return True
            return bool(price > 0 and amount * price < DUST_POSITION_USDT)

        cp = 0.0
        if abs(net_sol) > 0:
            try:
                cp = float(self.api.fetch_ticker()["last"])
            except Exception:
                cp = 0.0

        if is_dust_position(net_sol, cp):
            if abs(net_sol) > 0:
                value = abs(net_sol) * cp if cp > 0 else 0.0
                logger.info(f"ignore dust position: net_sol={net_sol:.6f} SOL value={value:.4f}U")
                try:
                    self.api.repay_available_debts()
                except Exception as e:
                    logger.warning(f"repay dust debt skipped: {e}")
            self.position = None
            self._write_position_file(current_price=fill, margin_level=margin_level, equity=margin_equity)
            return

        if net_sol > 0:
            if cp <= 0:
                cp = self.api.fetch_ticker()["last"]
            entry_price, entry_time = saved_entry(cp) if saved_matches("long", net_sol) else (cp, datetime.now())
            self.position = {"side": "long", "amount": net_sol,
                             "entry_price": entry_price, "entry_time": entry_time}
            if saved_matches("long", net_sol):
                for key in ("ai_entry", "ai_exit_plan", "ai_exit_checks", "last_ai_review_time"):
                    if saved.get(key) is not None:
                        self.position[key] = saved.get(key)
            try:
                self.highest = max(float(saved.get("highest", entry_price) or entry_price), cp, entry_price)
            except Exception:
                self.highest = max(cp, entry_price)
            self.lowest = entry_price
            self._write_position_file()
            logger.info(f"existing position: long {net_sol} SOL entry={entry_price:.4f} high={self.highest:.4f} (total={sol}, borrowed={bor})")
        elif net_sol < 0:
            if cp <= 0:
                cp = self.api.fetch_ticker()["last"]
            amount = abs(net_sol)
            entry_price, entry_time = saved_entry(cp) if saved_matches("short", amount) else (cp, datetime.now())
            self.position = {"side": "short", "amount": abs(net_sol),
                             "entry_price": entry_price, "entry_time": entry_time}
            if saved_matches("short", amount):
                for key in ("ai_entry", "ai_exit_plan", "ai_exit_checks", "last_ai_review_time"):
                    if saved.get(key) is not None:
                        self.position[key] = saved.get(key)
            try:
                self.lowest = min(float(saved.get("lowest", entry_price) or entry_price), cp, entry_price)
            except Exception:
                self.lowest = min(cp, entry_price)
            self.highest = entry_price
            self._write_position_file()
            logger.info(f"existing position: short {abs(net_sol)} SOL entry={entry_price:.4f} low={self.lowest:.4f} (total={sol}, borrowed={bor})")
        else:
            self.position = None
            self._write_position_file()

    def _tick(self, klines: dict):
        close = klines["close"]
        cp = close[-1]
        ind = self.strategy.compute(klines)

        # Position sizing uses margin account equity only.
        margin_equity = self.api.get_margin_equity()
        margin_level = self.api.get_margin_level()
        cfg = get_cfg(margin_equity)

        mtf_context = {}
        if self.features.multi_timeframe_filter or self.features.multi_timeframe_ai_context:
            try:
                mtf_klines = self.api.fetch_klines_multi(limit=100)
                mtf_context = self.strategy.mtf_trend(mtf_klines.get(MTF_15M), mtf_klines.get(MTF_1H))
                logger.info(
                    f"[{datetime.now().strftime('%H:%M:%S')}] SOL={cp:.4f} | "
                    f"equity={margin_equity:.2f} | margin_level={margin_level:.0f}% | "
                    f"RSI={self.strategy.last(ind['rsi']):.0f} | "
                    f"MTF cons={mtf_context.get('consensus', '-')}"
                )
            except Exception as e:
                logger.warning(f"MTF failed, fallback to single-TF: {e}")
                mtf_context = {}
        else:
            logger.info(
                f"[{datetime.now().strftime('%H:%M:%S')}] SOL={cp:.4f} | "
                f"equity={margin_equity:.2f} | margin_level={margin_level:.0f}% | "
                f"RSI={self.strategy.last(ind['rsi']):.0f}"
            )

        if self.position:
            self._update_extremes(cp)
            self._check_exit(ind, cp, klines)
            return

        if len(close) >= 2:
            gap = abs(cp - close[-2]) / close[-2]
            if gap > 0.02:
                logger.warning(f"price gap {gap*100:.1f}%")
                return

        if self.cooldown_until:
            now = datetime.now()
            if now < self.cooldown_until:
                left = int((self.cooldown_until - now).total_seconds() // 60) + 1
                logger.info(f"post-trade cooldown: {left}min")
                return
            self.cooldown_until = None

        ok, reason = self.risk.can_trade(margin_equity)
        if not ok:
            logger.info(f"risk block: {reason}")
            return

        strict_level = self._fake_breakout_level()
        if strict_level:
            logger.info(f"signal strict mode enabled after recent AI skips: level={strict_level}")

        lo, lm = self.strategy.check_long(ind, klines, strict_level, mtf_context)
        if lo:
            logger.info(f"signal: {lm}")
            cfg_for_ai = cfg
            if self.features.multi_timeframe_filter and mtf_context:
                if mtf_context.get("consensus") == "bear":
                    logger.info(f"MTF拒做多: {lm} (大周期偏空)")
                    self._log_trade("MTF拒做多", "long", 0, cp, signal_reason=lm, mtf=mtf_context)
                    return
                if mtf_context.get("consensus") == "neutral":
                    cfg_for_ai = self._mtf_degraded_cfg(cfg)
                    logger.info(f"MTF降级做多: cap_use={cfg_for_ai['cap_use']:.2f} (大周期震荡)")
            approved, ai = self.advisor.advise("long", lm, cp, klines, ind, margin_equity, margin_level, cfg_for_ai, self.risk, mtf_context)
            if not approved:
                logger.info(f"DeepSeek skipped long: {ai.get('reason', '')}")
                self._log_trade("AI拒绝开仓", "long", 0, cp, ai_entry=ai, signal_reason=lm, mtf=mtf_context)
                self._record_ai_skip("long", ai.get("reason", ""))
                return
            self._recent_ai_skips = []
            cfg = {**cfg_for_ai, "cap_use": max(MIN_AI_CAP_USE, min(self.advisor.effective_max_cap_use(cfg_for_ai), float(ai.get("cap_use", cfg_for_ai["cap_use"]) or cfg_for_ai["cap_use"])))}
            self._open("long", cp, margin_equity, cfg, ai_entry=ai, ind=ind)
            return

        so, sm = self.strategy.check_short(ind, klines, strict_level, mtf_context)
        if so:
            logger.info(f"signal: {sm}")
            cfg_for_ai = cfg
            if self.features.multi_timeframe_filter and mtf_context:
                if mtf_context.get("consensus") == "bull":
                    logger.info(f"MTF拒做空: {sm} (大周期偏多)")
                    self._log_trade("MTF拒做空", "short", 0, cp, signal_reason=sm, mtf=mtf_context)
                    return
                if mtf_context.get("consensus") == "neutral":
                    cfg_for_ai = self._mtf_degraded_cfg(cfg)
                    logger.info(f"MTF降级做空: cap_use={cfg_for_ai['cap_use']:.2f} (大周期震荡)")
            approved, ai = self.advisor.advise("short", sm, cp, klines, ind, margin_equity, margin_level, cfg_for_ai, self.risk, mtf_context)
            if not approved:
                logger.info(f"DeepSeek skipped short: {ai.get('reason', '')}")
                self._log_trade("AI拒绝开仓", "short", 0, cp, ai_entry=ai, signal_reason=sm, mtf=mtf_context)
                self._record_ai_skip("short", ai.get("reason", ""))
                return
            self._recent_ai_skips = []
            cfg = {**cfg_for_ai, "cap_use": max(MIN_AI_CAP_USE, min(self.advisor.effective_max_cap_use(cfg_for_ai), float(ai.get("cap_use", cfg_for_ai["cap_use"]) or cfg_for_ai["cap_use"])))}
            self._open("short", cp, margin_equity, cfg, ai_entry=ai, ind=ind)

    def _update_extremes(self, cp: float):
        if not self.position:
            return
        changed = False
        if self.position["side"] == "long" and cp > self.highest:
            self.highest = cp
            changed = True
        elif self.position["side"] == "short" and cp < self.lowest:
            self.lowest = cp
            changed = True
        if changed:
            self._write_position_file(current_price=cp)

    @staticmethod
    def _parse_optional_time(value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        text = str(value)
        try:
            return datetime.fromisoformat(text)
        except Exception:
            pass
        try:
            return datetime.strptime(f"{datetime.now().year}/{text}", "%Y/%m/%d %H:%M:%S")
        except Exception:
            return None

    def _record_ai_skip(self, side: str, reason: str):
        self._recent_ai_skips.append({
            "time": datetime.now(),
            "side": side,
            "reason": str(reason)[:120],
        })
        cutoff = datetime.now() - timedelta(hours=2)
        self._recent_ai_skips = [x for x in self._recent_ai_skips if x["time"] >= cutoff][-10:]

    def _fake_breakout_level(self) -> int:
        if not self.features.signal_fake_breakout_gate:
            return 0
        recent = self._recent_ai_skips[-FAKE_BREAKOUT_LOOKBACK:]
        if len(recent) >= FAKE_BREAKOUT_LOOKBACK:
            return 1
        return 0

    @staticmethod
    def _mtf_degraded_cfg(cfg: dict) -> dict:
        cap = max(MIN_AI_CAP_USE, float(cfg.get("cap_use", BASE_CAP_USE) or BASE_CAP_USE) * 0.5)
        return {**cfg, "cap_use": cap, "max_cap_use": cap}

    def _multi_timeframe_context(self) -> dict:
        if not (self.features.multi_timeframe_filter or self.features.multi_timeframe_ai_context):
            return {}
        try:
            mtf_klines = self.api.fetch_klines_multi(limit=100)
            return self.strategy.mtf_trend(mtf_klines.get(MTF_15M), mtf_klines.get(MTF_1H))
        except Exception as e:
            logger.warning(f"multi timeframe context failed: {e}")
            return {}

    def _build_exit_plan(self, ind: dict, cp: float) -> dict:
        tp = TAKE_PROFIT_PCT
        sl = STOP_LOSS_PCT
        if self.features.dynamic_atr_exit:
            atr_v = self.strategy.last(ind["atr"])
            atr_pct = atr_v / cp if cp > 0 and not np.isnan(atr_v) else 0
            if atr_pct > 0:
                tp = max(MIN_TAKE_PROFIT_PCT, min(0.035, atr_pct * ATR_TAKE_PROFIT_MULT))
                sl = max(MIN_DYNAMIC_STOP_LOSS_PCT, min(HARD_MAX_LOSS_PCT, atr_pct * ATR_STOP_LOSS_MULT))
        return {
            "next_take_profit_pct": tp,
            "next_stop_loss_pct": sl,
            "reason": "initial ATR plan" if self.features.dynamic_atr_exit else "initial plan",
        }

    def _ai_review_due(self, pos: dict) -> Tuple[bool, str]:
        now = datetime.now()
        entry_time = self._parse_optional_time(pos.get("entry_time")) or now
        age_min = (now - entry_time).total_seconds() / 60
        if age_min < AI_REVIEW_AFTER_MIN:
            return False, ""

        last_review = self._parse_optional_time(pos.get("last_ai_review_time"))
        if not last_review:
            checks = pos.get("ai_exit_checks") or []
            for item in reversed(checks):
                last_review = self._parse_optional_time(item.get("time"))
                if last_review:
                    break
        if not last_review:
            return True, f"持仓{age_min:.0f}分钟AI复评"

        since_min = (now - last_review).total_seconds() / 60
        if since_min >= AI_REVIEW_INTERVAL_MIN:
            return True, f"AI持仓后{since_min:.0f}分钟复评"
        return False, ""

    def _check_exit(self, ind: dict, cp: float, klines: dict):
        pos = self.position
        pos["highest"] = self.highest
        pos["lowest"] = self.lowest
        entry = float(pos.get("entry_price", 0) or 0)
        side = pos.get("side")
        pnl_pct = 0.0
        if entry > 0:
            pnl_pct = (cp - entry) / entry if side == "long" else (entry - cp) / entry
        entry_time = self._parse_optional_time(pos.get("entry_time")) or datetime.now()
        hold_min = (datetime.now() - entry_time).total_seconds() / 60
        if pos["side"] == "long":
            ex, reason = self.strategy.check_long_exit(
                ind, pos["entry_price"], cp, self.highest, pos["entry_time"])
        else:
            ex, reason = self.strategy.check_short_exit(
                ind, pos["entry_price"], cp, self.lowest, pos["entry_time"])

        hard_exit = False
        plan = pos.get("ai_exit_plan") or {}
        next_tp = max(MIN_TAKE_PROFIT_PCT, min(0.035, float(plan.get("next_take_profit_pct", TAKE_PROFIT_PCT) or TAKE_PROFIT_PCT)))
        next_sl = max(MIN_DYNAMIC_STOP_LOSS_PCT, min(HARD_MAX_LOSS_PCT, float(plan.get("next_stop_loss_pct", STOP_LOSS_PCT) or STOP_LOSS_PCT)))
        dynamic_hit = pnl_pct >= next_tp or pnl_pct <= -next_sl
        if pnl_pct <= -HARD_MAX_LOSS_PCT:
            ex, reason, hard_exit = True, f"硬止损-{HARD_MAX_LOSS_PCT*100:.1f}%", True
        elif hold_min >= FORCE_EXIT_NEGATIVE_HOLD_MIN and pnl_pct < 0:
            ex, reason, hard_exit = True, (
                f"负收益超时强平({hold_min:.0f}min, {pnl_pct*100:.2f}%)"
            ), True
        elif plan and not dynamic_hit:
            ex = False

        # Dynamic margin floor protection: larger positions need a higher margin level.
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
                hard_exit = True

        if not ex:
            due, review_reason = self._ai_review_due(pos)
            if due:
                ex, reason = True, review_reason
                hard_exit = False

        if ex:
            if not hard_exit:
                margin_equity = self.api.get_margin_equity()
                margin_level = self.api.get_margin_level()
                mtf_context = self._multi_timeframe_context()
                ai_exit = self.advisor.advise_exit(pos, reason, cp, klines, ind, margin_equity, margin_level, self.risk, mtf_context)
                pos["last_ai_review_time"] = datetime.now().isoformat()
                checks = list(pos.get("ai_exit_checks") or [])
                checks.append({
                    "time": datetime.now().strftime("%m/%d %H:%M:%S"),
                    "trigger": reason,
                    "decision": ai_exit,
                })
                pos["ai_exit_checks"] = checks[-8:]
                if ai_exit.get("action") == "hold":
                    pos["ai_exit_plan"] = {
                        "next_take_profit_pct": ai_exit.get("next_take_profit_pct", next_tp),
                        "next_stop_loss_pct": ai_exit.get("next_stop_loss_pct", next_sl),
                        "reason": ai_exit.get("reason", ""),
                        "updated_at": datetime.now().isoformat(),
                    }
                    logger.info(f"DeepSeek继续持仓: {ai_exit.get('reason', '')}")
                    self._write_position_file()
                    return
                pos["ai_last_exit"] = ai_exit
                self._write_position_file()
                if ai_exit.get("action") == "reverse":
                    reverse_side = ai_exit.get("reverse_side")
                    if reverse_side in {"long", "short"} and reverse_side != side:
                        logger.info(f"DeepSeek反手: close {side}, open {reverse_side}")
                        closed = self._close(cp, reason)
                        if closed and not self.position:
                            next_cp = self.api.fetch_ticker()["last"]
                            next_equity = self.api.get_margin_equity()
                            next_cfg = get_cfg(next_equity)
                            cap = float(ai_exit.get("cap_use", next_cfg["cap_use"]) or next_cfg["cap_use"])
                            next_cfg = {**next_cfg, "cap_use": max(MIN_AI_CAP_USE, min(self.advisor.effective_max_cap_use(next_cfg), cap))}
                            ok, risk_reason = self.risk.can_trade(next_equity)
                            if ok:
                                self.cooldown_until = None
                                self._open(reverse_side, next_cp, next_equity, next_cfg, ai_entry={
                                    "action": "approve",
                                    "cap_use": next_cfg["cap_use"],
                                    "reason": f"AI平仓后反手: {ai_exit.get('reason', '')}",
                                    "source": "exit_reverse",
                                })
                            else:
                                logger.info(f"反手被风控阻止: {risk_reason}")
                        return
            logger.info(f"exit: {reason}")
            self._close(cp, reason)

    def _open_with_order_engine(self, side: str, cp: float, amount: float,
                                margin_equity: float, ind: dict = None) -> dict:
        if self.features.limit_ioc_orders and ind:
            try:
                if side == "long":
                    bb_lower = self.strategy.last(ind["bb_l"])
                    limit_price = float(bb_lower) * 1.001 if not np.isnan(bb_lower) else cp * 1.001
                    return self.api.margin_order_ioc("buy", amount, limit_price)
                bb_upper = self.strategy.last(ind["bb_u"])
                limit_price = float(bb_upper) * 0.999 if not np.isnan(bb_upper) else cp * 0.999
                return self.api.margin_order_ioc("sell", amount, limit_price)
            except Exception as e:
                logger.warning(f"IOC order failed, fallback to market: {e}")
        return self.api.margin_buy(amount) if side == "long" else self.api.margin_sell(amount)

    def _open(self, side: str, cp: float, _margin_eq: float, cfg: dict,
              ai_entry: dict = None, ind: dict = None):
        # Dynamic margin floor: larger planned positions require a higher margin level.
        margin_level = self.api.get_margin_level()
        free = self.api.get_margin_usdt()
        margin_equity = self.api.get_margin_equity()

        # Nominal position = margin equity * leverage * capital use.
        target_notional = margin_equity * cfg["leverage"] * cfg["cap_use"]
        planned_usage = target_notional / max(margin_equity, 1)
        if planned_usage < 0.3:
            MARGIN_MIN = 150
        elif planned_usage < 0.7:
            MARGIN_MIN = 138
        else:
            MARGIN_MIN = 130

        if margin_level < MARGIN_MIN:
            logger.warning(f"margin level {margin_level:.0f}% < floor {MARGIN_MIN}% (planned usage {planned_usage*100:.0f}%); skip opening")
            return

        trade_cap = target_notional

        # Reduce position proportionally when margin level is below 200%.
        if margin_level < 200:
            trade_cap *= margin_level / 200
            logger.info(f"margin level is low ({margin_level:.0f}%); reduce position to {trade_cap:.1f}U")

        if trade_cap < 3:
            logger.warning(f"margin equity is insufficient (equity={margin_equity:.2f}, target={trade_cap:.2f})")
            return

        amount = trade_cap / cp
        amount = self.api.round_amount(amount)
        if amount <= 0:
            return
        if amount * cp < 10:
            logger.warning(f"trade notional ${amount * cp:.2f} is below minimum 10; skip")
            return

        logger.info(
            f"open {side}: {amount}SOL @~{cp:.4f} | notional={trade_cap:.1f}U "
            f"({cfg['leverage']}x*{cfg['cap_use']*100:.0f}%) | free={free:.1f}U | margin_level={margin_level:.0f}%"
        )

        try:
            o = self._open_with_order_engine(side, cp, amount, margin_equity, ind)
            fill = self.api.extract_fill_price(o, cp)
            slippage = self.slippage.record(side, cp, fill)
            exit_plan = self._build_exit_plan(ind, fill) if ind else {
                "next_take_profit_pct": TAKE_PROFIT_PCT,
                "next_stop_loss_pct": STOP_LOSS_PCT,
                "reason": "initial plan",
            }
            self.position = {"side": side, "amount": amount,
                             "entry_price": fill, "entry_time": datetime.now(),
                             "ai_entry": ai_entry or {},
                             "ai_exit_plan": exit_plan,
                             "ai_exit_checks": [],
                             "last_ai_review_time": None}
            self.highest = fill
            self.lowest = fill
            self._write_position_file()
            self._log_trade("开仓", side, amount, fill, ai_entry=ai_entry, cap_use=cfg.get("cap_use"),
                            slippage=slippage, slippage_summary=self.slippage.to_dict())
            logger.info(f"open succeeded: {side} {amount} SOL @{fill:.4f}")
        except Exception as e:
            logger.error(f"open failed: {e}")

    def _close(self, cp: float, reason: str):
        pos = self.position
        if not pos:
            return False
        amount = self.api.round_amount(pos["amount"])
        entry = pos["entry_price"]
        side = pos["side"]
        try:
            o = self.api.close_long(amount) if side == "long" else self.api.close_short(amount)
            fill = self.api.extract_fill_price(o, cp)
            slip_side = "short" if side == "long" else "long"
            slippage = self.slippage.record(slip_side, cp, fill)
            gross_pnl = (fill - entry) * amount if side == "long" else (entry - fill) * amount
            if self.features.fee_net_pnl:
                pnl, fee_estimate = net_pnl_after_fee(gross_pnl, entry, fill, amount)
            else:
                pnl, fee_estimate = gross_pnl, 0.0

            # Repay any available debt after close; do not treat tiny leftover debt as a real short.
            self.api.repay_available_debts()

            logger.info(
                f"平仓: {side} {amount}SOL netPnL={pnl:.2f}U gross={gross_pnl:.2f}U "
                f"fee≈{fee_estimate:.4f}U ({pnl/(entry*amount)*100:.2f}%) {reason}"
            )
            self._log_trade(
                "平仓", side, amount, fill, pnl,
                close_reason=reason,
                ai_entry=pos.get("ai_entry"),
                ai_exit=pos.get("ai_last_exit"),
                ai_exit_checks=pos.get("ai_exit_checks", []),
                gross_pnl=round(gross_pnl, 4),
                fee_estimate=round(fee_estimate, 4),
                net_pnl=round(pnl, 4),
                slippage=slippage,
                slippage_summary=self.slippage.to_dict(),
            )
            self.risk.record(pnl)
            self.advisor.record_trade_result(pos.get("ai_entry"), pnl)
            cooldown = LOSS_COOLDOWN_MIN if pnl < 0 else POST_TRADE_COOLDOWN_MIN
            if cooldown > 0:
                self.cooldown_until = datetime.now() + timedelta(minutes=cooldown)
                logger.info(f"post-trade cooldown set: {cooldown}min")
            else:
                self.cooldown_until = None
                logger.info("post-trade cooldown disabled")
            self._sync_position()
            return True
        except Exception as e:
            logger.error(f"平仓失败: {e}")
            return False


def load_config(path: str = "config.json") -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    cfg.setdefault("api_key", os.environ.get("BINANCE_API_KEY", ""))
    cfg.setdefault("secret", os.environ.get("BINANCE_SECRET", ""))
    cfg.setdefault("proxy", os.environ.get("BINANCE_PROXY", os.environ.get("HTTPS_PROXY", "")))
    cfg.setdefault("cf_worker", os.environ.get("CF_WORKER_URL", ""))
    cfg.setdefault("deepseek_api_key", os.environ.get("DEEPSEEK_API_KEY", ""))
    cfg.setdefault("deepseek_model", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    cfg.setdefault("deepseek_enabled", os.environ.get("DEEPSEEK_ENABLED", "1") not in ("0", "false", "False"))
    return cfg


def resolve_cf_worker() -> str:
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--cf-worker" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--cf-worker="):
            return arg.split("=", 1)[1]
    return ""


def main():
    cfg = load_config()
    runtime = build_runtime_config(cfg)
    ak = cfg.get("api_key", "")
    sk = cfg.get("secret", "")
    proxy = cfg.get("proxy", "")
    cf_worker = resolve_cf_worker() or cfg.get("cf_worker", "")
    deepseek_api_key = cfg.get("deepseek_api_key", "")
    deepseek_model = cfg.get("deepseek_model", "deepseek-chat")
    deepseek_enabled = bool(cfg.get("deepseek_enabled", True))
    if not ak or not sk:
        print("Please configure API keys")
        return

    close_all = "--close-all" in sys.argv
    if close_all:
        print("mode: close all positions then exit")

    TradingBot(ak, sk, proxy, cf_worker, deepseek_api_key, deepseek_model, deepseek_enabled, runtime).run(close_all=close_all)


if __name__ == "__main__":
    try:
        import ccxt, numpy
    except ImportError:
        print("pip install ccxt numpy")
        sys.exit(1)
    main()
