#!/usr/bin/env python3
"""Local server: serves the demo web + proxies GitHub API (dispatch & real machine info)."""
import io
import json
import os
import sys
import subprocess
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE, ".token")

TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not TOKEN and os.path.isfile(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        TOKEN = f.read().strip()
if not TOKEN:
    print("Thiếu token: đặt GITHUB_TOKEN hoặc tạo file .token", file=sys.stderr)
    sys.exit(1)

REPO = os.environ.get("REPO", "gaylo805-code/Pc")
BRANCH = os.environ.get("BRANCH", "main")
WORKFLOW = "main.yml"
API = "https://api.github.com"


def gh(method, path, body=None, raw=False):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
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


def latest_run():
    st, runs = gh("GET", f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=5")
    if st != 200 or not runs.get("workflow_runs"):
        return None, None
    for r in runs["workflow_runs"]:
        if r["status"] != "completed" or r.get("conclusion") != "success" or True:
            return r["id"], r
    return None, None


def download_artifact(art_id):
    url = f"{API}/repos/{REPO}/actions/artifacts/{art_id}/zip"
    cmd = ["curl", "-sL", "-m", "120",
           "-H", "Authorization: Bearer " + TOKEN,
           "-H", "Accept: application/vnd.github+json", url]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=130)
    except Exception:
        return 0, b""
    if p.returncode != 0 or not p.stdout:
        return 0, b""
    return 200, p.stdout


def connect_info(run_id):
    st, arts = gh("GET", f"/repos/{REPO}/actions/runs/{run_id}/artifacts")
    if st != 200:
        return None
    for a in arts.get("artifacts", []):
        if a["name"] == "connection-info":
            try:
                st2, blob = download_artifact(a["id"])
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


def live_info():
    try:
        st, d = gh("GET", f"/repos/{REPO}/contents/connection.json?ref=runner-status")
        if st != 200 or not d.get("content"):
            return None
        import base64
        raw = base64.b64decode(d["content"]).decode("utf-8", "ignore")
        return json.loads(raw)
    except Exception:
        return None


def gather(run_id=None):
    rid, r = latest_run()
    run_id = run_id or rid
    artifact = connect_info(run_id) if run_id else None
    live = live_info()
    info = live if live else artifact
    run_url = f"https://github.com/{REPO}/actions/runs/{run_id}" if run_id else None
    return {
        "repo": REPO,
        "run_id": run_id,
        "run_url": run_url,
        "run_status": r["status"] if r else None,
        "run_conclusion": r.get("conclusion") if r else None,
        "info": info,
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

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
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
            import urllib.parse
            qs = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            run_id = qs.get("run_id", [None])[0]
            if run_id:
                try:
                    run_id = int(run_id)
                except ValueError:
                    run_id = None
            self._json(gather(run_id))
            return
        self._no_route()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/create":
            st, _ = gh("POST", f"/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches", body={"ref": BRANCH})
            if st == 204:
                self._json({"ok": True, "message": "Đã kích hoạt tạo máy", "run_url": f"https://github.com/{REPO}/actions"})
            else:
                self._json({"ok": False, "status": st, "message": "Dispatch thất bại"}, 500)
            return
        self._no_route()

    def log_message(self, *a):
        pass


def main():
    port = int(os.environ.get("PORT", 8080))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"🚀 Web + GitHub API: http://localhost:{port}")
    print(f"   Repo: {REPO} | branch: {BRANCH} | workflow: {WORKFLOW}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nDừng server.")


if __name__ == "__main__":
    main()