"""
SOLUSDT monitor data fetcher.
"""

import os, json, time, logging, threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import ccxt
import requests
from storage import Storage
try:
    import ipaddress
    import ip2region.searcher as ip2region_searcher
    import ip2region.util as ip2region_util
except Exception:
    ipaddress = None
    ip2region_searcher = None
    ip2region_util = None

logger = logging.getLogger("Monitor")

SYMBOL = "SOL/USDT"
DASHBOARD_REFRESH_SECONDS = 20
ACCESS_LOG_LIMIT = 500
DUST_POSITION_SOL = 0.005
DUST_POSITION_USDT = 3.0
META_KEYS = {"info", "free", "used", "total", "timestamp", "datetime",
             "debt", "borrowed", "interest", "net", "currency", "free_margin",
             "used_margin", "equity", "unrealized_pnl", "margin_ratio", "position"}
CST = timezone(timedelta(hours=8))


def now_cst() -> datetime:
    return datetime.now(CST)


class GeoResolver:
    def __init__(self, db_path: str = ""):
        self.db_path = db_path
        self.searcher = None
        if not db_path or not ip2region_searcher or not ip2region_util:
            return
        try:
            if os.path.exists(db_path):
                content = ip2region_util.load_content_from_file(db_path)
                self.searcher = ip2region_searcher.new_with_buffer(ip2region_util.IPv4, content)
                logger.info(f"IP城市库已加载: {db_path}")
            else:
                logger.warning(f"IP城市库不存在: {db_path}")
        except Exception as e:
            logger.warning(f"IP城市库加载失败: {e}")

    def lookup(self, ip: str) -> str:
        ip = (ip or "").strip()
        if not ip or ip == "-":
            return "未知"
        if ipaddress:
            try:
                obj = ipaddress.ip_address(ip)
                if obj.is_private or obj.is_loopback or obj.is_link_local:
                    return "内网"
                if obj.version != 4:
                    return "未知"
            except Exception:
                return "未知"
        if not self.searcher:
            return "未知"
        try:
            region = self.searcher.search(ip) or ""
            parts = [p for p in region.split("|") if p and p != "0"]
            if not parts:
                return "未知"
            if len(parts) >= 3:
                country, province, city = parts[0], parts[1], parts[2]
                if country == "中国":
                    return " ".join(dict.fromkeys([province, city]))
                return " ".join(dict.fromkeys([country, province, city]))
            return " ".join(parts[:3])
        except Exception:
            return "未知"


class DataFetcher:
    MOJIBAKE_REPLACEMENTS = {
        "骞充粨": "平仓",
        "寮€浠?": "开仓",
        "寮€浠�": "开仓",
        "鏇存柊": "更新",
        "鍋氱┖": "做空",
        "鍋氬": "做多",
        "鐩堜簭": "盈亏",
        "蹇鐩?": "快速止盈",
        "蹇鐩�": "快速止盈",
        "鐩堝埄鍔ㄨ兘杞急": "盈利动能转弱",
        "鐩堝埄鍔ㄨ兘杞\ue100急": "盈利动能转弱",
        "鐩堝埄鍥炴挙淇濇姢": "盈利回撤保护",
        "淇濇湰姝㈢泩": "保本止盈",
        "姝㈡崯": "止损",
        "鏃堕棿姝㈡崯": "时间止损",
        "ATR姝㈡崯": "ATR止损",
        "RSI瓒呬拱": "RSI超买",
        "RSI瓒呭崠": "RSI超卖",
        "BB鍥炲綊": "BB回归",
        "鍥炲綊": "回归",
        "淇″彿": "信号",
        "浠撲綅": "仓位",
    }
    MOJIBAKE_MARKERS = "鏇鐩鐭鍋寮骞粨浠蹇鍥炲綊淇彿"

    @staticmethod
    def _cfg_bool(config: dict, key: str, default: bool = False) -> bool:
        flags = (config or {}).get("feature_flags") or {}
        value = flags.get(key, (config or {}).get(key, default))
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)

    @staticmethod
    def _cfg_int(config: dict, key: str, default: int) -> int:
        flags = (config or {}).get("feature_flags") or {}
        try:
            return int(flags.get(key, (config or {}).get(key, default)))
        except Exception:
            return int(default)

    def __init__(self, api_key: str, secret: str, proxy: str = "", config: dict = None, cf_worker: str = ""):
        base = {"apiKey": api_key, "secret": secret, "enableRateLimit": True, "timeout": 30000}
        self.proxy = proxy
        if proxy and not cf_worker:
            base["proxies"] = {"http": proxy, "https": proxy}
            logger.info(f"使用代理: {proxy}")

        self.spot = ccxt.binance({**base, "options": {"defaultType": "spot"}})
        self.margin = ccxt.binance({**base, "options": {"defaultType": "margin"}})
        self.futures = ccxt.binance({**base, "options": {"defaultType": "future"}})

        if cf_worker:
            cf_worker = cf_worker.rstrip("/")
            for ex in [self.spot, self.margin, self.futures]:
                for key in list(ex.urls.get("api", {}).keys()):
                    old = ex.urls["api"][key]
                    parsed = urlparse(old)
                    ex.urls["api"][key] = cf_worker + parsed.path
            logger.info(f"使用 CF Worker 中继: {cf_worker}")
        self._market_clients = [("spot", self.spot), ("margin", self.margin), ("futures", self.futures)]
        self._market_load_attempts = {}
        self._market_load_errors = {}
        self._load_markets_once(force=True)
        self._cache = {}
        self._lock = threading.Lock()

        self._cny_rate = float(config.get("usd_cny_rate", 7.25) if config else 7.25)
        self._initial_equity_cny = float(config.get("initial_equity_cny", 0) if config else 0)
        self._initial_equity = float(config.get("initial_equity", 0) if config else 0)
        self._deepseek_api_key = str(config.get("deepseek_api_key", "") if config else "").strip()
        self._deepseek_model = str(config.get("deepseek_model", "deepseek-chat") if config else "deepseek-chat")
        self._deepseek_enabled = bool(config.get("deepseek_enabled", True) if config else True)
        self._history_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
        self._position_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "position.json")
        self._trade_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.json")
        self._sentiment_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_sentiment.json")
        self._sentiment_cache = self._load_sentiment()
        self._last_snapshot_hour = None
        self._sqlite_storage = self._cfg_bool(config, "sqlite_storage", False)
        self._sqlite_read_enabled = self._cfg_bool(config, "sqlite_read_enabled", False)
        self._db = Storage(
            enabled=self._sqlite_storage or self._sqlite_read_enabled,
            keep_days=self._cfg_int(config, "db_keep_days", 90),
        )
        self._load_history()

    def _load_markets_once(self, force: bool = False) -> bool:
        ready = True
        now = time.time()
        for name, ex in self._market_clients:
            if getattr(ex, "markets", None):
                continue
            last_attempt = self._market_load_attempts.get(name, 0)
            if not force and now - last_attempt < 60:
                ready = False
                continue
            self._market_load_attempts[name] = now
            try:
                ex.load_markets()
                self._market_load_errors.pop(name, None)
                logger.info(f"{name} markets loaded")
            except Exception as e:
                ready = False
                self._market_load_errors[name] = str(e)
                logger.warning(f"{name} markets unavailable, will retry: {e}")
        return ready

    def _fetch_cny_rate(self):
        try:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
            r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=8, proxies=proxies)
            self._cny_rate = float(r.json()["rates"]["CNY"])
        except Exception:
            pass

    @staticmethod
    def _clean_history_points(raw) -> list:
        clean = []
        if not isinstance(raw, list):
            return clean
        for h in raw:
            try:
                ts = int(h.get("t", 0))
                value = float(h.get("v", 0))
            except Exception:
                continue
            if ts > 0 and value > 0:
                clean.append({"t": ts, "v": round(value, 2)})
        return clean

    def _load_history(self):
        try:
            if os.path.exists(self._history_file):
                with open(self._history_file, encoding="utf-8") as f:
                    raw = json.load(f)
            else:
                raw = []
        except Exception:
            raw = []
        self._history = self._clean_history_points(raw)

    def _save_snapshot(self, equity: float):
        try:
            equity = float(equity)
        except Exception:
            return
        if equity <= 0:
            return

        now = now_cst()
        hour_key = now.strftime("%Y%m%d%H")
        if hour_key == self._last_snapshot_hour:
            return
        self._last_snapshot_hour = hour_key

        cutoff_ms = int((now - timedelta(days=3)).timestamp() * 1000)
        self._history = self._clean_history_points([h for h in self._history if h["t"] > cutoff_ms])

        def same_cst_hour(point):
            try:
                dt = datetime.fromtimestamp(point["t"] / 1000, CST)
                return dt.strftime("%Y%m%d%H") == hour_key
            except Exception:
                return False

        if not any(same_cst_hour(h) for h in self._history):
            self._history.append({"t": int(now.timestamp() * 1000), "v": round(equity, 2)})
        try:
            with open(self._history_file, "w", encoding="utf-8") as f:
                json.dump(self._history, f)
        except Exception:
            pass
        if self._db.enabled:
            self._db.save_equity_snapshot(equity)

    def _load_sentiment(self) -> dict:
        try:
            if os.path.exists(self._sentiment_file):
                with open(self._sentiment_file, encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _save_sentiment(self, data: dict):
        try:
            with open(self._sentiment_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def _hour_key(dt: datetime = None) -> str:
        dt = dt or now_cst()
        return dt.strftime("%Y%m%d%H")

    def _fallback_sentiment(self, sol_price: float, sol_t: dict, price_history: list, reason: str = "") -> dict:
        closes = [float(x.get("c", 0) or 0) for x in price_history if x.get("c")]
        change = float(sol_t.get("percentage", 0) or 0)
        short_change = ((closes[-1] - closes[0]) / closes[0] * 100) if len(closes) >= 2 and closes[0] else 0
        if change > 1.0 and short_change >= 0:
            mood = "偏多"
            summary = "SOL 现在整体偏强，短线价格没有明显走坏。"
            outlook = "如果价格继续站稳短周期均线，上方还有试探空间；但追高容易被回撤洗出去。"
        elif change < -1.0 and short_change <= 0:
            mood = "偏空"
            summary = "SOL 现在整体偏弱，反弹力度还不够稳定。"
            outlook = "如果跌破最近低点，短线可能继续向下找支撑；做多需要等更明确的止跌信号。"
        else:
            mood = "震荡"
            summary = "SOL 现在更像横盘震荡，方向还不够干净。"
            outlook = "短线更适合等价格靠近区间边缘后再判断，不适合在中间位置重仓追。"
        now = now_cst()
        refresh_time = now.replace(minute=0, second=0, microsecond=0)
        return {
            "hour_key": self._hour_key(now),
            "refresh_time": refresh_time.strftime("%m/%d %H:00"),
            "generated_at": now.strftime("%H:%M:%S"),
            "mood": mood,
            "summary": summary,
            "outlook": outlook,
            "risk": "小资金高杠杆波动会被放大，突然拉升或急跌时要优先看保证金率。",
            "action_hint": "把它当作辅助判断，不要替代止损和仓位控制。",
            "source": "本地回退" if reason else "本地计算",
        }

    def _market_sentiment(self, sol_price: float, sol_t: dict, price_history: list,
                          position_info: dict, margin_level: float) -> dict:
        now = now_cst()
        hour_key = self._hour_key(now)
        cached = self._sentiment_cache if isinstance(self._sentiment_cache, dict) else {}
        if cached.get("hour_key") == hour_key:
            return cached

        if not (self._deepseek_enabled and self._deepseek_api_key):
            data = self._fallback_sentiment(sol_price, sol_t, price_history, "DeepSeek disabled")
            self._sentiment_cache = data
            self._save_sentiment(data)
            return data

        candles = []
        for item in price_history[-24:]:
            candles.append({
                "t": item.get("t"),
                "o": round(float(item.get("o", 0) or 0), 3),
                "h": round(float(item.get("h", 0) or 0), 3),
                "l": round(float(item.get("l", 0) or 0), 3),
                "c": round(float(item.get("c", 0) or 0), 3),
            })
        payload = {
            "task": "solusdt_market_sentiment",
            "timezone": "UTC+8",
            "refresh_hour": now.replace(minute=0, second=0, microsecond=0).isoformat(),
            "price": round(float(sol_price), 4),
            "change_24h_pct": round(float(sol_t.get("percentage", 0) or 0), 3),
            "recent_5m_candles": candles,
            "position": position_info or {},
            "margin_level_pct": margin_level,
            "instruction": "用中文，直白通俗，不要术语堆砌。分析 SOLUSDT 接下来几小时偏多、偏空还是震荡，说明原因和主要风险。只返回 JSON。",
            "return_schema": {
                "mood": "偏多|偏空|震荡",
                "summary": "一句话说明当前情绪",
                "outlook": "对接下来几小时的通俗判断",
                "risk": "最需要注意的风险",
                "action_hint": "一句话操作提醒，不要承诺收益",
            },
        }
        body = {
            "model": self._deepseek_model,
            "temperature": 0.2,
            "max_tokens": 320,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a concise SOLUSDT market analyst. Return JSON only. Chinese only."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
            ],
        }
        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {self._deepseek_api_key}", "Content-Type": "application/json"},
                data=json.dumps(body).encode("utf-8"),
                timeout=12,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            decision = json.loads(content)
            refresh_time = now.replace(minute=0, second=0, microsecond=0)
            data = {
                "hour_key": hour_key,
                "refresh_time": refresh_time.strftime("%m/%d %H:00"),
                "generated_at": now.strftime("%H:%M:%S"),
                "mood": str(decision.get("mood", "震荡"))[:12],
                "summary": str(decision.get("summary", ""))[:120],
                "outlook": str(decision.get("outlook", ""))[:220],
                "risk": str(decision.get("risk", ""))[:160],
                "action_hint": str(decision.get("action_hint", ""))[:160],
                "source": "DeepSeek",
            }
        except Exception as e:
            logger.warning(f"市场情绪生成失败: {e}")
            data = self._fallback_sentiment(sol_price, sol_t, price_history, str(e))
        self._sentiment_cache = data
        self._save_sentiment(data)
        return data

    def _read_position_file(self) -> dict:
        # Read the position snapshot written by the bot.
        try:
            if os.path.exists(self._position_file):
                with open(self._position_file, encoding="utf-8") as f:
                    return self._repair_text(json.load(f))
        except Exception:
            pass
        return {}

    def _read_trades_file(self) -> list:
        try:
            if os.path.exists(self._trade_file):
                with open(self._trade_file, encoding="utf-8") as f:
                    return self._repair_text(json.load(f))
        except Exception:
            pass
        return []

    @classmethod
    def _repair_text(cls, value):
        if isinstance(value, dict):
            return {k: cls._repair_text(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._repair_text(v) for v in value]
        if not isinstance(value, str):
            return value

        text = value
        for bad, good in cls.MOJIBAKE_REPLACEMENTS.items():
            text = text.replace(bad, good)

        if any(ch in text for ch in cls.MOJIBAKE_MARKERS):
            try:
                candidate = text.encode("gbk").decode("utf-8")
                if cls._mojibake_score(candidate) < cls._mojibake_score(text):
                    text = candidate
            except Exception:
                pass
        return text

    @classmethod
    def _mojibake_score(cls, text: str) -> int:
        return sum(text.count(ch) for ch in cls.MOJIBAKE_MARKERS) + text.count("�") * 3

    @staticmethod
    def _parse_trade_time(value, now=None):
        if not value:
            return None
        now = now or now_cst()
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts, CST)

        s = str(value).strip()
        normalized = s.replace("-", "/")
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%dT%H:%M:%S"):
            try:
                return datetime.strptime(normalized.replace("T", " "), "%Y/%m/%d %H:%M:%S").replace(tzinfo=CST)
            except Exception:
                pass
        for fmt in ("%m/%d %H:%M:%S",):
            try:
                dt = datetime.strptime(f"{now.year}/{normalized}", "%Y/%m/%d %H:%M:%S").replace(tzinfo=CST)
                if dt > now + timedelta(hours=12):
                    dt = dt.replace(year=dt.year - 1)
                return dt
            except Exception:
                pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST)
            return dt.astimezone(CST)
        except Exception:
            return None

    @classmethod
    def _today_trade_stats(cls, trade_history: list) -> dict:
        today = now_cst().date()
        count = 0
        pnl = 0.0
        for trade in trade_history if isinstance(trade_history, list) else []:
            if str(trade.get("action", "")) != "平仓":
                continue
            dt = cls._parse_trade_time(trade.get("time") if isinstance(trade, dict) else None)
            if not dt or dt.date() != today:
                continue
            amount = cls._to_float(trade.get("amount"), 0) or 0
            if cls._is_dust_position(amount):
                continue
            count += 1
            try:
                if trade.get("pnl") is not None:
                    pnl += float(trade.get("pnl") or 0)
            except Exception:
                pass
        return {"count": count, "pnl": round(pnl, 4)}

    @staticmethod
    def _to_float(value, default=None):
        try:
            if value is None or value == "":
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _is_dust_position(amount, price=0) -> bool:
        try:
            amount = abs(float(amount or 0))
            price = float(price or 0)
        except Exception:
            return True
        if amount <= 0:
            return True
        if amount < DUST_POSITION_SOL:
            return True
        return bool(price > 0 and amount * price < DUST_POSITION_USDT)

    @staticmethod
    def _derive_slippage(position_info: dict, trade_history: list) -> dict:
        default = {"avg": 0.0, "max": 0.0, "count": 0, "recent": []}
        if isinstance(position_info, dict) and isinstance(position_info.get("slippage"), dict):
            return {**default, **position_info.get("slippage")}
        for trade in reversed(trade_history if isinstance(trade_history, list) else []):
            if not isinstance(trade, dict):
                continue
            summary = trade.get("slippage_summary")
            if isinstance(summary, dict):
                return {**default, **summary}
        return default

    def _db_stats(self) -> dict:
        default = {
            "enabled": bool(self._db.enabled),
            "read_enabled": bool(self._sqlite_read_enabled),
            "trade_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_net_pnl": 0.0,
            "avg_hold_min": 0.0,
            "ai_stats": {
                "approve_win_rate": 0.0,
                "total_calls": 0,
                "avg_response_ms": 0,
            },
        }
        if not self._db.enabled:
            return default
        try:
            stats = self._db.get_trade_stats()
            ai_stats = self._db.get_ai_quality()
            return {
                **default,
                "trade_count": stats.get("total_trades", 0),
                "win_rate": stats.get("win_rate", 0.0),
                "profit_factor": stats.get("profit_factor", 0.0),
                "total_net_pnl": stats.get("total_net_pnl", 0.0),
                "avg_hold_min": stats.get("avg_hold_min", 0.0),
                "ai_stats": {
                    **default["ai_stats"],
                    **ai_stats,
                },
            }
        except Exception as e:
            logger.warning(f"SQLite stats unavailable: {e}")
            return default

    @staticmethod
    def _side_label(side: str) -> str:
        return "做多" if side == "long" else "做空" if side == "short" else "-"

    @classmethod
    def _trade_time_label(cls, value) -> str:
        dt = cls._parse_trade_time(value)
        if dt:
            return dt.strftime("%m/%d %H:%M")
        return str(value) if value else "-"

    @classmethod
    def _build_trade_records(cls, trade_history: list, position_info: dict, current_price: float) -> dict:
        open_trades = []
        completed = []
        signals = []
        for trade in trade_history if isinstance(trade_history, list) else []:
            if not isinstance(trade, dict):
                continue
            action = str(trade.get("action", ""))
            side = str(trade.get("side", "")).lower()
            if action == "AI拒绝开仓":
                signals.append({
                    "time": cls._trade_time_label(trade.get("time")),
                    "side": side,
                    "side_label": cls._side_label(side),
                    "amount": 0,
                    "price": cls._to_float(trade.get("price")),
                    "signal_reason": trade.get("signal_reason"),
                    "ai_entry": trade.get("ai_entry"),
                })
                continue
            if action == "开仓":
                open_trades.append(trade)
                continue
            if action != "平仓":
                continue

            close_amount = cls._to_float(trade.get("amount"), 0) or 0
            match_idx = None
            if close_amount > 0:
                for idx in range(len(open_trades) - 1, -1, -1):
                    if str(open_trades[idx].get("side", "")).lower() != side:
                        continue
                    open_amount = cls._to_float(open_trades[idx].get("amount"), 0) or 0
                    if abs(open_amount - close_amount) < 0.002:
                        match_idx = idx
                        break
            for idx in range(len(open_trades) - 1, -1, -1):
                if match_idx is not None:
                    break
                if str(open_trades[idx].get("side", "")).lower() == side:
                    match_idx = idx
                    break
            open_trade = open_trades.pop(match_idx) if match_idx is not None else None

            open_price = cls._to_float(open_trade.get("price") if open_trade else None)
            close_price = cls._to_float(trade.get("price"))
            amount = cls._to_float(trade.get("amount"), cls._to_float(open_trade.get("amount") if open_trade else None, 0)) or 0
            buy_price = open_price if side == "long" else close_price
            sell_price = close_price if side == "long" else open_price
            pnl = cls._to_float(trade.get("pnl"))
            if pnl is None and buy_price is not None and sell_price is not None and amount > 0:
                pnl = (sell_price - buy_price) * amount
            if cls._is_dust_position(amount, current_price):
                continue

            completed.append({
                "start_time": cls._trade_time_label(open_trade.get("time") if open_trade else None),
                "end_time": cls._trade_time_label(trade.get("time")),
                "side": side,
                "side_label": cls._side_label(side),
                "amount": round(amount, 6),
                "buy_price": buy_price,
                "sell_price": sell_price,
                "pnl": round(pnl, 4) if pnl is not None else None,
                "ai_entry": open_trade.get("ai_entry") if open_trade else trade.get("ai_entry"),
                "ai_exit": trade.get("ai_exit"),
                "ai_exit_checks": trade.get("ai_exit_checks", []),
            })

        open_record = None
        side = str(position_info.get("side", "")).lower() if isinstance(position_info, dict) else ""
        amount = cls._to_float(position_info.get("amount") if isinstance(position_info, dict) else None, 0) or 0
        entry_price = cls._to_float(position_info.get("entry_price") if isinstance(position_info, dict) else None)
        latest_open = None
        if side:
            for trade in reversed(open_trades):
                if str(trade.get("side", "")).lower() == side:
                    latest_open = trade
                    break
        if entry_price is None and latest_open:
            entry_price = cls._to_float(latest_open.get("price"))
        if amount <= 0 and latest_open:
            amount = cls._to_float(latest_open.get("amount"), 0) or 0
        if side in {"long", "short"} and not cls._is_dust_position(amount, current_price) and entry_price and current_price:
            pnl = (current_price - entry_price) * amount if side == "long" else (entry_price - current_price) * amount
            pnl_pct = pnl / (entry_price * amount) * 100 if entry_price * amount else 0
            open_record = {
                "start_time": cls._trade_time_label(
                    position_info.get("entry_time") if isinstance(position_info, dict) else None
                    or (latest_open.get("time") if latest_open else None)
                ),
                "side": side,
                "side_label": cls._side_label(side),
                "amount": round(amount, 6),
                "entry_price": entry_price,
                "current_price": current_price,
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 2),
                "ai_entry": (latest_open.get("ai_entry") if latest_open else None)
                            or (position_info.get("ai_entry") if isinstance(position_info, dict) else None),
                "ai_exit_plan": position_info.get("ai_exit_plan") if isinstance(position_info, dict) else None,
                "ai_exit_checks": position_info.get("ai_exit_checks", []) if isinstance(position_info, dict) else [],
            }

        return {
            "open": open_record,
            "signals": list(reversed(signals[-10:])),
            "completed": list(reversed(completed[-20:])),
        }

    @classmethod
    def _derive_position(cls, pos_info: dict, margin_assets: list, sol_price: float = 0) -> dict:
        file_pos = {}
        try:
            side = str(pos_info.get("side", "")).lower()
            amount = abs(float(pos_info.get("amount", 0) or 0))
            if side in {"long", "short"} and not cls._is_dust_position(amount, sol_price):
                file_pos = {"side": side, "amount": round(amount, 6), "source": "position_file"}
                for key in ("entry_price", "entry_time", "ai_entry", "ai_exit_plan", "ai_exit_checks"):
                    if pos_info.get(key):
                        file_pos[key] = pos_info[key]
        except Exception:
            file_pos = {}

        sol_row = next((a for a in margin_assets if a.get("asset") == "SOL"), None)
        if not sol_row:
            return file_pos
        try:
            net_sol = float(sol_row.get("net", 0) or 0)
        except Exception:
            return file_pos
        row_price = cls._to_float(sol_row.get("price"), sol_price) or sol_price
        if cls._is_dust_position(net_sol, row_price):
            return {}

        side = "long" if net_sol > 0 else "short"
        position = {"side": side, "amount": round(abs(net_sol), 6), "source": "margin_account"}
        if file_pos.get("side") == side:
            for key in ("entry_price", "entry_time", "ai_entry", "ai_exit_plan", "ai_exit_checks"):
                if file_pos.get(key):
                    position[key] = file_pos[key]
        return position

    def _calc_margin_level(self, margin_assets: list) -> float:
        asset_value = 0.0
        debt_value = 0.0
        for a in margin_assets:
            try:
                price = float(a.get("price", 0) or 0)
                asset_value += max(float(a.get("total", 0) or 0), 0) * price
                debt_value += max(float(a.get("borrowed", 0) or 0), 0) * price
            except Exception:
                pass
        local_level = 999.0 if debt_value <= 0 else (asset_value / debt_value) * 100

        try:
            info = self.margin.sapiGetMarginAccount()
            total_asset = float(info.get("totalAssetOfBtc", 0) or 0)
            total_debt = float(info.get("totalLiabilityOfBtc", 0) or 0)
            if total_asset > 0 and total_debt > 0:
                return (total_asset / total_debt) * 100
            raw_level = float(info.get("marginLevel", 0) or 0)
            if raw_level > 0 and total_debt > 0:
                return raw_level * 100 if raw_level < 20 else raw_level
        except Exception:
            pass
        return local_level

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
            # DEBUG: print SOL details.
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
            self._load_markets_once()
            if not getattr(self.spot, "markets", None):
                raise RuntimeError("spot markets unavailable; waiting for Binance/relay recovery")

            # SOL琛屾儏
            sol_t = self.spot.fetch_ticker(SYMBOL)
            sol_price = float(sol_t["last"])
            prices = {"USDT": 1.0, "SOL": sol_price}
            try:
                prices["BTC"] = float(self.spot.fetch_ticker("BTC/USDT")["last"])
            except Exception:
                prices["BTC"] = 0

            # Read position state and trade history written by the bot.
            pos_info = self._read_position_file()
            trade_history = self._read_trades_file()
            if self._sqlite_read_enabled and self._db.enabled:
                db_pos = self._db.get_latest_position()
                db_trades = self._db.get_trades(limit=50)
                db_history = self._db.get_equity_history(hours=72)
                if db_pos is not None:
                    pos_info = db_pos
                if db_trades:
                    trade_history = db_trades
                if db_history:
                    self._history = self._clean_history_points(db_history)

            # Four account snapshots.
            funding_a = self._get_funding()
            spot_a = self._extract(self.spot, "现货账户", prices)
            margin_a = self._extract(self.margin, "杠杆账户", prices)
            futures_a = self._extract(self.futures, "合约账户", prices)

            # Fill unknown asset prices.
            for a in funding_a + spot_a + margin_a + futures_a:
                if a["asset"] not in prices:
                    try:
                        prices[a["asset"]] = float(self.spot.fetch_ticker(f"{a['asset']}/USDT")["last"])
                    except Exception:
                        prices[a["asset"]] = 0

            # 閲嶆柊绠梫alue
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

            # Kline history for the dashboard chart.
            klines = self.spot.fetch_ohlcv(SYMBOL, timeframe="5m", limit=50)
            price_history = [{"t": k[0], "o": k[1], "h": k[2], "l": k[3], "c": k[4]} for k in klines]

            position_info = self._derive_position(pos_info, margin_a, sol_price)
            today_trade_stats = self._today_trade_stats(trade_history)
            trade_records = self._build_trade_records(trade_history, position_info, sol_price)
            slippage = self._derive_slippage(position_info, trade_history)

            # Margin level derived from current margin assets and debts.
            margin_level = self._calc_margin_level(margin_a)
            market_sentiment = self._market_sentiment(sol_price, sol_t, price_history, position_info, margin_level)

            # Daily PnL = current equity - first valid UTC+8 snapshot today.
            today_start = None
            today_start_ts = int(now_cst().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            for h in sorted(self._history, key=lambda x: x["t"]):
                if h["t"] >= today_start_ts and h.get("v", 0) > 0:
                    today_start = h["v"]
                    break
            daily_pnl = total_equity - today_start if today_start else 0
            daily_pnl_pct = (daily_pnl / today_start * 100) if today_start and today_start > 0 else 0

            # Total PnL = current equity - this version's initial equity.
            initial_equity = self._initial_equity
            if self._initial_equity_cny > 0 and self._cny_rate > 0:
                initial_equity = self._initial_equity_cny / self._cny_rate
            total_pnl = total_equity - initial_equity if initial_equity > 0 else 0
            total_pnl_pct = (total_pnl / initial_equity * 100) if initial_equity > 0 else 0
            if self._initial_equity_cny > 0:
                total_pnl_cny = total_equity * self._cny_rate - self._initial_equity_cny
            else:
                total_pnl_cny = total_pnl * self._cny_rate

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
                "total_pnl_cny": round(total_pnl_cny, 2),
                "total_pnl_pct": round(total_pnl_pct, 1),
                "initial_equity": round(initial_equity, 6),
                "initial_equity_cny": self._initial_equity_cny,
                "cny_rate": self._cny_rate,
                "total_cny": round(total_equity * self._cny_rate, 2),
                "equity_history": list(self._clean_history_points(self._history)),
                "position": position_info,
                "trade_history": trade_history,
                "trade_records": trade_records,
                "slippage": slippage,
                "today_trade_count": today_trade_stats["count"],
                "today_trade_pnl": today_trade_stats["pnl"],
                "sol_price": sol_price,
                "sol_change_24h": float(sol_t.get("percentage", 0) or 0),
                "price_history": price_history,
                "market_sentiment": market_sentiment,
                "db_stats": self._db_stats(),
                "updated_at": now_cst().strftime("%H:%M:%S"),
            }
            with self._lock:
                self._cache = data
        except Exception as e:
            logger.error(f"刷新失败: {e}")

    def get(self) -> dict:
        with self._lock:
            return dict(self._cache)


# ============================================================
