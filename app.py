# -*- coding: utf-8 -*-
"""知更 · 桌面应用入口

窗口（WebView2）和服务（server.py）跑在两个进程里。
为什么拆开：pywebview 的 WinForms 消息循环会把同进程的后台线程饿死，
表现是端口连得上、但 HTTP 请求永远收不到回包。
拆成两个进程后，服务和界面互不影响。

由 zhigeng.exe 用 pythonw 拉起，所以不会有黑色命令行窗口。
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

CREATE_NO_WINDOW = 0x08000000


def port_open(port, timeout=0.3):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def pythonw_path():
    """优先用 pythonw.exe，起子进程时不会闪黑框。"""
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        cand = exe[: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def start_server():
    """起后台服务；已经有一个在跑就直接用它（返回 None）。"""
    if port_open(server.PORT):
        return None

    os.makedirs(server.DATA_DIR, exist_ok=True)
    log_path = os.path.join(server.DATA_DIR, "app.log")
    logf = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            [pythonw_path(), os.path.join(server.BASE, "server.py"), "--headless"],
            cwd=server.BASE,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=logf,
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    finally:
        logf.close()

    for _ in range(80):          # 最多等 12 秒
        if port_open(server.PORT):
            return proc
        if proc.poll() is not None:
            break
        time.sleep(0.15)
    return proc


def stop_server():
    """让服务自己收摊（顺手结束 MCP 子进程），不行再强杀。"""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/shutdown" % server.PORT,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def set_icon():
    """把窗口图标换成 zhigeng.ico。

    走 Win32（ctypes）而不是 pythonnet：之前用 win.native.Icon 会在
    WebView2 初始化时和 pywebview 自己的 Focus() 互相卡死，窗口关不掉。
    """
    ico = os.path.join(server.BASE, "zhigeng.ico")
    if not os.path.exists(ico):
        return
    try:
        import ctypes
        from ctypes import wintypes

        u = ctypes.windll.user32
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowW.restype = wintypes.HWND
        u.LoadImageW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.LoadImageW.restype = wintypes.HANDLE
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
        u.SendMessageW.restype = wintypes.LPARAM

        hwnd = 0
        for _ in range(50):          # 窗口可能还没建好，等最多 5 秒
            hwnd = u.FindWindowW(None, "知更")
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return

        LR_LOADFROMFILE = 0x0010
        IMAGE_ICON = 1
        WM_SETICON = 0x0080
        for kind, size in ((1, 32), (0, 16)):     # 1=大图标 0=小图标
            h = u.LoadImageW(None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
            if h:
                u.SendMessageW(hwnd, WM_SETICON, kind, h)
    except Exception:
        pass


def main():
    proc = start_server()

    import webview

    win = webview.create_window(
        "知更",
        "http://127.0.0.1:%d/" % server.PORT,
        width=1180,
        height=840,
        min_size=(940, 620),
        background_color="#F7F5F1",
    )

    try:
        win.events.shown += lambda: set_icon()
    except Exception:
        pass

    try:
        # private_mode=False + 固定 storage_path：让窗口的 localStorage 留得下来，
        # 否则每次重开都算新访客，设置（包括 Key）全丢
        webview.start(
            private_mode=False,
            storage_path=os.path.join(server.DATA_DIR, "app-window"),
        )
    finally:
        # 只关自己起的服务；复用别人的服务时，关窗口不该把对方的服务带走
        if proc is not None:
            stop_server()
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
