# -*- coding: utf-8 -*-
"""定时简报生成器 —— 由 Windows 计划任务调用，不需要网页开着。

用法：
    py brief.py morning     早间简报
    py brief.py evening     晚间收尾
"""

import os
import sys
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


def toast(title, msg):
    ps = os.path.join(server.BASE, "toast.ps1")
    if not os.path.exists(ps):
        return
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps,
             "-Title", title, "-Msg", msg],
            timeout=25,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:
        pass


def log(line):
    """后台任务没有控制台，出错只能靠这个文件排查。"""
    try:
        os.makedirs(server.DATA_DIR, exist_ok=True)
        with open(os.path.join(server.DATA_DIR, "brief.log"), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (server.now_label(), line))
    except Exception:
        pass


def is_net_error(msg):
    s = (msg or "").lower()
    return any(k in s for k in (
        "getaddrinfo", "name or service", "timed out", "timeout",
        "connection", "11001", "10060", "ssl", "proxy", "unreachable",
    ))


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "morning"
    if kind not in ("morning", "evening"):
        print("用法：py brief.py [morning|evening]")
        return 2

    cfg = server.load_cfg_file()
    if not cfg.get("api_key"):
        log("[%s] 跳过：没有配置 API Key" % kind)
        print("还没有配置 API Key（在「知更」设置里填一次即可）")
        return 1

    text = None
    last = ""
    try:
        # 开机时段网络/代理常常还没起来，网络类错误隔一分钟重试两次
        for attempt in range(3):
            try:
                text, _push = server.run_brief(cfg, kind)
                last = ""
                break
            except Exception as e:
                last = str(e) or e.__class__.__name__
                if attempt < 2 and is_net_error(last):
                    log("[%s] 第 %d 次失败：%s（60 秒后重试）" % (kind, attempt + 1, last))
                    time.sleep(60)
                    continue
                break
    finally:
        # 简报进程会顺手拉起 MCP 子进程，这里收干净，别留一堆 node 在后台
        try:
            server.MCP.stop_all()
        except Exception:
            pass

    title = "知更 · 早间简报" if kind == "morning" else "知更 · 晚间收尾"
    if text is None or last:
        log("[%s] 失败：%s" % (kind, last))
        toast("知更 · 简报没生成", ("网络没通：" + last)[:150])
        print("生成失败：" + last)
        return 1

    toast(title, (text or "")[:180])
    log("[%s] 成功，%d 字" % (kind, len(text or "")))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
