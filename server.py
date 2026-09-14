#!/usr/bin/env python3
"""Local server: serves the web UI + proxies GitHub API (dispatch & real machine info).

Multi-account:
  - Dùng file .accounts.json để khai báo danh sách tài khoản:
      [ {"name": "gaylo805-code", "token": "ghp_..."},
        {"name": "bot2",          "token": "ghp_..."} ]
  - Nếu không có file, dùng token trong .token (tài khoản gaylo805-code).
  - Repo của từng tài khoản: <account>/Pc (windows) và <account>/Cc (ubuntu).
"""
import io
import json
import os
import re
import sys
import subprocess
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE, ".token")
ACCOUNTS_FILE = os.path.join(BASE, ".accounts.json")

API = "https://api.github.com"

TYPES = {
    "windows": {
        "repo_name": "Pc", "branch": "main", "workflow": "main.yml",
        "label": "Windows 2022", "icon": "🖥️", "slot": "rdp",
    },
    "ubuntu": {
        "repo_name": "Cc", "branch": "main", "workflow": "main.yml",
        "label": "Ubuntu 24.04", "icon": "🐧", "slot": "ssh",
    },
}
DEFAULT_TYPE = "windows"

# Thứ tự ưu tiên tìm IP trong log của workflow
_IP_PATTERNS = (
    r"Đã nhận IP[:：]*\s*(\d{1,3}(?:\.\d{1,3}){3})",
    r"ZeroTier IP[\s:：]*(\d{1,3}(?:\.\d{1,3}){3})",
    r"IP[:：]\s*(\d{1,3}(?:\.\d{1,3}){3})",
)


def _load_accounts():
    if os.path.isfile(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE) as f:
                arr = json.load(f)
            accs = [{"name": a["name"], "token": a.get("token", "").strip()} for a in arr if a.get("name")]
            if accs:
                return accs
        except Exception:
            pass
    tok = os.environ.get("GITHUB_TOKEN", "")
    if not tok and os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            tok = f.read().strip()
    if not tok:
        print("Thiếu token: đặt GITHUB_TOKEN, tạo file .token hoặc .accounts.json", file=sys.stderr)
        sys.exit(1)
    return [{"name": "gaylo805-code", "token": tok}]


ACCOUNTS = _load_accounts()
DEFAULT_ACC = ACCOUNTS[0]["name"]
TOKEN = ACCOUNTS[0]["token"]


def acc_token(acc):
    for a in ACCOUNTS:
        if a["name"] == acc:
            return a["token"]
    return TOKEN


def acc_repo(cfg, acc):
    return f"{acc}/{cfg['repo_name']}"


def gh(method, path, body=None, raw=False, token=None):
    tok = token or TOKEN
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            if raw:
                return r.status, data
            return r.status, json.loads(data or b"{}")
    except urllib.error.HTTPError as e:
        raw_body = e.read()
        if raw:
            return e.code, raw_body
        try:
            return e.code, json.loads(raw_body or b"{}")
        except Exception:
            return e.code, {"message": f"HTTP {e.code}"}
    except Exception as e:
        if raw:
            return 0, b""
        return 0, {"message": str(e)}


def latest_run(cfg, acc):
    repo = acc_repo(cfg, acc)
    st, runs = gh("GET", f"/repos/{repo}/actions/workflows/{cfg['workflow']}/runs?event=workflow_dispatch&per_page=5", token=acc_token(acc))
    if st != 200 or not runs.get("workflow_runs"):
        return None, None
    return runs["workflow_runs"][0]["id"], runs["workflow_runs"][0]


def download_artifact(art_id, cfg, acc):
    url = f"{API}/repos/{acc_repo(cfg, acc)}/actions/artifacts/{art_id}/zip"
    cmd = ["curl", "-sL", "-m", "120",
           "-H", "Authorization: Bearer " + acc_token(acc),
           "-H", "Accept: application/vnd.github+json", url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=130)
    except Exception:
        return 0, b""
    if p.returncode != 0 or not p.stdout:
        return 0, b""
    return 200, p.stdout


def connect_info(run_id, cfg, acc):
    st, arts = gh("GET", f"/repos/{acc_repo(cfg, acc)}/actions/runs/{run_id}/artifacts", token=acc_token(acc))
    if st != 200:
        return None
    for a in arts.get("artifacts", []):
        if a["name"] == "connection-info":
            try:
                st2, blob = download_artifact(a["id"], cfg, acc)
            except Exception:
                continue
            if st2 == 200:
                zf = zipfile.ZipFile(io.BytesIO(blob))
                for name in zf.namelist():
                    if name.endswith("connection.json"):
                        try:
                            return json.loads(zf.read(name).decode("utf-8", "ignore"))
                        except Exception:
                            return None
                return None
    return None


def live_info(cfg, acc):
    try:
        st, d = gh("GET", f"/repos/{acc_repo(cfg, acc)}/contents/connection.json?ref=runner-status", token=acc_token(acc))
        if st != 200 or not d.get("content"):
            return None
        import base64
        raw = base64.b64decode(d["content"]).decode("utf-8", "ignore")
        return json.loads(raw)
    except Exception:
        return None


def run_logs(run_id, cfg, acc):
    """Tải toàn bộ log của run — trong log workflow in sẵn IP ZeroTier."""
    st, jobs = gh("GET", f"/repos/{acc_repo(cfg, acc)}/actions/runs/{run_id}/jobs?per_page=50", token=acc_token(acc))
    if st != 200:
        return ""
    for j in jobs.get("jobs", []):
        url = f"{API}/repos/{acc_repo(cfg, acc)}/actions/jobs/{j['id']}/logs"
        cmd = ["curl", "-sL", "-m", "120", "-H", "Authorization: Bearer " + acc_token(acc), url]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=130)
        except Exception:
            continue
        if p.returncode == 0 and p.stdout:
            return p.stdout.decode("utf-8", "ignore")
    return ""


def ip_from_log(text):
    if not text:
        return None
    for pat in _IP_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def gather(t=None, run_id=None, acc=None):
    t = t if t in TYPES else DEFAULT_TYPE
    acc = acc if any(a["name"] == acc for a in ACCOUNTS) else DEFAULT_ACC
    cfg = TYPES[t]
    rid, r = latest_run(cfg, acc)
    run_id = run_id or rid
    active = r is not None and r["status"] == "in_progress"
    artifact = connect_info(run_id, cfg, acc) if active and run_id else None
    live = live_info(cfg, acc) if active else None
    info = (live if live else artifact) or {}
    info = dict(info)

    log = run_logs(run_id, cfg, acc) if active and run_id else ""
    real_ip = info.get("zt_ip") or ""
    log_found = ip_from_log(log)
    if log_found:
        real_ip = log_found
    info["zt_ip"] = real_ip
    info["discovered_by"] = "log" if real_ip else (info.get("discovered_by") or "artifact")

    repo = acc_repo(cfg, acc)
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if run_id else None
    if not active:
        info = None
    return {
        "type": t,
        "account": acc,
        "cfg": {"repo": repo, "label": cfg["label"], "icon": cfg["icon"], "slot": cfg["slot"]},
        "run_id": run_id,
        "run_url": run_url,
        "run_status": r["status"] if r else None,
        "run_conclusion": r.get("conclusion") if r else None,
        "info": info or None,
        "artifact_found": artifact is not None,
        "live_found": live is not None,
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _no_route(self):
        self._json({"error": "Not found"}, 404)

    def _qs(self):
        import urllib.parse
        q = self.path.split("?", 1)
        return urllib.parse.parse_qs(q[1]) if len(q) > 1 else {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(BASE, "index.html"), "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            except FileNotFoundError:
                self._json({"error": "index.html chưa tồn tại"}, 404)
                return
        if path == "/api/status":
            qs = self._qs()
            t = qs.get("type", [DEFAULT_TYPE])[0]
            acc = qs.get("account", [None])[0] or None
            run_id = qs.get("run_id", [None])[0]
            try:
                run_id = int(run_id) if run_id else None
            except ValueError:
                run_id = None
            self._json(gather(t, run_id, acc))
            return
        if path == "/api/types":
            self._json({k: {"label": v["label"], "icon": v["icon"], "slot": v["slot"]} for k, v in TYPES.items()})
            return
        if path == "/api/accounts":
            self._json([{"name": a["name"]} for a in ACCOUNTS])
            return
        self._no_route()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/create":
            length = int(self.headers.get("Content-Length", 0))
            if length:
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    body = {}
            else:
                body = {}
            t = body.get("type", DEFAULT_TYPE)
            acc = body.get("account")
            if not acc or not any(a["name"] == acc for a in ACCOUNTS):
                self._json({"ok": False, "message": "Tài khoản không hợp lệ"}, 400)
                return
            cfg = TYPES.get(t)
            if not cfg:
                self._json({"ok": False, "message": f"Loại máy '{t}' không hợp lệ"}, 400)
                return
            repo = acc_repo(cfg, acc)
            st, _ = gh("POST", f"/repos/{repo}/actions/workflows/{cfg['workflow']}/dispatches",
                       body={"ref": cfg["branch"]}, token=acc_token(acc))
            if st == 204:
                self._json({"ok": True, "type": t, "account": acc, "message": "Đã kích hoạt tạo máy",
                            "run_url": f"https://github.com/{repo}/actions"})
            else:
                self._json({"ok": False, "status": st, "message": "Dispatch thất bại (kiểm tra token/tên repo của tài khoản)"}, 500)
            return
        self._no_route()

    def log_message(self, *a):
        pass


def main():
    port = int(os.environ.get("PORT", 8080))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"🚀 Web + GitHub API: http://localhost:{port}")
    print(f"   Accounts: {', '.join(a['name'] for a in ACCOUNTS)}")
    for k, cfg in TYPES.items():
        print(f"   [{k}] {cfg['label']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nDừng server.")


if __name__ == "__main__":
    main()