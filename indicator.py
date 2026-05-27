import numpy as np


def sma(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.full_like(arr, np.nan, dtype=np.float64)
    if len(arr) < period:
        return result
    for i in range(period - 1, len(arr)):
        result[i] = np.mean(arr[i - period + 1:i + 1])
    return result


def ema(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.full_like(arr, np.nan, dtype=np.float64)
    if len(arr) < period:
        return result
    alpha = 2 / (period + 1)
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def rsi(close: np.ndarray, period: int = 7) -> np.ndarray:
    result = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period + 1:
        return result
    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    result[period] = 100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        result[i] = 100 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return result


def macd(close: np.ndarray, fast: int = 8, slow: int = 17, signal: int = 9):
    fast_line = ema(close, fast)
    slow_line = ema(close, slow)
    macd_line = fast_line - slow_line
    sig = ema(np.nan_to_num(macd_line), signal)
    hist = macd_line - sig
    return macd_line, sig, hist


def bb(close: np.ndarray, period: int = 14, std: float = 2.0):
    mid = sma(close, period)
    up = np.full_like(close, np.nan, dtype=np.float64)
    lo = np.full_like(close, np.nan, dtype=np.float64)
    for i in range(period - 1, len(close)):
        spread = np.std(close[i - period + 1:i + 1])
        up[i] = mid[i] + spread * std
        lo[i] = mid[i] - spread * std
    return mid, up, lo


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 7) -> np.ndarray:
    result = np.full_like(close, np.nan, dtype=np.float64)
    if len(close) < period + 1:
        return result
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    result[period] = np.mean(tr[:period])
    for i in range(period + 1, len(close)):
        result[i] = (result[i - 1] * (period - 1) + tr[i - 1]) / period
    return result
