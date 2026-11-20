#!/usr/bin/env python3
# map_editor.py —— 地图编辑器的本地服务器（纯标准库，无第三方依赖）。
# 用法：python map_editor.py [--port 8765]
# 然后浏览器打开 http://127.0.0.1:8765
import argparse
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # workspace/（maps/、levels/、editor/ 都在这一层）


def safe_file(name):
    """把 file 参数解析成 workspace 内 maps/ 或 levels/ 下的一个 .json 路径。
    不合法（含 ..、绝对路径、越界、非 .json）返回 None。"""
    if not name or name.startswith("/") or "\\" in name:
        return None
    p = (ROOT / name).resolve()
    maps = (ROOT / "maps").resolve()
    levels = (ROOT / "levels").resolve()
    if not (p.parent == maps or p.parent == levels):
        return None
    if p.suffix.lower() != ".json":
        return None
    return p


def list_scenarios():
    out = {"maps": [], "levels": []}
    for kind, dirname in (("maps", "maps"), ("levels", "levels")):
        d = ROOT / dirname
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            out[kind].append({"file": f"{dirname}/{f.name}", "name": f.stem})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "map_editor/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        q = urllib.parse.parse_qs(url.query)
        if path == "/":
            html = HERE / "map_editor.html"
            if not html.is_file():
                self._send(404, b"map_editor.html not found", "text/plain; charset=utf-8")
                return
            self._send(200, html.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/list":
            self._send(200, json.dumps(list_scenarios(), ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/read":
            name = q.get("file", [""])[0]
            p = safe_file(name)
            if p is None or not p.is_file():
                self._send(404, json.dumps({"error": "找不到文件"}).encode("utf-8"))
                return
            self._send(200, p.read_bytes(), "application/json; charset=utf-8")
            return
        # 其它静态文件（理论上用不到，但保留给编辑器可能的同目录资源）
        self._send(404, json.dumps({"error": "not found"}).encode("utf-8"))
        return

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path != "/api/write":
            self._send(404, json.dumps({"error": "not found"}).encode("utf-8"))
            return
        name = q.get("file", [""])[0]
        p = safe_file(name)
        if p is None:
            self._send(400, json.dumps({"error": "文件名不合法（只能写 maps/ 或 levels/ 下的 .json）"}).encode("utf-8"))
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._send(400, json.dumps({"error": "JSON 解析失败：" + str(e)}).encode("utf-8"))
            return
        text = json.dumps(data, ensure_ascii=False, indent=1)
        p.write_text(text, encoding="utf-8")
        self._send(200, json.dumps({"ok": True, "file": f"{p.parent.name}/{p.name}"}).encode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description="地图编辑器本地服务器")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"地图编辑器：http://{args.host}:{args.port}  (Ctrl+C 退出)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
