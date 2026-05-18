"""
SOLUSDT 交易系统 - 启动控制面板

普通模式:
    python launcher.py
    子进程隐藏运行，控制面板显示日志。

调试模式:
    python launcher.py --debug
    为交易机器人和监控仪表盘各打开一个可见终端，控制面板负责启动/停止/重启。
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_PORT = 8888


def parse_args():
    parser = argparse.ArgumentParser(description="SOLUSDT 启动控制面板")
    parser.add_argument("--debug", action="store_true", help="使用两个可见终端启动 bot 和 monitor")
    parser.add_argument("--no-auto-start", action="store_true", help="只打开控制面板，不自动启动子进程")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--panel-port", type=int, default=8890, help="控制面板端口，默认 8890")
    args, _ = parser.parse_known_args()
    return args


ARGS = parse_args()
PANEL_PORT = ARGS.panel_port
DEBUG_MODE = ARGS.debug or os.environ.get("LAUNCHER_DEBUG", "").lower() in {"1", "true", "yes", "on"}

PROCESS_DEFS = {
    "bot": {
        "label": "交易机器人",
        "script": "MainProgramme.py",
        "title": "SOLUSDT Bot",
        "port": None,
    },
    "monitor": {
        "label": "监控仪表盘",
        "script": "monitor.py",
        "title": "SOLUSDT Monitor",
        "port": DASHBOARD_PORT,
    },
}

process_state = {
    name: {"proc": None, "started_at": None, "mode": None, "last_msg": "未启动", "port": PROCESS_DEFS[name]["port"]}
    for name in PROCESS_DEFS
}
bot_log = []
mon_log = []
log_lists = {"bot": bot_log, "monitor": mon_log}
_process_snapshot_cache = {"time": 0.0, "items": []}


def port_in_use(port):
    """检查本机端口是否被占用。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def find_free_port(start_port):
    """从 start_port 起找一个本机可用端口，跳过控制面板端口。"""
    port = start_port
    while port < start_port + 50:
        if port != PANEL_PORT and not port_in_use(port):
            return port
        port += 1
    raise RuntimeError(f"未找到可用端口: {start_port}-{start_port + 49}")


def _append_log(name, line):
    log_list = log_lists[name]
    log_list.append(line)
    if len(log_list) > 80:
        del log_list[:-80]


def _read_log(name, proc):
    try:
        if not proc.stdout:
            return
        for line in proc.stdout:
            _append_log(name, line.rstrip())
    except Exception as exc:
        _append_log(name, f"[launcher] 读取日志失败: {exc}")


def _cmd_quote(value):
    return subprocess.list2cmdline([value])


def _new_process_env(name=None):
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if name == "monitor" and process_state[name]["port"]:
        env["SOLUSDT_MONITOR_PORT"] = str(process_state[name]["port"])
    return env


def _build_command(name, script_path):
    args = [sys.executable, "-u", script_path]
    if name == "monitor" and process_state[name]["port"]:
        args += ["--port", str(process_state[name]["port"])]

    if DEBUG_MODE and sys.platform == "win32":
        meta = PROCESS_DEFS[name]
        python_cmd = subprocess.list2cmdline(args)
        cmd = (
            f"title {meta['title']} DEBUG & "
            "chcp 65001 >nul & "
            f"cd /d {_cmd_quote(SCRIPT_DIR)} & "
            f"{python_cmd} & "
            "echo. & "
            f"echo [launcher] {meta['label']} 程序已退出，退出码 %errorlevel%。& "
            "echo [launcher] 关闭此窗口，或回到控制面板点击停止/重启。"
        )
        return ["cmd.exe", "/d", "/k", cmd]
    return args


def _creation_flags():
    if sys.platform != "win32":
        return 0
    if DEBUG_MODE:
        return subprocess.CREATE_NEW_CONSOLE
    return subprocess.CREATE_NO_WINDOW


def _is_running(name):
    proc = process_state[name]["proc"]
    return proc is not None and proc.poll() is None


def _mark_exited_processes():
    for name, info in process_state.items():
        proc = info["proc"]
        if proc is not None and proc.poll() is not None:
            code = proc.returncode
            info["last_msg"] = f"已退出，退出码 {code}"
            info["proc"] = None
            _append_log(name, f"[launcher] {PROCESS_DEFS[name]['label']} 已退出，退出码 {code}")


def _script_process_snapshot():
    """只读扫描同名脚本进程，用来避免重复启动交易机器人。"""
    if sys.platform != "win32":
        return []

    now = time.time()
    if now - _process_snapshot_cache["time"] < 3:
        return list(_process_snapshot_cache["items"])

    patterns = "|".join(meta["script"].replace(".", r"\.") for meta in PROCESS_DEFS.values())
    ps_cmd = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{$_.CommandLine -match '{patterns}'}} | "
        "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        raw = proc.stdout.strip()
        if not raw:
            items = []
        else:
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
    except Exception:
        items = []

    current_pid = os.getpid()
    cleaned = []
    for item in items:
        try:
            pid = int(item.get("ProcessId"))
        except Exception:
            continue
        if pid == current_pid:
            continue
        cleaned.append({"pid": pid, "cmd": item.get("CommandLine") or ""})

    _process_snapshot_cache.update(time=now, items=cleaned)
    return list(cleaned)


def external_script_processes(name):
    script = PROCESS_DEFS[name]["script"].lower()
    owned_pid = process_state[name]["proc"].pid if _is_running(name) else None
    matches = []
    for item in _script_process_snapshot():
        if script not in item["cmd"].lower():
            continue
        if owned_pid and item["pid"] == owned_pid:
            continue
        matches.append(item)
    return matches


def start_process(name):
    if name not in PROCESS_DEFS:
        raise ValueError(f"未知进程: {name}")

    _mark_exited_processes()
    if _is_running(name):
        pid = process_state[name]["proc"].pid
        return f"{PROCESS_DEFS[name]['label']} 已在运行 (PID {pid})"

    meta = PROCESS_DEFS[name]
    if name == "bot":
        external = external_script_processes(name)
        if external:
            pid_list = ", ".join(str(item["pid"]) for item in external[:3])
            msg = f"发现外部交易机器人 PID {pid_list}，为避免重复交易，未启动新的 bot"
            process_state[name]["last_msg"] = msg
            _append_log(name, f"[launcher] {msg}")
            return msg

    if name == "monitor":
        desired_port = PROCESS_DEFS[name]["port"] or DASHBOARD_PORT
        actual_port = find_free_port(desired_port)
        process_state[name]["port"] = actual_port
        if actual_port != desired_port:
            msg = f"端口 {desired_port} 已被占用，改用 {actual_port} 启动仪表盘"
            process_state[name]["last_msg"] = msg
            _append_log(name, f"[launcher] {msg}")

    script_path = os.path.join(SCRIPT_DIR, meta["script"])
    if not os.path.exists(script_path):
        raise FileNotFoundError(script_path)

    log_lists[name].clear()
    cmd = _build_command(name, script_path)
    stdout = None if DEBUG_MODE else subprocess.PIPE
    stderr = None if DEBUG_MODE else subprocess.STDOUT
    stdin = None if DEBUG_MODE else subprocess.DEVNULL

    proc = subprocess.Popen(
        cmd,
        cwd=SCRIPT_DIR,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_new_process_env(name),
        creationflags=_creation_flags(),
    )

    mode = "debug-terminal" if DEBUG_MODE else "hidden"
    process_state[name].update(
        proc=proc,
        started_at=datetime.now(),
        mode=mode,
        last_msg=(
            f"已启动 (PID {proc.pid}, {mode}, 端口 {process_state[name]['port']})"
            if name == "monitor"
            else f"已启动 (PID {proc.pid}, {mode})"
        ),
    )
    _append_log(name, f"[launcher] {meta['label']} 已启动 (PID {proc.pid}, {mode})")

    if not DEBUG_MODE:
        threading.Thread(target=_read_log, args=(name, proc), daemon=True).start()

    return process_state[name]["last_msg"]


def _kill_process_tree(proc):
    if not proc or proc.poll() is not None:
        return

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return
        except Exception:
            pass

    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def stop_process(name):
    if name not in PROCESS_DEFS:
        raise ValueError(f"未知进程: {name}")

    _mark_exited_processes()
    proc = process_state[name]["proc"]
    if not proc or proc.poll() is not None:
        process_state[name].update(proc=None, last_msg="未由 launcher 启动或已经停止")
        return process_state[name]["last_msg"]

    pid = proc.pid
    _kill_process_tree(proc)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass

    process_state[name].update(proc=None, started_at=None, last_msg=f"已停止 (PID {pid})")
    _append_log(name, f"[launcher] {PROCESS_DEFS[name]['label']} 已停止 (PID {pid})")
    return process_state[name]["last_msg"]


def restart_process(name):
    stop_process(name)
    time.sleep(0.8)
    return start_process(name)


def start_all():
    messages = [start_process("bot")]
    time.sleep(1.5)
    messages.append(start_process("monitor"))
    return messages


def stop_all():
    # 先停机器人，避免停止期间继续下单；监控随后关闭。
    return [stop_process("bot"), stop_process("monitor")]


def restart_all():
    stop_all()
    time.sleep(1)
    return start_all()


def _tail_file(path, limit=20):
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [line.rstrip() for line in f.readlines()[-limit:]]
    except Exception:
        return []


def get_display_log(name):
    launcher_lines = log_lists[name][-20:]

    if name == "bot" and DEBUG_MODE:
        file_lines = _tail_file(os.path.join(SCRIPT_DIR, "trading_bot.log"), 20)
        if file_lines:
            return "\n".join((launcher_lines[-5:] + file_lines)[-25:])

    if launcher_lines:
        return "\n".join(launcher_lines)

    if name == "bot":
        file_lines = _tail_file(os.path.join(SCRIPT_DIR, "trading_bot.log"), 20)
        if file_lines:
            return "\n".join(file_lines)

    if DEBUG_MODE:
        return "调试模式下请查看对应的终端窗口。"
    return "(暂无日志)"


def current_dashboard_port():
    if _is_running("monitor"):
        return process_state["monitor"]["port"] or DASHBOARD_PORT
    if port_in_use(DASHBOARD_PORT):
        return DASHBOARD_PORT
    return process_state["monitor"]["port"] or DASHBOARD_PORT


def check_status():
    _mark_exited_processes()
    result = {"mode": "debug" if DEBUG_MODE else "normal"}

    for name, meta in PROCESS_DEFS.items():
        proc = process_state[name]["proc"]
        running = proc is not None and proc.poll() is None
        status = "running" if running else "stopped"
        external = [] if running else external_script_processes(name)

        if name == "bot" and external:
            status = "external"
            pid_list = ", ".join(str(item["pid"]) for item in external[:3])
            process_state[name]["last_msg"] = f"发现外部交易机器人 PID {pid_list}"
        elif not running and meta["port"] and port_in_use(meta["port"]):
            status = "external"
            process_state[name]["last_msg"] = f"端口 {meta['port']} 被其他进程占用"

        result[name] = status
        result[f"{name}_pid"] = proc.pid if running else (external[0]["pid"] if external else None)
        result[f"{name}_mode"] = process_state[name]["mode"] or ("debug-terminal" if DEBUG_MODE else "hidden")
        result[f"{name}_msg"] = process_state[name]["last_msg"]
        result[f"{name}_port"] = process_state[name]["port"]
        result[f"{name}_started_at"] = (
            process_state[name]["started_at"].strftime("%H:%M:%S")
            if process_state[name]["started_at"]
            else ""
        )

    result["dashboard_port"] = current_dashboard_port()
    result["dashboard_url"] = f"http://localhost:{current_dashboard_port()}"
    return result


# ============================================================
PANEL_HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SOLUSDT 控制面板</title>
<style>
  :root{--bg:#f0f2f5;--card:#fff;--border:#d9dce2;--text:#2d3436;--sub:#747d8c;
        --green:#20bf6b;--red:#eb3b5a;--blue:#3867d6;--yellow:#f7b731}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;
       min-height:100vh;padding:20px}
  .header{text-align:center;padding:18px;margin-bottom:12px}
  .header h1{font-size:24px;color:var(--blue)}
  .header p{color:var(--sub);font-size:13px;margin-top:6px}
  .badge{display:inline-block;border:1px solid var(--border);border-radius:999px;padding:2px 10px;
         margin-left:8px;font-size:12px;color:var(--sub);background:#fff}
  .badge.debug{border-color:var(--yellow);color:#9a6b00;background:#fff8e1}

  .toolbar{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:16px}
  .status-row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}
  @media(max-width:760px){.status-row{grid-template-columns:1fr}}
  .status-card{background:var(--card);border:1px solid var(--border);border-radius:8px;
               padding:18px 22px;display:flex;align-items:center;justify-content:space-between;gap:14px;
               box-shadow:0 2px 8px rgba(0,0,0,.04)}
  .status-card .info h3{font-size:16px;margin-bottom:5px;display:flex;align-items:center;gap:8px}
  .status-card .info p{font-size:12px;color:var(--sub);line-height:1.6}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%}
  .dot.running{background:var(--green);animation:pulse 1.5s infinite}
  .dot.stopped{background:var(--red)}
  .dot.external{background:var(--yellow)}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

  .btn-row{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
  button{padding:8px 14px;border:1px solid var(--border);border-radius:8px;font-size:13px;
         font-weight:600;cursor:pointer;transition:all .15s;background:var(--card);color:var(--text)}
  .btn-start{border-color:var(--green);color:var(--green)}
  .btn-start:hover{background:var(--green);color:#fff}
  .btn-stop{border-color:var(--red);color:var(--red)}
  .btn-stop:hover{background:var(--red);color:#fff}
  .btn-main{border-color:var(--blue);color:var(--blue)}
  .btn-main:hover{background:var(--blue);color:#fff}
  button:disabled{opacity:.35;cursor:not-allowed}
  button:disabled:hover{background:var(--card);color:inherit}

  .log-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}
  @media(max-width:900px){.log-grid{grid-template-columns:1fr}}
  .log-panel{background:var(--card);border:1px solid var(--border);border-radius:8px;
             padding:16px 20px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
  .log-panel h3{font-size:14px;margin-bottom:10px;color:var(--blue)}
  .log-lines{background:#f7f8fa;border:1px solid var(--border);border-radius:8px;
             padding:10px 14px;font-family:'Consolas','Courier New',monospace;font-size:12px;
             height:180px;overflow-y:auto;white-space:pre-wrap;line-height:1.6;color:#2d3436}

  .iframe-wrap{background:var(--card);border:1px solid var(--border);border-radius:8px;
               overflow:hidden;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
  .iframe-wrap h3{padding:16px 20px 0;font-size:14px;color:var(--blue)}
  .iframe-hint{padding:4px 20px 12px;font-size:12px;color:var(--sub)}
  iframe{width:100%;height:calc(100vh - 60px);border:none;min-height:700px}

  .footer{text-align:center;padding:12px;color:#b2bec3;font-size:11px}
</style>
</head>
<body>

<div class="header">
  <h1>SOLUSDT 交易系统 · 控制面板 <span class="badge" id="modeBadge">--</span></h1>
  <p id="panelTime">--</p>
</div>

<div class="toolbar">
  <button class="btn-start" onclick="action('all','start')">全部启动</button>
  <button class="btn-stop" onclick="action('all','stop')">全部停止</button>
  <button class="btn-main" onclick="action('all','restart')">全部重启</button>
  <button class="btn-main" onclick="window.open(dashboardUrl,'_blank')" id="openDashboardBtn">打开仪表盘</button>
</div>

<div class="status-row">
  <div class="status-card">
    <div class="info">
      <h3><span class="dot" id="botDot"></span>交易机器人</h3>
      <p id="botStatus">--</p>
      <p id="botDetail">--</p>
    </div>
    <div class="btn-row">
      <button class="btn-start" onclick="action('bot','start')" id="botStartBtn">启动</button>
      <button class="btn-stop" onclick="action('bot','stop')" id="botStopBtn">停止</button>
      <button class="btn-main" onclick="action('bot','restart')" id="botRestartBtn">重启</button>
    </div>
  </div>
  <div class="status-card">
    <div class="info">
      <h3><span class="dot" id="monDot"></span>监控仪表盘</h3>
      <p id="monStatus">--</p>
      <p id="monDetail">--</p>
    </div>
    <div class="btn-row">
      <button class="btn-start" onclick="action('monitor','start')" id="monStartBtn">启动</button>
      <button class="btn-stop" onclick="action('monitor','stop')" id="monStopBtn">停止</button>
      <button class="btn-main" onclick="action('monitor','restart')" id="monRestartBtn">重启</button>
    </div>
  </div>
</div>

<div class="log-grid">
  <div class="log-panel">
    <h3>机器人日志</h3>
    <div class="log-lines" id="botLog">等待启动...</div>
  </div>
  <div class="log-panel">
    <h3>监控日志</h3>
    <div class="log-lines" id="monLog">等待启动...</div>
  </div>
</div>

<div class="iframe-wrap">
  <h3>仪表盘</h3>
  <div class="iframe-hint">如仪表盘尚未启动，此处显示空白；调试模式下也可以直接看 Monitor 终端。</div>
  <iframe src="about:blank" id="dashboardFrame"></iframe>
</div>

<div class="footer">SOLUSDT Launcher · 面板端口 8890 · 仪表盘端口 <span id="dashboardPort">--</span> · <span id="footerTime">--</span></div>

<script>
let dashboardUrl='http://localhost:8888';

async function fetchStatus(){
  try{
    const r=await fetch('/api/status');const d=await r.json();
    updateUI(d);
    document.getElementById('botLog').textContent=d.bot_log||'(暂无日志)';
    document.getElementById('monLog').textContent=d.mon_log||'(暂无日志)';
    document.getElementById('panelTime').textContent='更新 '+d.time;
    document.getElementById('footerTime').textContent=d.time;
  }catch(e){console.error(e)}
}

function statusText(s){
  if(s==='running')return '运行中';
  if(s==='external')return '外部进程运行中';
  return '已停止';
}

function updateCard(d,name,prefix){
  const s=d[name];
  const dot=document.getElementById(prefix+'Dot');
  dot.className='dot '+(s==='running'?'running':(s==='external'?'external':'stopped'));
  document.getElementById(prefix+'Status').textContent=statusText(s);
  const pid=d[name+'_pid']?'PID '+d[name+'_pid']:'无 PID';
  const started=d[name+'_started_at']?' · 启动 '+d[name+'_started_at']:'';
  document.getElementById(prefix+'Detail').textContent=pid+' · '+d[name+'_mode']+started+' · '+(d[name+'_msg']||'');

  const externalBot=name==='bot'&&s==='external';
  document.getElementById(prefix+'StartBtn').disabled=(s==='running'||externalBot);
  document.getElementById(prefix+'StopBtn').disabled=(s!=='running');
  document.getElementById(prefix+'RestartBtn').disabled=externalBot;
}

function updateUI(d){
  dashboardUrl=d.dashboard_url||'http://localhost:8888';
  document.getElementById('dashboardPort').textContent=d.dashboard_port||'8888';
  const frame=document.getElementById('dashboardFrame');
  if(frame.src!==dashboardUrl+'/'){
    frame.src=dashboardUrl;
  }
  const badge=document.getElementById('modeBadge');
  badge.textContent=d.mode==='debug'?'DEBUG 终端模式':'普通托管模式';
  badge.className='badge '+(d.mode==='debug'?'debug':'');
  updateCard(d,'bot','bot');
  updateCard(d,'monitor','mon');
}

async function action(name,cmd){
  try{
    const path=name==='all'?('/api/all/'+cmd):('/api/'+name+'/'+cmd);
    const r=await fetch(path,{method:'POST'});
    const d=await r.json();
    if(!d.ok){alert('操作失败: '+(d.error||'未知错误'))}
    if(name==='monitor'||name==='all'){
      setTimeout(fetchStatus,1200);
    }
    setTimeout(fetchStatus,500);
  }catch(e){alert('操作失败: '+e)}
}

fetchStatus();
setInterval(fetchStatus,3000);
</script>
</body>
</html>'''


class PanelHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_html()
        elif self.path == "/api/status":
            st = check_status()
            self._json({
                **st,
                "bot_log": get_display_log("bot"),
                "mon_log": get_display_log("monitor"),
                "time": datetime.now().strftime("%H:%M:%S"),
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.rstrip("/")
        try:
            if path == "/api/all/start":
                msg = start_all()
            elif path == "/api/all/stop":
                msg = stop_all()
            elif path == "/api/all/restart":
                msg = restart_all()
            elif path == "/api/bot/start":
                msg = start_process("bot")
            elif path == "/api/bot/stop":
                msg = stop_process("bot")
            elif path == "/api/bot/restart":
                msg = restart_process("bot")
            elif path == "/api/monitor/start":
                msg = start_process("monitor")
            elif path == "/api/monitor/stop":
                msg = stop_process("monitor")
            elif path == "/api/monitor/restart":
                msg = restart_process("monitor")
            else:
                self._json({"ok": False, "error": "unknown path"})
                return
            self._json({"ok": True, "msg": msg})
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)})

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(PANEL_HTML.encode("utf-8"))

    def log_message(self, fmt, *args):
        pass


class PanelServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def print_banner():
    mode = "DEBUG 终端模式" if DEBUG_MODE else "普通托管模式"
    print("=" * 60)
    print("  SOLUSDT 交易系统 · 启动控制面板")
    print(f"  模式:     {mode}")
    print(f"  控制面板: http://localhost:{PANEL_PORT}")
    print(f"  仪表盘:   http://localhost:{DASHBOARD_PORT}")
    print("=" * 60)


def main():
    print_banner()

    if port_in_use(PANEL_PORT):
        print(f"\n!! 端口 {PANEL_PORT} 已被占用，可能已有另一个控制面板运行中")
        print(f"   直接打开浏览器: http://localhost:{PANEL_PORT}")
        if not ARGS.no_browser:
            webbrowser.open(f"http://localhost:{PANEL_PORT}")
        return

    server = PanelServer(("127.0.0.1", PANEL_PORT), PanelHandler)

    if not ARGS.no_auto_start:
        print("\n自动启动交易机器人和监控仪表盘...")
        for line in start_all():
            print(f"  {line}")
    else:
        print("\n已启用 --no-auto-start，仅启动控制面板。")

    if not ARGS.no_browser:
        print(f"\n打开控制面板: http://localhost:{PANEL_PORT}")
        webbrowser.open(f"http://localhost:{PANEL_PORT}")

    print("控制面板服务已启动，按 Ctrl+C 停止 launcher 和由它启动的子进程\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止所有服务...")
    finally:
        stop_all()
        server.server_close()
        print("已全部停止。再见。")


if __name__ == "__main__":
    main()
