from dataclasses import dataclass


@dataclass
class FeatureFlags:
    signal_volume_filter: bool = True
    signal_ema_trend_filter: bool = True
    signal_bb_width_filter: bool = True
    signal_fake_breakout_gate: bool = True
    signal_rsi_divergence: bool = True
    dynamic_atr_exit: bool = True
    fee_net_pnl: bool = True
    ai_retries: bool = True
    ai_cache_fallback: bool = True
    ai_quality_stats: bool = True
    multi_timeframe_filter: bool = False
    multi_timeframe_ai_context: bool = True
    limit_ioc_orders: bool = False
    sqlite_storage: bool = False
    sqlite_read_enabled: bool = False


@dataclass
class RuntimeConfig:
    features: FeatureFlags
    db_keep_days: int = 90


def _bool(raw: dict, key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _int(raw: dict, key: str, default: int) -> int:
    try:
        return int(raw.get(key, default))
    except Exception:
        return int(default)


def build_runtime_config(raw_cfg: dict) -> RuntimeConfig:
    raw_flags = raw_cfg.get("feature_flags") or {}
    defaults = FeatureFlags()
    return RuntimeConfig(features=FeatureFlags(
        signal_volume_filter=_bool(raw_flags, "signal_volume_filter", defaults.signal_volume_filter),
        signal_ema_trend_filter=_bool(raw_flags, "signal_ema_trend_filter", defaults.signal_ema_trend_filter),
        signal_bb_width_filter=_bool(raw_flags, "signal_bb_width_filter", defaults.signal_bb_width_filter),
        signal_fake_breakout_gate=_bool(raw_flags, "signal_fake_breakout_gate", defaults.signal_fake_breakout_gate),
        signal_rsi_divergence=_bool(raw_flags, "signal_rsi_divergence", defaults.signal_rsi_divergence),
        dynamic_atr_exit=_bool(raw_flags, "dynamic_atr_exit", defaults.dynamic_atr_exit),
        fee_net_pnl=_bool(raw_flags, "fee_net_pnl", defaults.fee_net_pnl),
        ai_retries=_bool(raw_flags, "ai_retries", defaults.ai_retries),
        ai_cache_fallback=_bool(raw_flags, "ai_cache_fallback", defaults.ai_cache_fallback),
        ai_quality_stats=_bool(raw_flags, "ai_quality_stats", defaults.ai_quality_stats),
        multi_timeframe_filter=_bool(raw_flags, "multi_timeframe_filter", defaults.multi_timeframe_filter),
        multi_timeframe_ai_context=_bool(raw_flags, "multi_timeframe_ai_context", defaults.multi_timeframe_ai_context),
        limit_ioc_orders=_bool(raw_flags, "limit_ioc_orders", defaults.limit_ioc_orders),
        sqlite_storage=_bool(raw_flags, "sqlite_storage", defaults.sqlite_storage),
        sqlite_read_enabled=_bool(raw_flags, "sqlite_read_enabled", defaults.sqlite_read_enabled),
    ), db_keep_days=_int(raw_flags, "db_keep_days", raw_cfg.get("db_keep_days", 90)))
