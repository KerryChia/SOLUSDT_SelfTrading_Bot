"""
SOLUSDT monitor dashboard.
Shows account equity, current position, strategy state, trade records, and access logs.
"""

import os, sys, json, time, logging, threading, secrets
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

from data_fetcher import (
    ACCESS_LOG_LIMIT,
    CST,
    DASHBOARD_REFRESH_SECONDS,
    DataFetcher,
    GeoResolver,
    now_cst,
)

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


def index_html() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.warning(f"dashboard.html missing or unreadable: {e}")
        return '<!doctype html><html><head><meta charset="utf-8"><title>SOLUSDT Monitor</title></head><body>dashboard.html missing</body></html>'


LOGIN_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SOLUSDT Monitor Login</title>
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f6fb;
  font-family:Arial,'Microsoft YaHei',sans-serif;color:#17202a}
.login{width:min(360px,calc(100vw - 32px));background:#fff;border:1px solid #dfe4ea;
  border-radius:8px;padding:28px;box-shadow:0 18px 45px rgba(31,45,61,.10)}
h1{font-size:20px;margin:0 0 20px}
label{display:block;font-size:13px;color:#57606f;margin-bottom:8px}
input{width:100%;height:44px;border:1px solid #cfd6e4;border-radius:6px;padding:0 12px;
  font-size:18px;outline:none}
input:focus{border-color:#3867d6;box-shadow:0 0 0 3px rgba(56,103,214,.12)}
button{width:100%;height:44px;margin-top:16px;border:0;border-radius:6px;background:#3867d6;
  color:#fff;font-size:15px;font-weight:700;cursor:pointer}
.error{margin:0 0 14px;color:#c0392b;font-size:13px;min-height:18px}
</style>
</head>
<body>
<form class="login" method="POST" action="/login">
  <h1>SOLUSDT Monitor</h1>
  <p class="error">{error}</p>
  <label for="password">访问密码</label>
  <input id="password" name="password" type="password" inputmode="numeric" autocomplete="current-password" autofocus>
  <button type="submit">进入</button>
</form>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    fetcher: DataFetcher = None
    dashboard_password = ""
    admin_password = ""
    sessions = {}
    access_log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "access_log.jsonl")
    access_lock = threading.Lock()
    geo = GeoResolver()

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    @staticmethod
    def _session_key(ip: str, ua: str) -> str:
        return f"{ip}|{ua[:100]}"

    @staticmethod
    def _access_ts(record: dict) -> float:
        value = record.get("updated_time") or record.get("time") or ""
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp()
        except Exception:
            return 0.0

    def _client_ip(self):
        cf_ip = self.headers.get("CF-Connecting-IP", "").strip()
        if cf_ip:
            return cf_ip
        xff = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if xff:
            return xff
        return self.client_address[0] if self.client_address else "-"

    def _access_log(self, event: str, status: int):
        ua = (self.headers.get("User-Agent", "-") or "-").replace("\n", " ")[:140]
        ip = self._client_ip()
        city = self.geo.lookup(ip) if self.geo else "未知"
        now = now_cst().strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "time": now,
            "updated_time": now,
            "ip": ip,
            "city": city,
            "ua": ua,
            "event": event,
            "status": status,
            "path": self.path.split("?", 1)[0],
            "session_key": self._session_key(ip, ua),
        }
        logger.info(f"ACCESS {event} ip={ip} city={city} path={self.path} status={status} ua=\"{ua}\"")
        try:
            with self.access_lock:
                rows = []
                records = []
                if os.path.exists(self.access_log_file):
                    with open(self.access_log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                records.append(json.loads(line))
                            except Exception:
                                pass
                key = record["session_key"]
                match = None
                for idx in range(len(records) - 1, -1, -1):
                    old = records[idx]
                    if old.get("session_key") == key or (old.get("ip") == ip and old.get("ua") == ua):
                        match = idx
                        break
                if match is None:
                    records.append(record)
                else:
                    old = records[match]
                    old.update({
                        "time": now,
                        "updated_time": now,
                        "ip": ip,
                        "city": city,
                        "ua": ua,
                        "event": event,
                        "status": status,
                        "path": record["path"],
                        "session_key": key,
                    })
                    records[match] = old
                rows = [json.dumps(r, ensure_ascii=False) + "\n" for r in records[-ACCESS_LOG_LIMIT:]]
                with open(self.access_log_file, "w", encoding="utf-8") as f:
                    f.writelines(rows)
        except Exception as e:
            logger.warning(f"ACCESS log write failed: {e}")

    def _new_session(self, role: str) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"role": role, "time": time.time()}
        return token

    def _session_role(self):
        if not self.dashboard_password:
            return "admin"
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "solusdt_auth":
                item = self.sessions.get(value)
                if item:
                    return item.get("role", "user")
        return None

    def _is_admin(self):
        return self._session_role() == "admin"

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            role = self._session_role()
            if not role:
                self._access_log("login_page", 200)
                self._login()
                return
            self._access_log(f"dashboard_{role}", 200)
            self._html(role)
        elif path == "/api/data":
            if not self._is_authenticated():
                self._unauthorized_api()
                return
            self._api()
        elif path == "/api/access-log":
            if not self._is_admin():
                self._unauthorized_api()
                return
            self._api_access_log()
        else:
            self._access_log("not_found", 404)
            self.send_response(404); self.end_headers()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/login":
            self.send_response(404); self.end_headers()
            return

        length = min(int(self.headers.get("Content-Length", "0") or 0), 4096)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        password = parse_qs(raw).get("password", [""])[0]
        role = None
        if secrets.compare_digest(password, str(self.admin_password)):
            role = "admin"
        elif secrets.compare_digest(password, str(self.dashboard_password)):
            role = "user"
        if role:
            token = self._new_session(role)
            self._access_log(f"login_success_{role}", 303)
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"solusdt_auth={token}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        self._access_log("login_failed", 200)
        self._login("密码不正确")

    def _is_authenticated(self):
        return self._session_role() is not None

    def _login(self, error=""):
        body = LOGIN_HTML.replace("{error}", error).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, role="user"):
        html = index_html().replace("__IS_ADMIN__", "true" if role == "admin" else "false")
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api(self):
        data = self.fetcher.get() if self.fetcher else {}
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_access_log(self):
        records = []
        try:
            with self.access_lock:
                if os.path.exists(self.access_log_file):
                    with open(self.access_log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                records.append(json.loads(line))
                            except Exception:
                                pass
            merged = {}
            order = []
            for item in records:
                if item.get("event") == "api_unauthorized":
                    continue
                key = item.get("session_key") or self._session_key(item.get("ip", ""), item.get("ua", ""))
                latest_time = item.get("updated_time") or item.get("time") or ""
                item = dict(item)
                item["session_key"] = key
                item["updated_time"] = latest_time
                item["time"] = latest_time
                if key not in merged:
                    order.append(key)
                    merged[key] = item
                else:
                    old = merged[key]
                    if self._access_ts(item) >= self._access_ts(old):
                        merged[key] = item
            records = [merged[k] for k in order]
            records.sort(key=self._access_ts, reverse=True)
            records = records[:100]
        except Exception as e:
            logger.warning(f"ACCESS log read failed: {e}")
        body = json.dumps({"records": records}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized_api(self):
        body = b'{"error":"unauthorized"}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


def bg_refresh(f: DataFetcher):
    while True:
        time.sleep(DASHBOARD_REFRESH_SECONDS)
        try:
            f.refresh()
        except Exception as e:
            logger.error(f"刷新失败: {e}")


def load_config(path: str = "config.json") -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    # Environment variables can also provide config values.
    cfg.setdefault("api_key", os.environ.get("BINANCE_API_KEY", ""))
    cfg.setdefault("secret", os.environ.get("BINANCE_SECRET", ""))
    cfg.setdefault("proxy", os.environ.get("BINANCE_PROXY", os.environ.get("HTTPS_PROXY", "")))
    cfg.setdefault("cf_worker", os.environ.get("CF_WORKER_URL", ""))
    cfg.setdefault("dashboard_password", os.environ.get("DASHBOARD_PASSWORD", ""))
    cfg.setdefault("dashboard_admin_password", os.environ.get("DASHBOARD_ADMIN_PASSWORD", ""))
    cfg.setdefault("ip2region_db", os.environ.get("IP2REGION_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ip2region_v4.xdb")))
    cfg.setdefault("usd_cny_rate", 6.79538)
    cfg.setdefault("initial_equity_cny", 168)
    cfg.setdefault("initial_equity", 0)
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
    ak = cfg.get("api_key", "")
    sk = cfg.get("secret", "")
    proxy = cfg.get("proxy", "")
    cf_worker = resolve_cf_worker() or cfg.get("cf_worker", "")
    Handler.dashboard_password = str(cfg.get("dashboard_password", ""))
    Handler.admin_password = str(cfg.get("dashboard_admin_password", ""))
    Handler.geo = GeoResolver(str(cfg.get("ip2region_db", "")))

    # Start the HTTP server first so the page is immediately available.
    server = MonitorServer(("0.0.0.0", PORT), Handler)
    print(f"\n{'='*50}")
    print(f"  仪表盘: http://localhost:{PORT}")
    if cf_worker:
        print(f"  CF 中继: {cf_worker}")
    elif proxy:
        print(f"  代理: {proxy}")
    print(f"  Ctrl+C 停止")
    print(f"{'='*50}\n")

    # Initialize data fetcher in background.
    def init_fetcher():
        if not ak or not sk:
            logger.warning("API keys are not configured; monitor will use offline mode")
            return
        f = None
        while True:
            try:
                if f is None:
                    f = DataFetcher(ak, sk, proxy, cfg, cf_worker)
                    Handler.fetcher = f
                f.refresh()
            except Exception as e:
                logger.error(f"fetcher loop failed: {e}")
            time.sleep(DASHBOARD_REFRESH_SECONDS)

    threading.Thread(target=init_fetcher, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        server.shutdown()


if __name__ == "__main__":
    try:
        import ccxt
    except ImportError:
        print("pip install ccxt")
        sys.exit(1)
    main()

