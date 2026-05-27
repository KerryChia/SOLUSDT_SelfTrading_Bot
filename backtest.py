import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from indicator import atr, bb, ema, macd, rsi, sma


@dataclass
class BacktestResult:
    initial_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe: float
    win_rate_pct: float
    profit_factor: float
    trade_count: int
    equity_curve: list
    trades: list


class BacktestEngine:
    def __init__(self, fee_rate: float = 0.001, leverage: float = 5.0, cap_use: float = 0.48):
        self.fee_rate = float(fee_rate)
        self.leverage = float(leverage)
        self.cap_use = float(cap_use)

    @staticmethod
    def load_json(path: str) -> dict:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            "timestamp": [int(x[0]) for x in rows],
            "open": np.array([float(x[1]) for x in rows], dtype=np.float64),
            "high": np.array([float(x[2]) for x in rows], dtype=np.float64),
            "low": np.array([float(x[3]) for x in rows], dtype=np.float64),
            "close": np.array([float(x[4]) for x in rows], dtype=np.float64),
            "volume": np.array([float(x[5]) for x in rows], dtype=np.float64),
        }

    @staticmethod
    def indicators(klines: dict, params: dict) -> dict:
        close = klines["close"]
        high = klines["high"]
        low = klines["low"]
        volume = klines["volume"]
        macd_l, sig_l, hist = macd(close, 8, 17, 9)
        bb_m, bb_u, bb_l = bb(close, params.get("bb_period", 14), params.get("bb_std", 2.0))
        return {
            "ema5": ema(close, 5),
            "ema13": ema(close, 13),
            "ema30": ema(close, 30),
            "rsi": rsi(close, 7),
            "macd": macd_l,
            "sig": sig_l,
            "hist": hist,
            "bb_m": bb_m,
            "bb_u": bb_u,
            "bb_l": bb_l,
            "bb_width": np.divide(bb_u - bb_l, bb_m, out=np.full_like(bb_m, np.nan), where=~np.isnan(bb_m) & (bb_m != 0)),
            "atr": atr(high, low, close, 7),
            "vol_ma": sma(volume, 10),
        }

    def _signal(self, i: int, klines: dict, ind: dict, params: dict, mode: str):
        close = klines["close"]
        open_ = klines["open"]
        high = klines["high"]
        low = klines["low"]
        volume = klines["volume"]
        price = close[i]
        if i < 40 or price <= 0:
            return None
        atr_pct = ind["atr"][i] / price if not np.isnan(ind["atr"][i]) else 0
        if atr_pct < params.get("min_atr_pct", 0.00045):
            return None
        if np.isnan(ind["bb_width"][i]) or ind["bb_width"][i] < params.get("min_bb_width", 0.006):
            return None
        if np.isnan(ind["vol_ma"][i]) or volume[i] < ind["vol_ma"][i] * params.get("vol_mult", 1.5):
            return None

        long_trend = ind["ema5"][i] > ind["ema13"][i] > ind["ema30"][i]
        short_trend = ind["ema5"][i] < ind["ema13"][i] < ind["ema30"][i]
        long_sig = (
            long_trend and low[i] <= ind["bb_l"][i] * params.get("bb_long_touch", 1.003)
            and close[i] > open_[i] and ind["rsi"][i] > ind["rsi"][i - 1]
        )
        short_sig = (
            short_trend and high[i] >= ind["bb_u"][i] * params.get("bb_short_touch", 0.997)
            and close[i] < open_[i] and ind["rsi"][i] < ind["rsi"][i - 1]
        )
        if mode == "ai_sim":
            if long_sig and ind["rsi"][i] > 52:
                long_sig = False
            if short_sig and ind["rsi"][i] < 48:
                short_sig = False
        if long_sig:
            return "long"
        if short_sig:
            return "short"
        return None

    def run(self, klines: dict, initial_equity: float = 30.0, mode: str = "rules",
            params: dict = None) -> BacktestResult:
        params = params or {}
        ind = self.indicators(klines, params)
        equity = float(initial_equity)
        equity_curve = []
        trades = []
        pos = None
        peak = equity
        max_dd = 0.0

        for i, price in enumerate(klines["close"]):
            if i < 40:
                equity_curve.append(equity)
                continue
            if pos:
                pnl_pct = (price - pos["entry"]) / pos["entry"] if pos["side"] == "long" else (pos["entry"] - price) / pos["entry"]
                atr_pct = ind["atr"][i] / price if not np.isnan(ind["atr"][i]) and price else 0
                tp = max(params.get("take_profit", 0.015), atr_pct * params.get("atr_tp_mult", 1.5))
                sl = min(params.get("hard_stop", 0.010), max(0.006, atr_pct * params.get("atr_sl_mult", 1.0)))
                if pnl_pct >= tp or pnl_pct <= -sl:
                    notional = equity * self.leverage * self.cap_use
                    gross = notional * pnl_pct
                    fee = notional * self.fee_rate * 2
                    net = gross - fee
                    equity += net
                    trades.append({**pos, "exit_i": i, "exit": float(price), "pnl": round(net, 4), "pnl_pct": round(pnl_pct * 100, 3)})
                    pos = None
            else:
                side = self._signal(i, klines, ind, params, mode)
                if side:
                    pos = {"side": side, "entry_i": i, "entry": float(price)}
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
            equity_curve.append(round(equity, 4))

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]
        gains = sum(t["pnl"] for t in wins)
        loss_abs = abs(sum(t["pnl"] for t in losses))
        returns = np.diff(np.array(equity_curve, dtype=np.float64))
        sharpe = 0.0
        if len(returns) > 2 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * math.sqrt(365 * 24 * 12))
        return BacktestResult(
            initial_equity=initial_equity,
            final_equity=round(equity, 4),
            total_return_pct=round((equity - initial_equity) / initial_equity * 100, 3),
            max_drawdown_pct=round(max_dd * 100, 3),
            sharpe=round(sharpe, 3),
            win_rate_pct=round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            profit_factor=round(gains / loss_abs, 3) if loss_abs > 0 else (999.0 if gains > 0 else 0.0),
            trade_count=len(trades),
            equity_curve=equity_curve,
            trades=trades,
        )

    @staticmethod
    def _trend_from_ind(klines: dict, ind: dict, idx: int) -> str:
        if not klines or not ind:
            return "neutral"
        close = klines["close"]
        if len(close) == 0:
            return "neutral"
        idx = min(max(0, int(idx)), len(close) - 1)
        if idx < 40:
            return "neutral"
        price = close[idx]
        ema30 = ind["ema30"][idx]
        hist = ind["hist"][idx]
        if np.isnan(ema30) or np.isnan(hist):
            return "neutral"
        if price > ema30 and hist > 0:
            return "bull"
        if price < ema30 and hist < 0:
            return "bear"
        return "neutral"

    def _mtf_consensus_at(self, i: int, klines_15m: dict, ind_15m: dict,
                          klines_1h: dict, ind_1h: dict) -> str:
        trend_15m = self._trend_from_ind(klines_15m, ind_15m, i // 3) if klines_15m else "neutral"
        trend_1h = self._trend_from_ind(klines_1h, ind_1h, i // 12) if klines_1h else "neutral"
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
        if aligned_bull:
            return "bull"
        if aligned_bear:
            return "bear"
        return "neutral"

    def run_with_mtf(self, klines_5m: dict, klines_15m=None, klines_1h=None,
                     initial_equity: float = 30.0, mode: str = "rules",
                     params: dict = None) -> BacktestResult:
        params = params or {}
        ind = self.indicators(klines_5m, params)
        ind_15m = self.indicators(klines_15m, params) if klines_15m else None
        ind_1h = self.indicators(klines_1h, params) if klines_1h else None
        equity = float(initial_equity)
        equity_curve = []
        trades = []
        pos = None
        peak = equity
        max_dd = 0.0

        for i, price in enumerate(klines_5m["close"]):
            if i < 40:
                equity_curve.append(equity)
                continue
            if pos:
                pnl_pct = (price - pos["entry"]) / pos["entry"] if pos["side"] == "long" else (pos["entry"] - price) / pos["entry"]
                atr_pct = ind["atr"][i] / price if not np.isnan(ind["atr"][i]) and price else 0
                tp = max(params.get("take_profit", 0.015), atr_pct * params.get("atr_tp_mult", 1.5))
                sl = min(params.get("hard_stop", 0.010), max(0.006, atr_pct * params.get("atr_sl_mult", 1.0)))
                if pnl_pct >= tp or pnl_pct <= -sl:
                    notional = equity * self.leverage * self.cap_use
                    gross = notional * pnl_pct
                    fee = notional * self.fee_rate * 2
                    net = gross - fee
                    equity += net
                    trades.append({**pos, "exit_i": i, "exit": float(price), "pnl": round(net, 4), "pnl_pct": round(pnl_pct * 100, 3)})
                    pos = None
            else:
                side = self._signal(i, klines_5m, ind, params, mode)
                if side:
                    consensus = self._mtf_consensus_at(i, klines_15m, ind_15m, klines_1h, ind_1h)
                    if side == "long" and consensus == "bear":
                        side = None
                    elif side == "short" and consensus == "bull":
                        side = None
                    if side:
                        pos = {"side": side, "entry_i": i, "entry": float(price), "mtf": consensus}
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)
            equity_curve.append(round(equity, 4))

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]
        gains = sum(t["pnl"] for t in wins)
        loss_abs = abs(sum(t["pnl"] for t in losses))
        returns = np.diff(np.array(equity_curve, dtype=np.float64))
        sharpe = 0.0
        if len(returns) > 2 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * math.sqrt(365 * 24 * 12))
        return BacktestResult(
            initial_equity=initial_equity,
            final_equity=round(equity, 4),
            total_return_pct=round((equity - initial_equity) / initial_equity * 100, 3),
            max_drawdown_pct=round(max_dd * 100, 3),
            sharpe=round(sharpe, 3),
            win_rate_pct=round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            profit_factor=round(gains / loss_abs, 3) if loss_abs > 0 else (999.0 if gains > 0 else 0.0),
            trade_count=len(trades),
            equity_curve=equity_curve,
            trades=trades,
        )

    def optimize(self, klines: dict, grid: dict, initial_equity: float = 30.0,
                 mode: str = "rules", klines_15m=None, klines_1h=None,
                 mtf_options=None) -> list:
        keys = list(grid.keys())
        results = []
        mtf_options = list(mtf_options) if mtf_options is not None else [False]
        for values in itertools.product(*[grid[k] for k in keys]):
            base_params = dict(zip(keys, values))
            for use_mtf in mtf_options:
                params = {**base_params, "use_mtf": bool(use_mtf)}
                if use_mtf:
                    result = self.run_with_mtf(klines, klines_15m, klines_1h, initial_equity, mode, base_params)
                else:
                    result = self.run(klines, initial_equity, mode, base_params)
                results.append((params, result))
        return sorted(results, key=lambda item: (item[1].final_equity, -item[1].max_drawdown_pct), reverse=True)
