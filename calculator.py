import time
from collections import deque
from decimal import Decimal, ROUND_HALF_UP


DEFAULT_FEE_RATE = Decimal("0.001")


def money(value) -> Decimal:
    return Decimal(str(value or 0))


def round_money(value: Decimal, places: str = "0.0001") -> float:
    return float(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def estimate_round_trip_fee(entry_price: float, exit_price: float, amount: float,
                            fee_rate: Decimal = DEFAULT_FEE_RATE) -> float:
    entry_notional = money(entry_price) * money(amount)
    exit_notional = money(exit_price) * money(amount)
    return round_money((entry_notional + exit_notional) * fee_rate)


def net_pnl_after_fee(gross_pnl: float, entry_price: float, exit_price: float,
                      amount: float, fee_rate: Decimal = DEFAULT_FEE_RATE) -> tuple[float, float]:
    fee = estimate_round_trip_fee(entry_price, exit_price, amount, fee_rate)
    return round_money(money(gross_pnl) - money(fee)), fee


class SlippageTracker:
    def __init__(self, max_records: int = 100):
        self.records = deque(maxlen=max_records)

    def record(self, side: str, signal_price: float, fill_price: float):
        signal_price = float(signal_price or 0)
        fill_price = float(fill_price or 0)
        slippage_pct = (fill_price - signal_price) / signal_price * 100 if signal_price > 0 else 0.0
        if side == "short":
            slippage_pct = -slippage_pct
        item = {
            "time": time.strftime("%m/%d %H:%M:%S"),
            "side": side,
            "signal": round(signal_price, 4),
            "fill": round(fill_price, 4),
            "slippage_pct": round(slippage_pct, 4),
        }
        self.records.append(item)
        return item

    def avg_slippage_pct(self) -> float:
        if not self.records:
            return 0.0
        return round(sum(r["slippage_pct"] for r in self.records) / len(self.records), 4)

    def max_slippage_pct(self) -> float:
        if not self.records:
            return 0.0
        return round(max(r["slippage_pct"] for r in self.records), 4)

    def to_dict(self) -> dict:
        return {
            "avg": self.avg_slippage_pct(),
            "max": self.max_slippage_pct(),
            "count": len(self.records),
            "recent": list(self.records)[-10:],
        }
