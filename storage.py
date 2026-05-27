"""
SQLite storage module for dual-write persistence.
"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Any, Optional


DB_PATH = "trades.db"
SCHEMA_VERSION = 1


def _logger():
    return logging.getLogger("SOLUSDT")


def _db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _json(value: Any) -> str:
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return ""


def _loads(value: Any, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _iso_now() -> str:
    return datetime.now().isoformat()


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d %H:%M:%S"):
        try:
            if fmt.startswith("%m"):
                return datetime.strptime(f"{datetime.now().year}/{text}", "%Y/%m/%d %H:%M:%S")
            return datetime.strptime(text.replace("T", " "), fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _time_label(value: Any) -> str:
    dt = _parse_time(value)
    return dt.strftime("%m/%d %H:%M:%S") if dt else str(value or "")


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at       TEXT NOT NULL,
                closed_at       TEXT,
                side            TEXT NOT NULL,
                amount_sol      REAL NOT NULL,
                entry_price     REAL NOT NULL,
                exit_price      REAL,
                gross_pnl       REAL,
                fee_estimate    REAL,
                net_pnl         REAL,
                hold_minutes    REAL,
                close_reason    TEXT,
                signal_reason   TEXT,
                mtf_consensus   TEXT,
                cap_use         REAL,
                ai_entry_action TEXT,
                ai_entry_reason TEXT,
                ai_exit_action  TEXT,
                ai_exit_reason  TEXT,
                ai_checks_json  TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);
            CREATE INDEX IF NOT EXISTS idx_trades_side ON trades(side);
            CREATE INDEX IF NOT EXISTS idx_trades_net_pnl ON trades(net_pnl);

            CREATE TABLE IF NOT EXISTS position_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                side        TEXT,
                amount_sol  REAL,
                entry_price REAL,
                current_price REAL,
                unrealized_pnl_pct REAL,
                margin_level REAL,
                equity      REAL,
                ai_plan_json TEXT
            );

            CREATE TABLE IF NOT EXISTS equity_snapshots (
                ts      TEXT NOT NULL,
                equity  REAL NOT NULL,
                UNIQUE(ts)
            );

            CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(ts);

            CREATE TABLE IF NOT EXISTS ai_decisions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                call_type   TEXT NOT NULL,
                side        TEXT,
                price       REAL,
                action      TEXT NOT NULL,
                cap_use     REAL,
                response_ms INTEGER,
                reason      TEXT,
                payload_json TEXT,
                result_json  TEXT
            );

            CREATE TABLE IF NOT EXISTS risk_state (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                daily_pnl       REAL NOT NULL,
                daily_trades    INTEGER NOT NULL,
                consec_losses   INTEGER NOT NULL,
                start_equity    REAL,
                updated_at      TEXT DEFAULT (datetime('now')),
                UNIQUE(date)
            );
        """)
        conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))


class Storage:
    def __init__(self, enabled: bool = False, keep_days: int = 90):
        self.enabled = bool(enabled)
        self.keep_days = int(keep_days or 90)
        if self.enabled:
            try:
                init_db()
                _logger().info("SQLite enabled")
            except Exception as e:
                _logger().warning(f"SQLite init failed, fallback to JSON: {e}")
                self.enabled = False

    def _warn(self, message: str, exc: Exception):
        _logger().warning(f"{message}: {exc}")

    def save_trade(self, trade: dict) -> bool:
        if not self.enabled:
            return False
        try:
            ai_entry = trade.get("ai_entry") if isinstance(trade.get("ai_entry"), dict) else {}
            ai_exit = trade.get("ai_exit") if isinstance(trade.get("ai_exit"), dict) else {}
            mtf = trade.get("mtf") if isinstance(trade.get("mtf"), dict) else {}
            opened_at = trade.get("opened_at") or trade.get("time") or _iso_now()
            closed_at = trade.get("closed_at")
            side_value = str(trade.get("side", ""))
            amount_value = float(trade.get("amount_sol", trade.get("amount", 0)) or 0)
            entry_price_value = float(trade.get("entry_price", trade.get("price", 0)) or 0)
            exit_price_value = trade.get("exit_price")
            gross_pnl_value = trade.get("gross_pnl")
            fee_estimate_value = trade.get("fee_estimate")
            net_pnl_value = trade.get("net_pnl", trade.get("pnl"))
            hold_minutes_value = trade.get("hold_minutes")
            close_reason_value = trade.get("close_reason", "")
            signal_reason_value = trade.get("signal_reason", "")
            mtf_consensus_value = trade.get("mtf_consensus") or mtf.get("consensus")
            cap_use_value = trade.get("cap_use")
            ai_entry_action = ai_entry.get("action")
            ai_entry_reason = ai_entry.get("reason")
            ai_exit_action = ai_exit.get("action")
            ai_exit_reason = ai_exit.get("reason")
            ai_checks_json = _json(trade.get("ai_exit_checks", []))
            with _conn() as conn:
                if closed_at:
                    cur = conn.execute(
                        """
                        UPDATE trades
                        SET closed_at = ?,
                            amount_sol = ?,
                            entry_price = CASE WHEN entry_price > 0 THEN entry_price ELSE ? END,
                            exit_price = ?,
                            gross_pnl = ?,
                            fee_estimate = ?,
                            net_pnl = ?,
                            hold_minutes = ?,
                            close_reason = ?,
                            signal_reason = CASE WHEN ? != '' THEN ? ELSE signal_reason END,
                            mtf_consensus = COALESCE(?, mtf_consensus),
                            cap_use = COALESCE(?, cap_use),
                            ai_entry_action = COALESCE(?, ai_entry_action),
                            ai_entry_reason = COALESCE(?, ai_entry_reason),
                            ai_exit_action = ?,
                            ai_exit_reason = ?,
                            ai_checks_json = ?
                        WHERE id = (
                            SELECT id FROM trades
                            WHERE closed_at IS NULL AND side = ?
                            ORDER BY opened_at DESC, id DESC
                            LIMIT 1
                        )
                        """,
                        (
                            closed_at, amount_value, entry_price_value, exit_price_value,
                            gross_pnl_value, fee_estimate_value, net_pnl_value, hold_minutes_value,
                            close_reason_value, signal_reason_value, signal_reason_value,
                            mtf_consensus_value, cap_use_value, ai_entry_action, ai_entry_reason,
                            ai_exit_action, ai_exit_reason, ai_checks_json, side_value,
                        ),
                    )
                    if cur.rowcount:
                        return True
                conn.execute(
                    """
                    INSERT INTO trades (
                        opened_at, closed_at, side, amount_sol, entry_price, exit_price,
                        gross_pnl, fee_estimate, net_pnl, hold_minutes, close_reason,
                        signal_reason, mtf_consensus, cap_use, ai_entry_action,
                        ai_entry_reason, ai_exit_action, ai_exit_reason, ai_checks_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        opened_at,
                        closed_at,
                        side_value,
                        amount_value,
                        entry_price_value,
                        exit_price_value,
                        gross_pnl_value,
                        fee_estimate_value,
                        net_pnl_value,
                        hold_minutes_value,
                        close_reason_value,
                        signal_reason_value,
                        mtf_consensus_value,
                        cap_use_value,
                        ai_entry_action,
                        ai_entry_reason,
                        ai_exit_action,
                        ai_exit_reason,
                        ai_checks_json,
                    ),
                )
            return True
        except Exception as e:
            self._warn("SQLite save_trade failed", e)
            return False

    def get_trades(self, limit: int = 50, offset: int = 0,
                   side: str = None, since: str = None, until: str = None,
                   min_pnl: float = None, max_pnl: float = None) -> list:
        if not self.enabled:
            return []
        try:
            where = []
            params = []
            if side:
                where.append("side = ?")
                params.append(side)
            if since:
                where.append("COALESCE(closed_at, opened_at) >= ?")
                params.append(since)
            if until:
                where.append("COALESCE(closed_at, opened_at) <= ?")
                params.append(until)
            if min_pnl is not None:
                where.append("(net_pnl IS NULL OR net_pnl >= ?)")
                params.append(min_pnl)
            if max_pnl is not None:
                where.append("(net_pnl IS NULL OR net_pnl <= ?)")
                params.append(max_pnl)
            sql = "SELECT * FROM trades"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
            with _conn() as conn:
                rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            out = []
            for row in reversed(rows):
                open_item = {
                    "time": _time_label(row.get("opened_at")),
                    "action": "开仓",
                    "side": row.get("side"),
                    "amount": row.get("amount_sol"),
                    "price": row.get("entry_price"),
                    "signal_reason": row.get("signal_reason"),
                    "cap_use": row.get("cap_use"),
                    "ai_entry": {
                        "action": row.get("ai_entry_action"),
                        "reason": row.get("ai_entry_reason"),
                    },
                }
                out.append(open_item)
                if row.get("closed_at"):
                    out.append({
                        "time": _time_label(row.get("closed_at")),
                        "action": "平仓",
                        "side": row.get("side"),
                        "amount": row.get("amount_sol"),
                        "price": row.get("exit_price"),
                        "pnl": row.get("net_pnl"),
                        "gross_pnl": row.get("gross_pnl"),
                        "fee_estimate": row.get("fee_estimate"),
                        "net_pnl": row.get("net_pnl"),
                        "close_reason": row.get("close_reason"),
                        "ai_entry": open_item.get("ai_entry"),
                        "ai_exit": {
                            "action": row.get("ai_exit_action"),
                            "reason": row.get("ai_exit_reason"),
                        },
                        "ai_exit_checks": _loads(row.get("ai_checks_json"), []),
                    })
            return out
        except Exception as e:
            self._warn("SQLite get_trades failed", e)
            return []

    def get_trade_stats(self, since: str = None) -> dict:
        default = {"total_trades": 0, "win_count": 0, "loss_count": 0, "win_rate": 0.0,
                   "total_net_pnl": 0.0, "avg_hold_min": 0.0, "profit_factor": 0.0}
        if not self.enabled:
            return default
        try:
            where = "WHERE closed_at IS NOT NULL"
            params = []
            if since:
                where += " AND closed_at >= ?"
                params.append(since)
            with _conn() as conn:
                rows = conn.execute(
                    f"SELECT net_pnl, hold_minutes FROM trades {where}",
                    params,
                ).fetchall()
            pnls = [float(r["net_pnl"] or 0) for r in rows]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            hold = [float(r["hold_minutes"] or 0) for r in rows if r["hold_minutes"] is not None]
            total = len(pnls)
            loss_abs = abs(sum(losses))
            return {
                "total_trades": total,
                "win_count": len(wins),
                "loss_count": len(losses),
                "win_rate": round(len(wins) / total * 100, 2) if total else 0.0,
                "total_net_pnl": round(sum(pnls), 4),
                "avg_hold_min": round(sum(hold) / len(hold), 2) if hold else 0.0,
                "profit_factor": round(sum(wins) / loss_abs, 3) if loss_abs > 0 else (999.0 if wins else 0.0),
            }
        except Exception as e:
            self._warn("SQLite get_trade_stats failed", e)
            return default

    def save_position_snapshot(self, pos: dict, price: float, margin_level: float, equity: float) -> bool:
        if not self.enabled:
            return False
        try:
            pos = pos or {}
            side = pos.get("side")
            amount = float(pos.get("amount", 0) or 0)
            entry = float(pos.get("entry_price", 0) or 0)
            price = float(price or entry or 0)
            pnl_pct = 0.0
            if side == "long" and entry > 0:
                pnl_pct = (price - entry) / entry * 100
            elif side == "short" and entry > 0:
                pnl_pct = (entry - price) / entry * 100
            with _conn() as conn:
                conn.execute(
                    """
                    INSERT INTO position_snapshots (
                        ts, side, amount_sol, entry_price, current_price,
                        unrealized_pnl_pct, margin_level, equity, ai_plan_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_iso_now(), side, amount, entry, price, pnl_pct, margin_level, equity,
                     _json(pos.get("ai_exit_plan"))),
                )
            return True
        except Exception as e:
            self._warn("SQLite save_position_snapshot failed", e)
            return False

    def get_latest_position(self) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            with _conn() as conn:
                row = conn.execute(
                    "SELECT * FROM position_snapshots ORDER BY ts DESC, id DESC LIMIT 1"
                ).fetchone()
            if not row:
                return None
            row = dict(row)
            if not row.get("side"):
                return {}
            return {
                "side": row.get("side"),
                "amount": row.get("amount_sol"),
                "entry_price": row.get("entry_price"),
                "entry_time": row.get("ts"),
                "ai_exit_plan": _loads(row.get("ai_plan_json"), {}),
            }
        except Exception as e:
            self._warn("SQLite get_latest_position failed", e)
            return None

    def save_equity_snapshot(self, equity: float, ts: str = None) -> bool:
        if not self.enabled:
            return False
        try:
            equity = float(equity or 0)
            if equity <= 0:
                return False
            now = _parse_time(ts) if ts else None
            now = (now or datetime.now()).replace(minute=0, second=0, microsecond=0)
            cutoff = (datetime.now() - timedelta(days=self.keep_days)).isoformat()
            with _conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO equity_snapshots (ts, equity) VALUES (?, ?)",
                    (now.isoformat(), equity),
                )
                conn.execute("DELETE FROM equity_snapshots WHERE ts < ?", (cutoff,))
            return True
        except Exception as e:
            self._warn("SQLite save_equity_snapshot failed", e)
            return False

    def get_equity_history(self, hours: int = 72) -> list:
        if not self.enabled:
            return []
        try:
            since = (datetime.now() - timedelta(hours=int(hours or 72))).isoformat()
            with _conn() as conn:
                rows = conn.execute(
                    "SELECT ts, equity FROM equity_snapshots WHERE ts >= ? ORDER BY ts",
                    (since,),
                ).fetchall()
            out = []
            for r in rows:
                dt = _parse_time(r["ts"])
                if not dt:
                    continue
                equity = float(r["equity"] or 0)
                out.append({"ts": r["ts"], "equity": equity,
                            "t": int(dt.timestamp() * 1000), "v": round(equity, 2)})
            return out
        except Exception as e:
            self._warn("SQLite get_equity_history failed", e)
            return []

    def save_ai_decision(self, call_type: str, side: str, price: float,
                         action: str, cap_use: float, response_ms: int,
                         reason: str, payload: dict = None, result: dict = None) -> bool:
        if not self.enabled:
            return False
        try:
            with _conn() as conn:
                conn.execute(
                    """
                    INSERT INTO ai_decisions (
                        ts, call_type, side, price, action, cap_use,
                        response_ms, reason, payload_json, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_iso_now(), call_type, side, price, action, cap_use,
                     response_ms, reason, _json(payload), _json(result)),
                )
            return True
        except Exception as e:
            self._warn("SQLite save_ai_decision failed", e)
            return False

    def get_ai_quality(self, since: str = None) -> dict:
        default = {"total_calls": 0, "total_approves": 0, "approve_win_rate": 0.0,
                   "avg_response_ms": 0}
        if not self.enabled:
            return default
        try:
            where = []
            params = []
            if since:
                where.append("ts >= ?")
                params.append(since)
            sql_where = "WHERE " + " AND ".join(where) if where else ""
            with _conn() as conn:
                row = conn.execute(
                    f"""
                    SELECT COUNT(*) AS total_calls,
                           SUM(CASE WHEN action='approve' THEN 1 ELSE 0 END) AS total_approves,
                           AVG(response_ms) AS avg_response_ms
                    FROM ai_decisions {sql_where}
                    """,
                    params,
                ).fetchone()
                trade_rows = conn.execute(
                    "SELECT net_pnl FROM trades WHERE ai_entry_action='approve' AND closed_at IS NOT NULL"
                ).fetchall()
            pnls = [float(r["net_pnl"] or 0) for r in trade_rows]
            wins = [p for p in pnls if p > 0]
            return {
                "total_calls": int(row["total_calls"] or 0),
                "total_approves": int(row["total_approves"] or 0),
                "approve_win_rate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
                "avg_response_ms": int(row["avg_response_ms"] or 0),
            }
        except Exception as e:
            self._warn("SQLite get_ai_quality failed", e)
            return default

    def save_risk_state(self, date: str, daily_pnl: float, daily_trades: int,
                        consec_losses: int, start_equity: float) -> bool:
        if not self.enabled:
            return False
        try:
            with _conn() as conn:
                conn.execute(
                    """
                    INSERT INTO risk_state (
                        date, daily_pnl, daily_trades, consec_losses, start_equity, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        daily_pnl=excluded.daily_pnl,
                        daily_trades=excluded.daily_trades,
                        consec_losses=excluded.consec_losses,
                        start_equity=excluded.start_equity,
                        updated_at=excluded.updated_at
                    """,
                    (date, daily_pnl, daily_trades, consec_losses, start_equity, _iso_now()),
                )
            return True
        except Exception as e:
            self._warn("SQLite save_risk_state failed", e)
            return False

    def get_risk_state(self, date: str = None) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            date = date or datetime.now().date().isoformat()
            with _conn() as conn:
                row = conn.execute("SELECT * FROM risk_state WHERE date = ?", (date,)).fetchone()
            return dict(row) if row else None
        except Exception as e:
            self._warn("SQLite get_risk_state failed", e)
            return None
