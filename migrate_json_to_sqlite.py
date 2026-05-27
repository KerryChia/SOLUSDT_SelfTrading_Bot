"""
Migrate existing JSON data into trades.db.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from storage import Storage, init_db


BASE = Path(__file__).resolve().parent


def read_json(name, default):
    path = BASE / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"skip {name}: {e}")
        return default


def parse_time(value):
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text)
    except Exception:
        pass
    try:
        return datetime.strptime(f"{datetime.now().year}/{text}", "%Y/%m/%d %H:%M:%S")
    except Exception:
        return None


def migrate_trades(db: Storage):
    rows = read_json("trades.json", [])
    open_trades = []
    count = 0
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", ""))
        side = str(item.get("side", ""))
        if action == "开仓":
            open_trades.append(item)
            db.save_trade({
                "opened_at": (parse_time(item.get("time")) or datetime.now()).isoformat(),
                "side": side,
                "amount_sol": item.get("amount", 0),
                "entry_price": item.get("price", 0),
                "signal_reason": item.get("signal_reason", ""),
                "cap_use": item.get("cap_use"),
                "ai_entry": item.get("ai_entry"),
            })
            count += 1
            continue
        if action != "平仓":
            continue
        match = None
        for idx in range(len(open_trades) - 1, -1, -1):
            if str(open_trades[idx].get("side", "")) == side:
                match = open_trades.pop(idx)
                break
        opened_at = parse_time(match.get("time")) if match else None
        closed_at = parse_time(item.get("time"))
        db.save_trade({
            "opened_at": (opened_at or closed_at or datetime.now()).isoformat(),
            "closed_at": (closed_at or datetime.now()).isoformat(),
            "side": side,
            "amount_sol": item.get("amount", match.get("amount") if match else 0),
            "entry_price": match.get("price", 0) if match else 0,
            "exit_price": item.get("price", 0),
            "gross_pnl": item.get("gross_pnl"),
            "fee_estimate": item.get("fee_estimate"),
            "net_pnl": item.get("net_pnl", item.get("pnl")),
            "close_reason": item.get("close_reason", ""),
            "ai_entry": item.get("ai_entry") or (match.get("ai_entry") if match else None),
            "ai_exit": item.get("ai_exit"),
            "ai_exit_checks": item.get("ai_exit_checks", []),
        })
        count += 1
    return count


def migrate_position(db: Storage):
    pos = read_json("position.json", {})
    if not isinstance(pos, dict) or not pos:
        return 0
    db.save_position_snapshot(pos, pos.get("entry_price", 0), 0, 0)
    return 1


def migrate_history(db: Storage):
    rows = read_json("history.json", [])
    count = 0
    for item in rows if isinstance(rows, list) else []:
        try:
            equity = float(item.get("v", 0) or 0)
        except Exception:
            continue
        if equity > 0:
            ts = item.get("t")
            iso_ts = None
            try:
                iso_ts = datetime.fromtimestamp(int(ts) / 1000).isoformat()
            except Exception:
                pass
            db.save_equity_snapshot(equity, iso_ts)
            count += 1
    return count


def migrate_risk(db: Storage):
    state = read_json("risk_state.json", {})
    if not isinstance(state, dict) or not state:
        return 0
    db.save_risk_state(
        state.get("date") or datetime.now().date().isoformat(),
        float(state.get("daily_pnl", 0) or 0),
        int(state.get("daily_trades", 0) or 0),
        int(state.get("consec_losses", 0) or 0),
        float(state.get("start_equity", 0) or 0),
    )
    return 1


def migrate_ai_jsonl(db: Storage):
    path = BASE / "ai_stats.jsonl"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        db.save_ai_decision(
            item.get("call_type", item.get("task", "entry")),
            item.get("side", ""),
            float(item.get("price", 0) or 0),
            item.get("action", ""),
            item.get("cap_use"),
            int(item.get("response_ms", 0) or 0),
            item.get("reason", ""),
            item.get("payload"),
            item.get("result", item),
        )
        count += 1
    return count


def main():
    os.chdir(BASE)
    init_db()
    db = Storage(enabled=True)
    print("trades:", migrate_trades(db))
    print("position:", migrate_position(db))
    print("history:", migrate_history(db))
    print("risk:", migrate_risk(db))
    print("ai:", migrate_ai_jsonl(db))
    print("OK")


if __name__ == "__main__":
    main()
