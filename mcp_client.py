# -*- coding: utf-8 -*-
"""
MCP（Model Context Protocol）客户端 —— 只用标准库。

MCP 的 stdio 传输就是「一行一个 JSON-RPC 消息」。这里起子进程、
用管道收发，把远端服务器暴露的工具翻译成本程序能调用的工具。

配置：data/mcp.json
{
  "servers": [
    {"name": "filesystem", "enabled": true, "command": "node",
     "args": ["...\\dist\\index.js", "D:\\工作目录"], "env": {}}
  ]
}
"""

import json
import os
import queue
import subprocess
import threading
import time

PROTO = "2024-11-05"
CREATE_NO_WINDOW = 0x08000000


class MCPServer(object):
    def __init__(self, spec, base_dir):
        self.spec = spec
        self.name = spec.get("name") or "mcp"
        self.base = base_dir
        self.proc = None
        self.tools = []
        self.error = ""
        self.ready = False
        self._id = 0
        self._pending = {}
        self._lock = threading.Lock()

    # ---------- 进程与传输 ----------
    def _cmd(self):
        cmd = [self.spec.get("command") or "node"]
        for a in self.spec.get("args") or []:
            a = str(a).replace("{BASE}", self.base)
            cmd.append(a)
        return cmd

    def start(self, timeout=40):
        env = os.environ.copy()
        for k, v in (self.spec.get("env") or {}).items():
            env[str(k)] = str(v).replace("{BASE}", self.base)
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, creationflags=flags,
            cwd=self.spec.get("cwd") or None, bufsize=0,
        )
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._err_reader, daemon=True).start()

        self.request("initialize", {
            "protocolVersion": PROTO,
            "capabilities": {},
            "clientInfo": {"name": "3hao", "version": "1.0"},
        }, timeout=timeout)
        self.notify("notifications/initialized", {})
        res = self.request("tools/list", {}, timeout=timeout)
        self.tools = res.get("tools") or []
        self.ready = True

    def _reader(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            mid = msg.get("id")
            if mid is not None and mid in self._pending:
                self._pending.pop(mid).put(msg)

    def _err_reader(self):
        for raw in self.proc.stderr:
            text = raw.decode("utf-8", "replace").strip()
            if text and not self.error:
                self.error = text[:300]

    def _send(self, obj):
        if not self.proc or self.proc.poll() is not None:
            raise RuntimeError("MCP 服务器 %s 已经退出" % self.name)
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout=60):
        with self._lock:
            self._id += 1
            mid = self._id
            q = queue.Queue()
            self._pending[mid] = q
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError("MCP 服务器 %s 超过 %d 秒没有响应" % (self.name, timeout))
        finally:
            self._pending.pop(mid, None)
        if "error" in msg:
            raise RuntimeError(str((msg.get("error") or {}).get("message") or "MCP 调用失败"))
        return msg.get("result") or {}

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---------- 调用 ----------
    def call(self, tool, args):
        res = self.request("tools/call", {"name": tool, "arguments": args or {}}, timeout=180)
        out = []
        for item in res.get("content") or []:
            if item.get("type") == "text":
                out.append(item.get("text") or "")
            else:
                out.append("[%s]" % item.get("type"))
        text = "\n".join(out).strip()
        if res.get("isError"):
            return "MCP 返回错误：" + (text or "未知错误")
        return text or "（MCP 没有返回内容）"

    def stop(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass


class MCPManager(object):
    """懒启动：第一次需要工具时才拉起子进程，失败也不影响主流程。"""

    def __init__(self, config_path, base_dir):
        self.path = config_path
        self.base = base_dir
        self._servers = {}
        self._lock = threading.Lock()
        self._started = False
        self._mtime = 0

    def specs(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        return [s for s in (data.get("servers") or []) if s.get("enabled", True)]

    def _config_mtime(self):
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return 0

    def ensure_started(self):
        mtime = self._config_mtime()
        with self._lock:
            if self._started and mtime == self._mtime:
                return
            for s in self._servers.values():
                s.stop()
            self._servers = {}
            self._mtime = mtime
            self._started = True
            for spec in self.specs():
                name = spec.get("name") or "mcp"
                srv = MCPServer(spec, self.base)
                try:
                    srv.start()
                except Exception as e:
                    srv.error = str(e)[:300]
                    srv.ready = False
                self._servers[name] = srv

    # ---------- 对外 ----------
    def tools(self):
        """返回可直接塞进 Anthropic tools 列表的定义。"""
        self.ensure_started()
        out = []
        for srv in self._servers.values():
            if not srv.ready:
                continue
            for t in srv.tools:
                out.append({
                    "name": "mcp__%s__%s" % (srv.name, t.get("name")),
                    "description": (t.get("description") or "")[:900]
                                   + "（来自 MCP 服务器 %s）" % srv.name,
                    "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
                })
        return out

    def call(self, full_name, args):
        parts = full_name.split("__", 2)
        if len(parts) != 3:
            return "工具名不对：" + full_name
        _, srv_name, tool = parts
        self.ensure_started()
        srv = self._servers.get(srv_name)
        if not srv:
            return "找不到 MCP 服务器：" + srv_name
        if not srv.ready:
            return "MCP 服务器 %s 没启动起来：%s" % (srv_name, srv.error or "未知原因")
        try:
            return srv.call(tool, args)
        except Exception as e:
            return "MCP 调用失败：" + (str(e) or e.__class__.__name__)

    def restart(self):
        with self._lock:
            self._started = False
        self.ensure_started()
        return self.status()

    def status(self):
        self.ensure_started()
        out = []
        for srv in self._servers.values():
            out.append({
                "name": srv.name,
                "ready": bool(srv.ready),
                "tools": [t.get("name") for t in srv.tools],
                # 启动成功时 stderr 里的那一行是服务器的欢迎语，不算错误
                "error": "" if srv.ready else (srv.error or "启动失败"),
            })
        return out

    def stop_all(self):
        for s in self._servers.values():
            s.stop()
