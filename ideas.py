# -*- coding: utf-8 -*-
"""灵感采集 —— 按一下热键说一句，自动变成一条灵感。

流程：全局热键 Ctrl+Alt+R（或界面上点按钮）开始录音 → 再按一次结束
     → 本地 faster-whisper 转文字（离线，不联网）→ 交给 server 存进 state

依赖：sounddevice（录）、faster-whisper（转文字）、keyboard（热键）、numpy
模型：默认复用「转文字」项目里已经下好的 small，不重新下载
"""

import os
import subprocess
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))

HOTKEY = "ctrl+alt+r"
SAMPLE_RATE = 16000
MIN_SECONDS = 0.6        # 比这短当误触，丢掉
MAX_SECONDS = 300        # 保险：10 分钟自动停

MODEL_CANDIDATES = [
    os.path.join(BASE, "models", "faster-whisper-small"),
    os.path.join(os.path.dirname(BASE), "转文字", "models", "faster-whisper-small"),
    os.path.join(os.path.dirname(BASE), "转文字", "models", "faster-whisper-medium"),
]

# 中文口语 + 专有名词，给个开头提示能让识别准一点
INITIAL_PROMPT = "以下是普通话的口语记录，可能包含想法、待办、项目名和英文技术词。"


def toast(title, msg):
    """非阻塞地弹一个气泡。toast.ps1 会 sleep 9 秒保活，绝不能同步等它。"""
    ps = os.path.join(BASE, "toast.ps1")
    if not os.path.exists(ps):
        return
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps,
             "-Title", title, "-Msg", msg],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
    except Exception:
        pass


def find_model():
    env = (os.environ.get("TODAY_WHISPER_MODEL") or "").strip()
    if env and os.path.isdir(env):
        return env
    for p in MODEL_CANDIDATES:
        if os.path.isdir(p):
            return p
    return ""


class Recorder(object):
    """一个麦克风录音器 + 懒加载的转写模型。线程安全，热键和界面共用。"""

    def __init__(self, on_text=None):
        self.on_text = on_text
        self._lock = threading.Lock()
        self._model = None
        self._model_lock = threading.Lock()
        self._model_error = ""
        self._stream = None
        self._frames = []
        self._started_at = 0.0
        self._busy = False          # 正在转写
        self._hotkey_handle = None
        self._hotkey_error = ""
        self.hotkey_on = False

    # ---------- 状态 ----------
    def status(self):
        return {
            "recording": self._stream is not None,
            "transcribing": self._busy,
            "seconds": round(time.time() - self._started_at, 1) if self._stream else 0,
            "hotkey": HOTKEY,
            "hotkey_on": self.hotkey_on,
            "model": find_model(),
            "error": self.available()[1],
        }

    def available(self):
        """返回 (能不能用, 原因)。"""
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
        except Exception as e:
            return False, "缺少 sounddevice/numpy：%s" % e
        if not find_model():
            return False, "找不到 whisper 模型目录"
        return True, ""

    # ---------- 录音 ----------
    def toggle(self):
        if self._stream is not None:
            return self.stop()
        return self.start()

    def start(self):
        ok, why = self.available()
        if not ok:
            toast("灵感采集", why)
            return False
        if self._stream is not None:
            return False
        try:
            import numpy as np
            import sounddevice as sd

            self._frames = []

            def cb(indata, frames, t, status):  # noqa: ARG001
                self._frames.append(indata.copy())

            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=cb
            )
            self._stream.start()
            self._started_at = time.time()
            toast("🎙 正在录音", "说完再按一次 Ctrl+Alt+R 结束")
            return True
        except Exception as e:
            self._stream = None
            toast("灵感采集", "麦克风打不开：%s" % str(e)[:80])
            return False

    def stop(self):
        stream = self._stream
        if stream is None:
            return False
        self._stream = None
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

        secs = time.time() - self._started_at
        try:
            import numpy as np
            frames = self._frames
            self._frames = []
            if not frames:
                toast("🎙 录音结束", "没录到声音，已忽略")
                return True
            audio = np.concatenate(frames).reshape(-1)
        except Exception:
            return True

        if secs < MIN_SECONDS or audio.size < SAMPLE_RATE * MIN_SECONDS:
            toast("🎙 录音结束", "录音太短，已忽略")
            return True

        self._busy = True
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()
        return True

    # ---------- 转写 ----------
    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            path = find_model()
            if not path:
                raise RuntimeError("找不到 whisper 模型目录")
            from faster_whisper import WhisperModel
            self._model = WhisperModel(path, device="cpu", compute_type="int8", num_workers=1)
            return self._model

    def _transcribe(self, audio):
        text = ""
        try:
            model = self._get_model()
            segs, _info = model.transcribe(
                audio, language="zh", vad_filter=True, beam_size=1,
                initial_prompt=INITIAL_PROMPT,
            )
            text = "".join(s.text for s in segs).strip()
        except Exception as e:
            self._model_error = str(e)[:200]
            toast("灵感采集", "转文字失败：%s" % self._model_error)
        finally:
            self._busy = False

        if not text:
            return
        # 去掉常见的口水词
        for w in ("嗯，", "嗯。", "呃，", "那个，", "就是，"):
            while text.startswith(w):
                text = text[len(w):].strip()
        if not text:
            return
        toast("✅ 灵感已记下", text[:120])
        if self.on_text:
            try:
                self.on_text(text)
            except Exception:
                pass

    # ---------- 热键 ----------
    def start_hotkey(self):
        """注册全局热键。失败（被占用/权限）只是没有热键，界面按钮照样能用。"""
        if self._hotkey_handle is not None:
            return True
        try:
            import keyboard
            self._hotkey_handle = keyboard.add_hotkey(HOTKEY, self.toggle, suppress=False)
            self.hotkey_on = True
            return True
        except Exception as e:
            self.hotkey_on = False
            self._hotkey_error = str(e)[:120]
            return False
