# -*- coding: utf-8 -*-
"""
「知更」—— 本地个人 AI 智能体
单文件 Python 服务：静态页面 + Agent 循环 + 记忆/任务存储。

启动：  py server.py
然后用浏览器打开 http://127.0.0.1:8899
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq
from urllib import error as urlerr



class _NullStream(object):
    """pythonw.exe 下 sys.stdout / sys.stderr 是 None，
    往里写日志会抛异常、把处理请求的线程直接打死（表现：连上了但永远不回包）。
    这里兜一个黑洞流，环境只是「没人看日志」，不该影响服务。"""

    encoding = "utf-8"

    def write(self, *_a):
        return 0

    def writelines(self, *_a):
        pass

    def flush(self):
        pass

    def isatty(self):
        return False

    def close(self):
        pass


for _name in ("stdout", "stderr", "__stdout__", "__stderr__"):
    try:
        if getattr(sys, _name, None) is None:
            setattr(sys, _name, _NullStream())
    except Exception:
        pass


BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("TODAY_DATA_DIR") or os.path.join(BASE, "data")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
SKILLS_DIR = os.path.join(BASE, "skills")
MCP_FILE = os.path.join(DATA_DIR, "mcp.json")
# 日记库：Obsidian 直接打开这个目录。它在 data/ 下，但属于用户内容，
# 文件工具可以写；数据库文件（state.json 等）仍然禁止。
DIARY_DIR = os.environ.get("TODAY_DIARY_DIR") or os.path.join(DATA_DIR, "diary")
PORT = int(os.environ.get("TODAY_PORT") or 8899)
# 文件工具的默认活动范围：本程序所在目录的上一层
DEFAULT_WORKSPACE = os.path.dirname(BASE)

sys.path.insert(0, BASE)
import mcp_client  # noqa: E402
import ideas  # noqa: E402
import inspire  # noqa: E402

MCP = mcp_client.MCPManager(MCP_FILE, BASE)

_lock = threading.RLock()

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# --------------------------------------------------------------------------
# 状态存储
# --------------------------------------------------------------------------

def _empty_state():
    return {"memories": [], "tasks": [], "ideas": [], "discover": [], "seen": [],
            "conversations": [], "active": "", "meta": {}}


def _msg_title(msgs, fallback="新对话"):
    for m in msgs:
        if m.get("role") != "user":
            continue
        text = "".join(
            b.get("text", "") for b in (m.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip().replace("\n", " ")
        if text:
            return text[:18] + ("…" if len(text) > 18 else "")
    return fallback


def _new_conv(title="新对话", cid=None, kind="chat"):
    return {
        "id": cid or ("c_" + uuid.uuid4().hex[:8]),
        "title": title,
        "kind": kind,
        "created": now_label(),
        "updated": now_label(),
        "messages": [],
    }


def get_conv(st, cid=None):
    """取当前对话；没有就建一个。"""
    cid = cid or st.get("active") or ""
    convs = st.get("conversations") or []
    for c in convs:
        if c.get("id") == cid:
            return c
    if convs:
        st["active"] = convs[0]["id"]
        return convs[0]
    c = _new_conv()
    st["conversations"] = [c]
    st["active"] = c["id"]
    return c


def brief_conv(st):
    """简报单独一个对话，别混进正在聊的那条里。"""
    for c in st.get("conversations") or []:
        if c.get("kind") == "brief":
            return c
    c = _new_conv("简报", cid="c_brief", kind="brief")
    st.setdefault("conversations", []).insert(0, c)
    return c


def load_state():
    with _lock:
        if not os.path.exists(STATE_FILE):
            st = _empty_state()
            get_conv(st)
            return st
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            # 主文件坏了就退到上一版
            st = _empty_state()
            bak = STATE_FILE + ".bak"
            if os.path.exists(bak):
                try:
                    with open(bak, "r", encoding="utf-8") as f:
                        st = json.load(f)
                except Exception:
                    st = _empty_state()

        # 老版本：messages 是一条大流，升成「对话列表」
        legacy = st.pop("messages", None)
        for k, v in _empty_state().items():
            st.setdefault(k, v)

        migrated = False
        if legacy is None and not st["conversations"]:
            c = _new_conv()
            st["conversations"] = [c]
            st["active"] = c["id"]
            migrated = True
        elif legacy is not None:
            if not st["conversations"]:
                c = _new_conv(_msg_title(legacy, "默认对话"))
                c["messages"] = legacy
                st["conversations"] = [c]
                st["active"] = c["id"]
            elif legacy:
                get_conv(st, st.get("active"))["messages"].extend(legacy)
            migrated = True
        get_conv(st)
        if migrated:
            # 迁移结果要落盘，否则每次读都生成新 id，前端切来切去对不上
            save_state(st)
        return st


def save_state(st):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        # 留一份上一版：真写坏了（脚本误写、断电）还能从 .bak 捞回来
        try:
            if os.path.exists(STATE_FILE) and os.path.getsize(STATE_FILE) > 2:
                import shutil
                shutil.copy2(STATE_FILE, STATE_FILE + ".bak")
        except Exception:
            pass
        os.replace(tmp, STATE_FILE)


def now_label():
    n = datetime.now()
    # 别用 strftime 拼中文：Windows 上它走 C locale，在非 UTF-8 环境里
    # 会直接抛 UnicodeEncodeError('locale' codec ...)，后台任务就静默失败了
    return "%d年%02d月%02d日 %s %02d:%02d" % (
        n.year, n.month, n.day, WEEKDAYS[n.weekday()], n.hour, n.minute)


# --------------------------------------------------------------------------
# 灵感速记
# --------------------------------------------------------------------------

_REC = None


def recorder():
    """全局唯一的录音器：热键和界面按钮共用同一个实例。"""
    global _REC
    if _REC is None:
        _REC = ideas.Recorder(on_text=lambda t: add_idea(t, "voice"))
    return _REC


def add_idea(text, source="chat"):
    text = (text or "").strip()
    if not text:
        return None
    st = load_state()
    item = {
        "id": "i_" + uuid.uuid4().hex[:8],
        "text": text[:1000],
        "created": now_label(),
        "source": source,
        "done": False,
    }
    st.setdefault("ideas", []).insert(0, item)
    del st["ideas"][500:]
    save_state(st)
    return item


# --------------------------------------------------------------------------
# 发现：从十几个平台抓灵感
# --------------------------------------------------------------------------

def discover_refresh(cfg, limit=8):
    """抓一轮新项目。返回 (picks, errors)；picks 为空说明这轮没东西。

    注意：这一轮要跑二十多秒（抓十来个平台 + 模型策展）。期间用户可能点了
    收藏、删除、转任务——所以**不能**把开头读到的 state 直接写回去，
    否则这二十多秒里的操作会被旧快照覆盖（用户收藏的条目就在「换一批」
    之后莫名消失了）。收尾时重新读盘，只把新 picks 并进去。
    """
    snapshot = load_state()
    picks, errors = inspire.run_once(cfg, state=snapshot, limit=limit)
    if not picks:
        return picks, errors

    fresh = load_state()
    board = fresh.get("discover") or []
    have = {x["id"] for x in board}
    board.extend(p for p in picks if p["id"] not in have)

    if len(board) > 120:
        over = len(board) - 120
        keep = []
        for it in board:
            if over > 0 and not it.get("saved"):
                over -= 1
                continue
            keep.append(it)
        board = keep
    fresh["discover"] = board

    seen = list(fresh.get("seen") or [])
    have_seen = set(seen)
    for p in picks:
        if p["id"] not in have_seen:
            seen.append(p["id"])
            have_seen.add(p["id"])
    fresh["seen"] = seen[-4000:]

    save_state(fresh)
    return picks, errors


def idea_to_task(iid):
    st = load_state()
    for it in st.get("ideas") or []:
        if it["id"] != iid:
            continue
        st["tasks"].append({
            "id": "t_" + uuid.uuid4().hex[:8],
            "title": it["text"][:60],
            "due": "",
            "priority": "中",
            "done": False,
        })
        it["done"] = True
        save_state(st)
        return True
    return False


# --------------------------------------------------------------------------
# 后台任务用的配置（定时简报要读，所以落盘；只在用户主动开启时才写）
# --------------------------------------------------------------------------

def load_cfg_file():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg_file(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG_FILE)


def drop_cfg_file():
    try:
        os.remove(CONFIG_FILE)
    except OSError:
        pass


CFG_FIELDS = ("provider", "api_key", "base_url", "model", "workspace")


def merged_cfg(parts):
    """前端传来的配置优先，缺的字段用落盘那份补。

    好处是：Key 只需要输一次。界面重开、浏览器配置被清掉，
    只要 data/config.json 还在，就不必再填。
    """
    parts = parts or {}
    saved = load_cfg_file()

    def one(k):
        v = parts.get(k)
        v = v.strip() if isinstance(v, str) else ""
        return v

    out = {}
    for k in CFG_FIELDS:
        out[k] = one(k) or (saved.get(k) or "")

    # 换了服务商又没填地址，别把上一家的地址/模型套过来
    if one("provider") and one("provider") != (saved.get("provider") or ""):
        if not one("base_url"):
            out["base_url"] = ""
        if not one("model"):
            out["model"] = ""
    if not out["provider"]:
        out["provider"] = "anthropic"
    return out


def cfg_public():
    """给前端看的配置摘要——不返回 Key 本身，只说「有没有」。"""
    f = load_cfg_file()
    return {
        "provider": f.get("provider") or "",
        "base_url": f.get("base_url") or "",
        "model": f.get("model") or "",
        "workspace": f.get("workspace") or "",
        "has_key": bool((f.get("api_key") or "").strip()),
    }


# --------------------------------------------------------------------------
# MCP 配置种子（首次启动时把三个官方服务器写进 data/mcp.json）
# --------------------------------------------------------------------------

MCP_SEED = [
    ("filesystem", "@modelcontextprotocol/server-filesystem", "文件系统",
     lambda entry, ws: {"command": "node", "args": [entry, ws]}),
    ("memory", "@modelcontextprotocol/server-memory", "知识图谱记忆",
     lambda entry, ws: {"command": "node", "args": [entry],
                        "env": {"MEMORY_FILE_PATH": os.path.join(DATA_DIR, "memory.json")}}),
    ("sequential-thinking", "@modelcontextprotocol/server-sequential-thinking", "分步推理",
     lambda entry, ws: {"command": "node", "args": [entry]}),
]


def seed_mcp_config():
    if os.path.exists(MCP_FILE):
        return
    servers = []
    ws = DEFAULT_WORKSPACE
    for name, pkg, _label, make in MCP_SEED:
        entry = os.path.join(BASE, "mcp", "node_modules", pkg, "dist", "index.js")
        if not os.path.exists(entry):
            continue
        spec = {"name": name, "enabled": True}
        spec.update(make(entry, ws))
        servers.append(spec)
    if not servers:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = MCP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"servers": servers}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MCP_FILE)


# --------------------------------------------------------------------------
# 工具定义
# --------------------------------------------------------------------------

TOOLS = [
    {
        "name": "remember",
        "description": (
            "把值得长期记住的用户信息写入长期记忆。适合：人物关系、正在做的项目、"
            "明确表达的偏好、答应过的事。不要记录一次性的闲聊。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "一句话事实，第三人称，例如「用户在做校园 3D 地图项目」"},
                "category": {"type": "string", "enum": ["人物", "项目", "偏好", "承诺", "其他"]},
            },
            "required": ["content", "category"],
        },
    },
    {
        "name": "forget",
        "description": "删除一条已经不成立或用户明确要求删除的记忆。",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "记忆的 id"}},
            "required": ["id"],
        },
    },
    {
        "name": "add_task",
        "description": "给用户添加一件今天或近期要做的事。",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题，简短"},
                "due": {"type": "string", "description": "截止时间，可留空，例如「今天下午」「11月15日」"},
                "priority": {"type": "string", "enum": ["高", "中", "低"]},
            },
            "required": ["title"],
        },
    },
    {
        "name": "complete_task",
        "description": "把某个任务标记为已完成。",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "任务的 id"}},
            "required": ["id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "读取当前所有任务及其 id、状态，用户问「我今天要做什么」时使用。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_files",
        "description": "列出某个目录下的文件和子目录。不懂项目结构时先用它看一眼。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，可用相对路径（相对工作目录），留空表示工作目录本身"},
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": "读取一个文本文件的内容。改文件之前一定要先读，否则会覆盖掉别人的东西。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径，相对或绝对"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "把完整内容写入文件（新建或整体覆盖）。只在需要整份重写时用；小改动请用 edit_file。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的完整文本"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "把文件里的一段文字替换成新的文字。old_text 必须与原文完全一致且只出现一次，适合局部修改。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_text": {"type": "string", "description": "要被替换的原文片段，必须唯一"},
                "new_text": {"type": "string", "description": "替换后的新文字"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "make_dir",
        "description": "新建一个目录（已存在也不算错）。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录路径"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_skills",
        "description": "列出本机安装的技能（skill）。每个技能是一份专业作业指南。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "use_skill",
        "description": (
            "读取一个技能的完整作业指南。任务和某个技能描述对得上时，先调用它，"
            "再按指南里的步骤和铁律干活。不要凭印象假装已经读过。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "技能名，见 list_skills"}},
            "required": ["name"],
        },
    },
    {
        "name": "add_idea",
        "description": (
            "记一条灵感。用户随口说出的想法、点子、以后可能要做的事，"
            "不属于明确待办、也不值得进长期记忆时用它。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "灵感内容，一句话说清"}},
            "required": ["text"],
        },
    },
    {
        "name": "list_ideas",
        "description": "读取记下的灵感列表（最近的在前）。用户问「我记过什么点子」时用。",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "discover_projects",
        "description": (
            "现抓一批灵感：GitHub、Hacker News、Product Hunt、Reddit、阮一峰周刊、"
            "少数派、InfoQ、爱范儿、量子位、V2EX、掘金，挑几个有意思的项目或文章推荐给用户。"
            "用户说「找点好玩的」「有什么新东西」「推荐几个项目」时用。"
            "抓一轮要二十来秒，抓完每条都有一句中文推荐理由。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "推荐几个，默认 5，最多 8"},
            },
            "required": [],
        },
    },
    {
        "name": "list_discover",
        "description": (
            "看「发现」面板里都推过什么（最近的在前），带 id、来源、是否已收藏/已转任务。"
            "用户说「推送的灵感」「推了什么」时用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "看最近几条，默认 20"},
            },
            "required": [],
        },
    },
    {
        "name": "mark_discover",
        "description": (
            "改「发现」面板里的状态：收藏 / 取消收藏 / 标记已看 / 标记未看。"
            "用户说「都收藏了」「把这些标记一下」时用，scope=all 就是全部。"
            "不要用文件工具去改 data/state.json，那是知更 自己的数据库。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "unsave", "done", "undone"]},
                "scope": {"type": "string", "enum": ["all", "ids"],
                          "description": "all=面板里所有条目；ids=只改 ids 里那几个"},
                "ids": {"type": "array", "items": {"type": "string"},
                        "description": "scope=ids 时给"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "write_diary",
        "description": (
            "把当天日记写进日记库（data/diary/日记/YYYY-MM-DD.md）。"
            "body 是他自己的日记，按他说话的次序自由写成一整篇，不要套小节、不要写「今天做了什么／明天要做什么」这种结构；"
            "success 和 reflection 是从正文里提炼出来的，quotes 照抄他的原句。"
            "同一天再调用会追加，不会覆盖。过零点写的内容按前一天算，date 传前一天。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD，默认今天"},
                "body": {"type": "string", "description": "日记正文，模仿用户自己的口吻"},
                "success": {"type": "string", "description": "从正文提炼的成功日记，语气平和"},
                "reflection": {"type": "string", "description": "只写用户自己说出的反思，不替他总结"},
                "quotes": {"type": "array", "items": {"type": "string"},
                           "description": "原话，照抄，不改写"},
            },
            "required": ["body"],
        },
    },
    {
        "name": "list_diary",
        "description": "列出最近的日记（日期 + 开头一句），用户问「我最近写了什么」时用。",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "默认 10 篇"}},
            "required": [],
        },
    },
    {
        "name": "daily_todo",
        "description": (
            "「每日必做」清单：每天按 data/diary/每日必做/_模板.md 自动生成当天的一份。"
            "查看今天还剩什么、把某项勾掉/取消、临时加一项，都用它。"
            "用户说「今天必做还有什么」「背单词打了」时用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["list", "check", "uncheck", "add", "remove"],
                           "description": "默认 list"},
                "item": {"type": "string", "description": "条目文字，模糊匹配即可"},
                "index": {"type": "integer", "description": "第几项，从 1 开始"},
                "date": {"type": "string", "description": "YYYY-MM-DD，默认今天"},
            },
            "required": [],
        },
    },
    {
        "name": "append_project",
        "description": (
            "给「项目」页的「现在到哪」追加一行带日期的进展（data/diary/项目/<项目名>.md）。"
            "页面不存在会按四段模板新建。用户聊到某个项目推进了什么时用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "项目名，如「校园地图」"},
                "line": {"type": "string", "description": "一行进展，不写日期，工具自己加"},
                "date": {"type": "string", "description": "YYYY-MM-DD，默认今天"},
            },
            "required": ["name", "line"],
        },
    },
]


# --------------------------------------------------------------------------
# 技能（skills/ 下每个子目录一个 SKILL.md，按需读取，不占常驻上下文）
# --------------------------------------------------------------------------

def load_skills():
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    for slug in sorted(os.listdir(SKILLS_DIR)):
        p = os.path.join(SKILLS_DIR, slug, "SKILL.md")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception:
            continue
        meta, body = {}, raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].lstrip("\n")
                for line in parts[1].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
        out.append({
            "slug": slug,
            "name": meta.get("name") or slug,
            "description": meta.get("description") or "",
            "body": body,
        })
    return out


def find_skill(name):
    key = (name or "").strip().lower()
    for s in load_skills():
        if key in (s["slug"].lower(), s["name"].lower()):
            return s
    return None


def all_tools():
    try:
        return TOOLS + MCP.tools()
    except Exception:
        return TOOLS


def tool_to_openai(t):
    return {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }


def _safe_path(cfg, p):
    """把用户/模型给的路径解析到工作目录之内，越界一律拒绝。"""
    root = os.path.realpath((cfg.get("workspace") or "").strip() or DEFAULT_WORKSPACE)
    raw = (p or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("路径为空")
    cand = raw if os.path.isabs(raw) else os.path.join(root, raw)
    cand = os.path.realpath(cand)
    n_root = os.path.normcase(root)
    n_cand = os.path.normcase(cand)
    if n_cand != n_root and not n_cand.startswith(n_root + os.sep):
        raise ValueError("这个路径在工作目录之外，被拦下了。当前工作目录：" + root)
    return cand


def _read_text(path):
    """读成统一 \\n 换行的文本，同时记下原文件的换行风格。"""
    with open(path, "rb") as f:
        raw = f.read()
    txt = None
    for enc in ("utf-8", "gb18030"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if txt is None:
        txt = raw.decode("utf-8", "replace")
    nl = "\r\n" if "\r\n" in txt else "\n"
    if nl == "\r\n":
        txt = txt.replace("\r\n", "\n")
    return txt, nl


def _write_text(path, txt, nl):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    if nl != "\n":
        txt = txt.replace("\n", nl)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(txt)


def _size(n):
    if n < 1024:
        return "%d B" % n
    if n < 1048576:
        return "%.1f KB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


def _rel(cfg, path):
    root = os.path.realpath((cfg.get("workspace") or "").strip() or DEFAULT_WORKSPACE)
    try:
        return os.path.relpath(path, root)
    except Exception:
        return path


def _in_data_dir(p, cfg):
    """知更 自己的数据库（data/ 下的 state.json 等）不允许用文件工具写。"""
    if not p or not isinstance(p, str):
        return False
    root = os.path.realpath((cfg.get("workspace") or "").strip() or DEFAULT_WORKSPACE)
    full = os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
    diary = os.path.realpath(DIARY_DIR)
    if full == diary or full.startswith(diary + os.sep):
        return False          # 日记库是用户内容，放行
    data = os.path.realpath(DATA_DIR)
    return full == data or full.startswith(data + os.sep)


DATA_GUARD_MSG = ("这是知更 自己的数据目录（state.json / config.json 等），"
                  "不能直接用文件工具改。发现面板用 list_discover / mark_discover，"
                  "记忆和任务用 remember / add_task 这些工具。")


# --------------------------------------------------------------------------
# 日记库（data/diary/，Obsidian 直接打开）
# --------------------------------------------------------------------------

DIARY_WEEK = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _rel_soft(path):
    try:
        return os.path.relpath(path, BASE)
    except Exception:
        return path


def _diary_date(s):
    s = (s or "").strip()
    if not s:
        return date.today()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _diary_sections(text):
    """第一个 `## ` 之前的是日记正文（正文本身不套结构），之后按小节切。"""
    secs, cur, buf = {}, "正文", []
    for line in (text or "").splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if cur:
                secs[cur] = "\n".join(buf).strip()
            cur, buf = line[3:].strip(), []
        else:
            if line.strip() == "---":
                continue
            buf.append(line)
    if cur:
        secs[cur] = "\n".join(buf).strip()
    # 兼容旧文件：以前正文挂在「## 日记」下面
    if "日记" in secs and not secs.get("正文"):
        secs["正文"] = secs.pop("日记")
    return secs


def _write_diary(args):
    day = _diary_date(args.get("date"))
    path = os.path.join(DIARY_DIR, "日记", day.isoformat() + ".md")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    body = (args.get("body") or "").strip()
    success = (args.get("success") or "").strip()
    reflection = (args.get("reflection") or "").strip()
    quotes = args.get("quotes") or []
    if isinstance(quotes, str):
        quotes = [quotes]
    quotes = [q.strip() for q in quotes if isinstance(q, str) and q.strip()]
    if not (body or success or reflection or quotes):
        raise ValueError("内容为空，没写。")

    secs = _diary_sections(open(path, encoding="utf-8").read()) if os.path.exists(path) else {}
    for key, val in (("正文", body), ("成功日记", success), ("反思", reflection)):
        if val:
            old = secs.get(key) or ""
            secs[key] = (old + "\n\n" + val).strip() if old else val
    if quotes:
        old = secs.get("原话备查") or ""
        add = "\n".join("> " + q for q in quotes)
        secs["原话备查"] = (old + "\n\n" + add).strip() if old else add

    # 正文直接放在标题下面，不套「## 日记」这种壳；提炼出来的东西才分小节
    out = ["# %s　%s" % (day.isoformat(), DIARY_WEEK[day.weekday()]), "",
           (secs.get("正文") or "").strip(), ""]
    for key in ("成功日记", "反思"):
        out += ["## " + key, "", (secs.get(key) or "").strip(), ""]
    out += ["---", "", "## 原话备查", "", (secs.get("原话备查") or "").strip()]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")

    return ("已写入 %s：正文 %d 字，成功日记 %d 字，反思 %d 字，原话 %d 条。" % (
        _rel_soft(path), len(body), len(success), len(reflection), len(quotes)))


def _list_diary(limit=10):
    d = os.path.join(DIARY_DIR, "日记")
    if not os.path.isdir(d):
        return "日记库还是空的。"
    fs = sorted([x for x in os.listdir(d)
                 if re.match(r"^\d{4}-\d{2}-\d{2}\.md$", x)], reverse=True)[:max(1, limit)]
    if not fs:
        return "日记库还是空的。"
    lines = []
    for f in fs:
        first = ""
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            for ln in fh:
                t = ln.strip()
                if t and not t.startswith("#") and not t.startswith(">") and t != "---":
                    first = t[:60]
                    break
        lines.append("[%s] %s" % (f[:-3], first))
    return "\n".join(lines)


DAILY_DIRNAME = "每日必做"
DAILY_TEMPLATE = "_模板.md"


def _daily_path(day):
    return os.path.join(DIARY_DIR, DAILY_DIRNAME, day.isoformat() + ".md")


def _daily_parse(text):
    """从 Markdown 里读复选框，返回 [(完成与否, 文本)]。"""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*-\s*\[( |x|X)\]\s*(.+)$", line)
        if m:
            out.append((m.group(1).lower() == "x", m.group(2).strip()))
    return out


def _daily_render(day, items):
    lines = ["# %s　%s" % (day.isoformat(), DIARY_WEEK[day.weekday()]), ""]
    for done, text in items:
        lines.append("- [%s] %s" % ("x" if done else " ", text))
    return "\n".join(lines).rstrip() + "\n"


def _daily_ensure(day):
    path = _daily_path(day)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        return path, _daily_parse(open(path, encoding="utf-8").read())
    tpl = os.path.join(DIARY_DIR, DAILY_DIRNAME, DAILY_TEMPLATE)
    items = _daily_parse(open(tpl, encoding="utf-8").read()) if os.path.exists(tpl) else []
    with open(path, "w", encoding="utf-8") as f:
        f.write(_daily_render(day, items))
    return path, items


def _daily_save(day, items):
    with open(_daily_path(day), "w", encoding="utf-8") as f:
        f.write(_daily_render(day, items))


def _daily_todo(args):
    day = _diary_date(args.get("date"))
    action = (args.get("action") or "list").lower()
    path, items = _daily_ensure(day)

    if action == "list":
        if not items:
            return "今天的「每日必做」还是空的。要加什么，直接说。"
        done = sum(1 for d, _ in items if d)
        lines = ["%s 的每日必做（%d/%d）：" % (day.isoformat(), done, len(items))]
        for i, (d, t) in enumerate(items, 1):
            lines.append("%d. [%s] %s" % (i, "x" if d else " ", t))
        return "\n".join(lines)

    if action in ("check", "uncheck"):
        want = str(args.get("item") or "").strip()
        idx = args.get("index")
        hit = False
        for i, (d, t) in enumerate(items, 1):
            if (idx is not None and i == int(idx)) or (want and want in t):
                items[i - 1] = (action == "check", t)
                hit = True
                if idx is not None:
                    break
        if not hit:
            return "没找到这一项：" + want
        _daily_save(day, items)
        return "已%s：%s" % ("勾上了" if action == "check" else "取消勾选了",
                            want or ("第 %s 项" % idx))

    if action == "add":
        want = str(args.get("item") or "").strip()
        if not want:
            return "要加什么？"
        items.append((False, want))
        _daily_save(day, items)
        return "已加入今天的每日必做：" + want

    if action == "remove":
        want = str(args.get("item") or "").strip()
        idx = args.get("index")
        before = len(items)
        if idx is not None:
            i = int(idx)
            if 1 <= i <= len(items):
                items.pop(i - 1)
        else:
            items = [x for x in items if want not in x[1]]
        if len(items) == before:
            return "没找到这一项：" + want
        _daily_save(day, items)
        return "已删除。"

    return "action 只能是 list / check / uncheck / add / remove。"


def _append_project(args):
    name = re.sub(r'[\\/:*?"<>|]', "-", (args.get("name") or "").strip())
    line = (args.get("line") or "").strip()
    if not name or not line:
        raise ValueError("项目名和进展都不能为空。")
    day = _diary_date(args.get("date"))
    path = os.path.join(DIARY_DIR, "项目", name + ".md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bullet = "- %s　%s" % (day.isoformat(), line)

    if not os.path.exists(path):
        text = ("# %s\n\n`[[项目/_索引|项目]]`\n\n## 目标\n\n（待补）\n\n"
                "## 现在到哪\n\n%s\n\n## 卡住的\n\n（暂无记录）\n\n"
                "## 做过的决定\n\n（暂无记录）\n" % (name, bullet))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return "新建了项目页 " + _rel_soft(path) + "，并记下这一行。"

    lines = open(path, encoding="utf-8").read().splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "## 现在到哪":
            start = i + 1
            break
    if start is None:
        lines += ["", "## 现在到哪", "", bullet]
    else:
        end = len(lines)
        for j in range(start, len(lines)):
            if lines[j].startswith("## "):
                end = j
                break
        ins = end
        while ins > start and not lines[ins - 1].strip():
            ins -= 1
        lines.insert(ins, bullet)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")
    return "已在「%s」的「现在到哪」追加：%s" % (name, bullet)


def _file_tool(name, args, cfg):
    if name == "list_files":
        p = _safe_path(cfg, args.get("path"))
        if not os.path.isdir(p):
            raise ValueError("不是目录：" + _rel(cfg, p))
        names = sorted(os.listdir(p))
        lines = []
        for n in names[:300]:
            full = os.path.join(p, n)
            if os.path.isdir(full):
                lines.append("[目录] " + n + "/")
            else:
                try:
                    sz = os.path.getsize(full)
                except OSError:
                    sz = 0
                lines.append("[文件] " + n + "  " + _size(sz))
        more = "" if len(names) <= 300 else "\n……还有 %d 项未列出" % (len(names) - 300)
        return ("目录 " + _rel(cfg, p) + "：\n" + ("\n".join(lines) or "（空目录）") + more,
                "列目录 · " + _rel(cfg, p))

    if name == "read_file":
        p = _safe_path(cfg, args.get("path"))
        if not os.path.isfile(p):
            raise ValueError("文件不存在：" + _rel(cfg, p))
        size = os.path.getsize(p)
        if size > 5 * 1024 * 1024:
            raise ValueError("文件太大（%s），不支持直接读取" % _size(size))
        txt, _ = _read_text(p)
        note = ""
        if len(txt) > 60000:
            txt = txt[:60000]
            note = "\n\n……（文件较长，只读到前 60000 个字符）"
        return txt + note, "读文件 · " + _rel(cfg, p)

    if name == "write_file":
        p = _safe_path(cfg, args.get("path"))
        if _in_data_dir(p, cfg):
            raise ValueError(DATA_GUARD_MSG)
        content = args.get("content")
        if content is None:
            raise ValueError("content 不能为空")
        existed = os.path.exists(p)
        nl = "\n"
        if existed and os.path.isfile(p):
            try:
                _, nl = _read_text(p)
            except Exception:
                nl = "\n"
            # 覆盖前留一份 .bak，万一写坏了还能捞回来
            try:
                import shutil
                shutil.copy2(p, p + ".bak")
            except Exception:
                pass
        _write_text(p, content, nl)
        return (("已覆盖写入 " if existed else "已新建 ") + _rel(cfg, p) +
                "（%d 字符）" % len(content),
                ("覆盖 · " if existed else "新建 · ") + _rel(cfg, p))

    if name == "edit_file":
        p = _safe_path(cfg, args.get("path"))
        if not os.path.isfile(p):
            raise ValueError("文件不存在：" + _rel(cfg, p))
        old = args.get("old_text") or ""
        new = args.get("new_text")
        if not old:
            raise ValueError("old_text 不能为空")
        if new is None:
            new = ""
        txt, nl = _read_text(p)
        cnt = txt.count(old)
        if cnt == 0:
            raise ValueError("文件里没找到这段原文。先 read_file 看一眼实际内容再改。")
        if cnt > 1:
            raise ValueError("这段原文出现了 %d 次，不唯一。请多带一点上下文。" % cnt)
        _write_text(p, txt.replace(old, new, 1), nl)
        return "已修改 " + _rel(cfg, p), "改文件 · " + _rel(cfg, p)

    if name == "make_dir":
        p = _safe_path(cfg, args.get("path"))
        os.makedirs(p, exist_ok=True)
        return "已创建目录 " + _rel(cfg, p), "建目录 · " + _rel(cfg, p)

    raise ValueError("未知文件工具：" + name)


def execute_tool(name, args, state, cfg):
    """执行工具，返回 (给模型看的字符串, 给前端看的摘要)。"""
    if name == "remember":
        content = (args.get("content") or "").strip()
        if not content:
            return "记忆内容为空，未写入。", "没有内容"
        item = {
            "id": "m_" + uuid.uuid4().hex[:8],
            "content": content,
            "category": args.get("category") or "其他",
            "created": now_label(),
        }
        state["memories"].append(item)
        return "已记住：" + content, "记住 · " + content

    if name == "forget":
        mid = args.get("id") or ""
        before = len(state["memories"])
        state["memories"] = [m for m in state["memories"] if m["id"] != mid]
        if len(state["memories"]) < before:
            return "已删除记忆 " + mid, "忘掉了一条记忆"
        return "没有找到 id 为 " + mid + " 的记忆。", "没找到"

    if name == "add_task":
        title = (args.get("title") or "").strip()
        if not title:
            return "任务标题为空，未添加。", "没有标题"
        item = {
            "id": "t_" + uuid.uuid4().hex[:8],
            "title": title,
            "due": args.get("due") or "",
            "priority": args.get("priority") or "中",
            "done": False,
            "created": now_label(),
        }
        state["tasks"].append(item)
        return "已添加任务：" + title, "任务 · " + title

    if name == "complete_task":
        tid = args.get("id") or ""
        for t in state["tasks"]:
            if t["id"] == tid:
                t["done"] = True
                return "已完成：" + t["title"], "完成 · " + t["title"]
        return "没有找到 id 为 " + tid + " 的任务。", "没找到"

    if name == "list_tasks":
        if not state["tasks"]:
            return "当前没有任务。", "查看任务"
        lines = []
        for t in state["tasks"]:
            flag = "已完成" if t["done"] else "未完成"
            lines.append("[%s] %s %s %s" % (t["id"], t["title"], t["due"], flag))
        return "\n".join(lines), "查看任务"

    if name == "add_idea":
        text = (args.get("text") or "").strip()
        if not text:
            return "内容为空，没记。", "记灵感"
        state.setdefault("ideas", []).insert(0, {
            "id": "i_" + uuid.uuid4().hex[:8],
            "text": text[:1000],
            "created": now_label(),
            "source": "chat",
            "done": False,
        })
        del state["ideas"][500:]
        save_state(state)
        return "记下了：" + text, "记灵感"

    if name == "list_ideas":
        items = state.get("ideas") or []
        if not items:
            return "还没记过灵感。", "查看灵感"
        lines = []
        for i in items[:30]:
            flag = "（已转任务）" if i.get("done") else ""
            lines.append("[%s] %s %s%s" % (i["id"], i.get("created", "")[-11:], i["text"], flag))
        return "\n".join(lines), "查看灵感"

    if name == "discover_projects":
        try:
            n = int(args.get("count") or 6)
        except Exception:
            n = 6
        try:
            picks, errors = discover_refresh(cfg, limit=max(1, min(8, n)))
            # discover_refresh 是拿磁盘上的新状态写的，聊天这份要跟上，
            # 否则后面 save_chat_state 之前的内容会被旧快照覆盖
            db = load_state()
            state["discover"] = db.get("discover") or state.get("discover")
            state["seen"] = db.get("seen") or state.get("seen")
        except Exception as e:
            return "抓取失败：" + (str(e) or e.__class__.__name__)[:200], "找项目失败"
        if not picks:
            return "这轮没抓到新项目。" + ("；".join(errors)[:200] if errors else ""), "没找到"
        lines = [
            "- [%s] %s：%s（%s）%s" % (p.get("src_name") or p.get("tag"), p["title"],
                                     p["why"], p.get("hot_label") or p["tag"], p["url"])
            for p in picks
        ]
        return "\n".join(lines), "找到 %d 个灵感" % len(picks)

    if name == "list_discover":
        items = state.get("discover") or []
        if not items:
            return "「发现」面板还是空的。", "查看发现"
        try:
            n = max(1, min(60, int(args.get("limit") or 20)))
        except Exception:
            n = 20
        lines = []
        for it in reversed(items[-n:]):
            flags = []
            if it.get("saved"):
                flags.append("已收藏")
            if it.get("done"):
                flags.append("已转任务")
            label = " ".join(x for x in (it.get("title") or "", it.get("hot_label") or "") if x)
            lines.append("[%s] %s｜%s%s" % (
                it["id"], it.get("src_name") or it.get("source") or "", label,
                ("｜" + " ".join(flags)) if flags else ""))
        return "\n".join(lines), "查看发现（%d 条）" % len(items)

    if name == "mark_discover":
        items = state.get("discover") or []
        if not items:
            return "「发现」面板还是空的，没得标记。", "没有可标记的"
        action = (args.get("action") or "").lower()
        key = {"save": ("saved", True), "unsave": ("saved", False),
               "done": ("done", True), "undone": ("done", False)}.get(action)
        if not key:
            return "action 只能是 save / unsave / done / undone。", "参数不对"
        field, val = key
        ids = set(args.get("ids") or [])
        scope = args.get("scope") or ("ids" if ids else "all")
        n = 0
        for it in items:
            if scope == "all" or it["id"] in ids:
                it[field] = val
                n += 1
        save_state(state)
        word = {"saved": "收藏", "done": "看过"}[field]
        verb = "" if val else "取消"
        return ("已%s%s %d 条。" % (verb, word, n)), ("%s%s %d 条" % (verb, word, n))

    if name == "write_diary":
        try:
            return _write_diary(args), "写日记"
        except ValueError as e:
            return str(e), "写日记失败"
        except Exception as e:
            return "写日记失败：" + str(e)[:200], "写日记失败"

    if name == "list_diary":
        try:
            return _list_diary(int(args.get("limit") or 10)), "看日记"
        except Exception as e:
            return "读日记失败：" + str(e)[:200], "读日记失败"

    if name == "daily_todo":
        try:
            return _daily_todo(args), "每日必做"
        except Exception as e:
            return "每日必做出错了：" + str(e)[:200], "每日必做失败"

    if name == "append_project":
        try:
            return _append_project(args), "项目进展"
        except ValueError as e:
            return str(e), "记进展失败"
        except Exception as e:
            return "记进展失败：" + str(e)[:200], "记进展失败"

    if name == "list_skills":
        sk = load_skills()
        if not sk:
            return "本机还没有安装技能。", "查看技能"
        lines = ["- %s（%s）：%s" % (s["name"], s["slug"], s["description"]) for s in sk]
        return "\n".join(lines), "查看技能（%d 个）" % len(sk)

    if name == "use_skill":
        want = args.get("name") or args.get("slug") or ""
        s = find_skill(want)
        if not s:
            names = "、".join(x["name"] for x in load_skills()) or "（没有）"
            return "没有这个技能：" + str(want) + "。现有技能：" + names, "没找到技能"
        return s["body"], "技能 · " + s["name"]

    if name.startswith("mcp__"):
        # 外接的文件系统工具同样不许碰 data/：模型曾经拿着一堆
        # "saved": false 去逐条替换 state.json，改不动就反复重试，把整轮卡死。
        if name.startswith("mcp__filesystem__") and re.search(
                r"(write|edit|create|move|delete)", name):
            for key in ("path", "paths", "source", "destination"):
                v = args.get(key)
                for p in (v if isinstance(v, list) else [v]):
                    if _in_data_dir(p, cfg):
                        return DATA_GUARD_MSG, "拒绝改数据文件"
        out = MCP.call(name, args)
        return out, "MCP · " + name.split("__", 2)[-1]

    if name in ("list_files", "read_file", "write_file", "edit_file", "make_dir"):
        try:
            return _file_tool(name, args, cfg)
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            return "操作失败：" + msg, "失败 · " + msg[:60]

    return "未知工具：" + name, "未知工具"


# --------------------------------------------------------------------------
# 系统提示词
# --------------------------------------------------------------------------

def build_system(state, cfg):
    mem = state["memories"]
    tasks = [t for t in state["tasks"] if not t["done"]]
    ideas_list = state.get("ideas") or []
    ws = os.path.realpath((cfg.get("workspace") or "").strip() or DEFAULT_WORKSPACE)

    mem_txt = "\n".join("- [%s] %s" % (m["category"], m["content"]) for m in mem[-40:]) or "（还什么都没记住）"
    task_txt = "\n".join(
        "- [%s] %s%s %s" % (t["id"], t["title"], (" · " + t["due"]) if t["due"] else "", t["priority"])
        for t in tasks
    ) or "（今天没有待办）"
    idea_txt = "\n".join(
        "- %s %s" % (i.get("created", "")[-11:], i["text"])
        for i in ideas_list if not i.get("done")
    ) or "（还没记过灵感）"

    picks = (state.get("discover") or [])[-5:]
    pick_txt = "\n".join(
        "- [%s] %s：%s" % (p.get("src_name") or "GitHub", p.get("title"), p.get("why"))
        for p in reversed(picks)
    ) or "（还没有推荐过项目）"

    skills = load_skills()
    skill_txt = "\n".join(
        "- %s：%s" % (s["name"], s["description"]) for s in skills
    ) or "（没有安装技能）"

    return (
        "你是「知更」，用户的个人 AI 智能体，不是一个通用聊天机器人。\n"
        "现在是 " + now_label() + "。\n\n"
        "你的行为准则：\n"
        "1. 有记忆：遇到值得长期记住的事（人物、项目、偏好、承诺），主动调用 remember 存下来，不要问「要不要记住」。\n"
        "2. 有行动：用户提到要做的事，顺手用 add_task 变成任务；用户说做完了，用 complete_task 划掉。\n"
        "3. 说人话：简洁、具体、有温度。不要每次都列一堆条目标题，除非内容真的需要分点。\n"
        "4. 不编造：不知道就直说，不要假装查过。\n"
        "5. 能动手就别只动嘴：用户让你看、改、写文件时，直接用工具做，做完告诉他改了哪个文件、改了什么。\n\n"
        "【你的工作目录】" + ws + "\n"
        "文件工具的路径都相对于它。你只能在这个目录（含子目录）里操作，越界会被拒绝。\n"
        "改文件的铁律：先 read_file 看现状，再 edit_file 做局部替换；"
        "只有整份重写才用 write_file。old_text 要足够长以保证唯一。\n"
        "改完务必用一句话说清楚：改了哪个文件、哪一处、改成了什么。\n"
        "铁律：data/ 目录是知更 自己的数据库（state.json 等），任何文件工具都不许写它——"
        "发现面板用 list_discover / mark_discover，记忆和任务用对应工具。\n\n"
        "【可用技能】任务和下面某条描述对得上时，先 use_skill 读它的完整指南，再按里面的步骤和铁律做，"
        "不要凭印象假装读过。\n" + skill_txt + "\n\n"
        "【外部工具】名字以 mcp__ 开头的是外接的 MCP 服务器提供的工具（文件系统、知识图谱记忆、分步推理等），"
        "用法和内置工具一样，失败就如实告诉用户，不要重试超过两次。\n\n"
        "【长期记忆】\n" + mem_txt + "\n\n"
        "【今天的任务】\n" + task_txt + "\n\n"
        "【灵感速记】（用户按 Ctrl+Alt+R 说一句，或随口一提，就会记在这里）\n" + idea_txt + "\n\n"
        "【最近发现】左侧「发现」面板里的项目，聊到相关话题可以参考，别硬塞。\n" + pick_txt
    )


# --------------------------------------------------------------------------
# Anthropic 调用
# --------------------------------------------------------------------------

def stream_anthropic(cfg, messages, system, state, emit):
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("没有安装 anthropic 库，请先运行：py -m pip install anthropic")

    client = anthropic.Anthropic(
        api_key=cfg["api_key"],
        # 显式指定，避免继承本机 ANTHROPIC_BASE_URL 之类的环境变量
        base_url=(cfg.get("base_url") or "").strip() or "https://api.anthropic.com",
        timeout=300.0,
        max_retries=1,
    )
    kwargs = dict(
        model=cfg.get("model") or "claude-opus-5-5",
        max_tokens=8000,
        system=system,
        tools=all_tools(),
        messages=messages,
    )

    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                emit({"t": "delta", "text": event.delta.text})
        final = stream.get_final_message()

    payload = json.loads(final.to_json())
    return payload.get("content", []), payload.get("stop_reason")


# --------------------------------------------------------------------------
# OpenAI 兼容调用（DeepSeek / Kimi / 通义 / 智谱 / OpenAI …）
# --------------------------------------------------------------------------

def to_openai_messages(system, messages):
    out = [{"role": "system", "content": system}]
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            texts, calls = [], []
            for b in content or []:
                if b.get("type") == "text":
                    texts.append(b.get("text", ""))
                elif b.get("type") == "tool_use":
                    calls.append({
                        "id": b["id"],
                        "type": "function",
                        "function": {
                            "name": b["name"],
                            "arguments": json.dumps(b.get("input", {}), ensure_ascii=False),
                        },
                    })
            msg = {"role": "assistant", "content": "".join(texts) or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user
            pending_text = []
            for b in content or []:
                if b.get("type") == "text":
                    pending_text.append(b.get("text", ""))
                elif b.get("type") == "tool_result":
                    out.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id"),
                        "content": b.get("content") or "",
                    })
            if pending_text:
                out.append({"role": "user", "content": "".join(pending_text)})
    return out


def _post_stream(url, headers, body, on_data):
    data = json.dumps(body).encode("utf-8")
    req = urlreq.Request(url, data=data, headers=headers, method="POST")
    try:
        # 300 秒太长：网络一卡，界面就干等五分钟。90 秒足够出一段流了。
        resp = urlreq.urlopen(req, timeout=90)
    except urlerr.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(raw)
            msg = (j.get("error") or {}).get("message") or raw
        except Exception:
            msg = raw
        raise RuntimeError("接口返回 %s：%s" % (e.code, str(msg)[:300]))
    except urlerr.URLError as e:
        raise RuntimeError("连不上接口（%s）。如果用的是国外服务，先开代理再试。" % e.reason)
    with resp:
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    on_data(json.loads(chunk))
                except Exception:
                    continue
        except (TimeoutError, OSError) as e:
            # 流到一半断了：别让界面一直转圈，说人话
            raise RuntimeError("网络中途断了（%s）。检查一下网络或代理，再发一次。" % e)


def stream_openai(cfg, messages, system, state, emit):
    base = (cfg.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
    url = base + "/chat/completions"
    body = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": to_openai_messages(system, messages),
        "tools": [tool_to_openai(t) for t in all_tools()],
        "stream": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["api_key"],
    }

    text_parts = []
    calls = {}

    def on_data(obj):
        for ch in obj.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("content"):
                text_parts.append(d["content"])
                emit({"t": "delta", "text": d["content"]})
            for tc in d.get("tool_calls") or []:
                i = tc.get("index", 0)
                slot = calls.setdefault(i, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]

    _post_stream(url, headers, body, on_data)

    blocks = []
    joined = "".join(text_parts)
    if joined:
        blocks.append({"type": "text", "text": joined})
    for _, c in sorted(calls.items()):
        try:
            args = json.loads(c["args"]) if c["args"] else {}
        except Exception:
            args = {}
        blocks.append({
            "type": "tool_use",
            "id": c["id"] or ("call_" + uuid.uuid4().hex[:8]),
            "name": c["name"],
            "input": args,
        })
    stop = "tool_use" if calls else "end_turn"
    return blocks, stop


# --------------------------------------------------------------------------
# Agent 主循环
# --------------------------------------------------------------------------

MAX_STEPS = 12   # 一轮对话里最多连续调用工具几次


def save_chat_state(state):
    """聊天中途写盘前，先从磁盘拿最新的发现/去重记录。

    一次对话可能跑很久，期间用户会在面板里收藏、删除，或者点了「换一批」。
    聊天手里那份 discover 是开场时的旧快照，直接写回去会把这些操作冲掉。
    """
    try:
        db = load_state()
        for k in ("discover", "seen"):
            if k in db:
                state[k] = db[k]
    except Exception:
        pass
    save_state(state)


def _sanitize_tool_pairs(msgs):
    """把「发起过但没等到结果」的 tool_use 剔掉。

    上一轮如果中途断网 / 被杀掉，历史里会留下一条只有 tool_use 的 assistant，
    下一条却是普通用户消息。OpenAI 兼容接口会直接 400，整段对话就废了。
    """
    out = []
    for i, m in enumerate(msgs):
        if m.get("role") == "assistant" and isinstance(m.get("content"), list):
            ids = [b.get("id") for b in m["content"] if b.get("type") == "tool_use"]
            if ids:
                nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                have = set()
                if nxt and nxt.get("role") == "user" and isinstance(nxt.get("content"), list):
                    have = {b.get("tool_use_id") for b in nxt["content"]
                            if b.get("type") == "tool_result"}
                if not set(ids) <= have:
                    keep = [b for b in m["content"]
                            if b.get("type") != "tool_use" or b.get("id") in have]
                    if not keep:
                        continue
                    m = {"role": "assistant", "content": keep}
        out.append(m)
    msgs[:] = out


def run_agent(cfg, state, emit):
    if not cfg.get("api_key"):
        raise RuntimeError("还没有填写 API Key，点击左下角设置。")

    conv = get_conv(state)
    messages = conv["messages"]
    # 定时简报可能先写过一条 assistant，接口要求首条必须是 user
    while messages and messages[0].get("role") != "user":
        messages.pop(0)
    _sanitize_tool_pairs(messages)

    # 网络抖动时给一次重试：只有在「一个字都还没吐出去」时才重来，
    # 否则界面上会出现两段拼接的文字。
    raw_emit = emit
    sent_any = {"v": False}

    def emit(obj):
        if obj.get("t") in ("delta", "tool"):
            sent_any["v"] = True
        raw_emit(obj)

    steps = 0
    seen_calls = {}
    for _ in range(MAX_STEPS):
        steps += 1
        system = build_system(state, cfg)
        for attempt in range(2):
            sent_any["v"] = False
            try:
                if (cfg.get("provider") or "anthropic") == "anthropic":
                    blocks, stop = stream_anthropic(cfg, messages, system, state, emit)
                else:
                    blocks, stop = stream_openai(cfg, messages, system, state, emit)
                break
            except Exception as e:
                msg = str(e) or e.__class__.__name__
                flaky = any(k in msg for k in ("网络", "连不上", "timed out", "timeout",
                                               "SSL", "EOF", "连接", "URL", "10061", "10054"))
                if attempt == 0 and not sent_any["v"] and flaky:
                    time.sleep(2)
                    continue
                raise

        messages.append({"role": "assistant", "content": blocks})

        if stop != "tool_use":
            break

        results = []
        for b in blocks:
            if b.get("type") != "tool_use":
                continue
            name = b.get("name")
            args = b.get("input") or {}
            emit({"t": "tool", "id": b.get("id"), "name": name, "input": args})

            # 同一个工具 + 同一份参数重复第三次，说明它卡住了（以前模型拿着
            # state.json 里一堆相同的 "saved": false 反复改，改不动就无限重试）
            key = name + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
            seen_calls[key] = seen_calls.get(key, 0) + 1
            if seen_calls[key] >= 3:
                out, summary = ("你已经在同一个操作上重复 3 次了，先停下来。"
                                "换个办法，或者直接告诉用户卡在哪。"), "重复操作已拦截"
            else:
                out, summary = execute_tool(name, args, state, cfg)
                save_chat_state(state)
            emit({"t": "tool_result", "id": b.get("id"), "summary": summary})
            emit({"t": "state", "memories": state["memories"], "tasks": state["tasks"]})
            results.append({
                "type": "tool_result",
                "tool_use_id": b.get("id"),
                "content": out,
            })
        if not results:
            break
        messages.append({"role": "user", "content": results})

    # 到步数上限时如果最后一条只有工具调用、一句人话都没说，补一句说明，
    # 否则界面上什么都看不到，用户以为它卡死了
    if steps >= MAX_STEPS and messages:
        last = messages[-1]
        blocks = last.get("content") or []
        if last.get("role") == "assistant" and blocks and not any(
                isinstance(b, dict) and b.get("type") == "text" and b.get("text")
                for b in blocks):
            blocks.insert(0, {"type": "text", "text":
                              "（这轮工具调用到 %d 步上限，先停在这儿。要继续就说「接着弄」。）" % MAX_STEPS})

    # 只保留最近若干条，避免无限增长；裁剪点必须落在「普通用户消息」上，
    # 否则会切散 tool_use / tool_result 配对，下一次请求会被接口拒绝。
    if len(messages) > 60:
        cut = len(messages) - 60
        while cut < len(messages):
            m = messages[cut]
            blocks = m.get("content") or []
            if m.get("role") == "user" and any(
                isinstance(b, dict) and b.get("type") == "text" for b in blocks
            ):
                break
            cut += 1
        if cut < len(messages):
            conv["messages"] = messages[cut:]
    conv["updated"] = now_label()
    save_chat_state(state)


# --------------------------------------------------------------------------
# 定时简报（无人值守，由 Windows 计划任务调用）
# --------------------------------------------------------------------------

BRIEF_ASK = {
    "morning": "（定时任务，无人值守）现在是早上。请按下面这份技能里的【早间 · 四件套】写早间简报。",
    "evening": "（定时任务，无人值守）现在是晚上。请按下面这份技能里的【晚间 · 复盘四件套】写晚间收尾。",
}

BRIEF_SKILL = """【作业指南】
%s

【本次要求】
- 逐条对照记忆清单和任务清单，清单里没有的内容一个字都不要写。
- 时间、日期、任务状态都要准确；拿不准就写「没有记录」。
- 全文 400 字以内，不要开场白，直接给内容。"""


def run_brief(cfg, kind):
    """生成一条简报，写进对话流，返回 (正文, 是否推送成功)。emit 用空函数。"""
    if not cfg.get("api_key"):
        raise RuntimeError("还没有配置 API Key")

    st = load_state()
    system = build_system(st, cfg)
    sk = find_skill("brief")
    guide = sk["body"] if sk else ""
    ask = BRIEF_ASK[kind] + "\n\n" + (BRIEF_SKILL % guide if guide else "")
    msgs = [{"role": "user", "content": [{"type": "text", "text": ask}]}]

    final_text = ""
    used_openai = (cfg.get("provider") or "anthropic") != "anthropic"

    for _ in range(4):
        if used_openai:
            blocks, stop = stream_openai(cfg, msgs, system, st, lambda e: None)
        else:
            blocks, stop = stream_anthropic(cfg, msgs, system, st, lambda e: None)

        spoken = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        if spoken:
            final_text = "\n".join(spoken).strip()

        if stop != "tool_use":
            break

        msgs.append({"role": "assistant", "content": blocks})
        results = []
        for b in blocks:
            if b.get("type") != "tool_use":
                continue
            out, _summary = execute_tool(b.get("name"), b.get("input") or {}, st, cfg)
            results.append({"type": "tool_result", "tool_use_id": b.get("id"), "content": out})
        if not results:
            break
        msgs.append({"role": "user", "content": results})

    if not final_text:
        raise RuntimeError("模型没有返回内容")

    conv = brief_conv(st)
    conv["messages"].append({
        "role": "assistant",
        "content": [{"type": "text", "text": final_text}],
        "brief": kind,
    })
    conv["updated"] = now_label()
    save_state(st)
    return final_text, True




class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """浏览器关连接、刷新页面都会抛 ConnectionReset，别往日志里堆栈。"""
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ---------- 基础 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/state":
            st = load_state()
            conv = get_conv(st)
            return self._json({
                "memories": st["memories"],
                "tasks": st["tasks"],
                "messages": conv["messages"],
                "conversations": [
                    {"id": c["id"], "title": c.get("title") or "新对话",
                     "kind": c.get("kind") or "chat", "updated": c.get("updated") or "",
                     "count": len(c.get("messages") or [])}
                    for c in st["conversations"]
                ],
                "active": conv["id"],
                "now": now_label(),
                "config": cfg_public(),
                "ideas": st.get("ideas") or [],
                "discover": st.get("discover") or [],
                "recorder": recorder().status(),
            })
        if path == "/api/capabilities":
            try:
                mcp_status = MCP.status()
            except Exception as e:
                mcp_status = [{"name": "mcp", "ready": False, "tools": [], "error": str(e)[:200]}]
            return self._json({
                "skills": [{"slug": s["slug"], "name": s["name"], "description": s["description"]}
                           for s in load_skills()],
                "mcp": mcp_status,
                "mcp_file": MCP_FILE,
                "node_modules": os.path.isdir(os.path.join(BASE, "mcp", "node_modules")),
            })
        if path in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if path == "/style.css":
            return self._file("style.css", "text/css; charset=utf-8")
        if path == "/app.js":
            return self._file("app.js", "application/javascript; charset=utf-8")
        return self._send(404, "not found", "text/plain; charset=utf-8")

    def _file(self, name, ctype):
        p = os.path.join(BASE, name)
        if not os.path.exists(p):
            return self._send(404, "missing " + name, "text/plain; charset=utf-8")
        with open(p, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        st = load_state()
        if path.startswith("/api/memory/"):
            mid = path.rsplit("/", 1)[-1]
            st["memories"] = [m for m in st["memories"] if m["id"] != mid]
            save_state(st)
            return self._json({"ok": True})
        if path.startswith("/api/task/"):
            tid = path.rsplit("/", 1)[-1]
            st["tasks"] = [t for t in st["tasks"] if t["id"] != tid]
            save_state(st)
            return self._json({"ok": True})
        if path == "/api/messages":
            get_conv(st)["messages"] = []
            save_state(st)
            return self._json({"ok": True})
        if path.startswith("/api/conv/"):
            cid = path.rsplit("/", 1)[-1]
            now = st["conversations"]
            if len(now) <= 1:
                # 最后一条不删，清空内容就好，免得界面没东西可显示
                now[0]["messages"] = []
                now[0]["title"] = "新对话"
            else:
                st["conversations"] = [c for c in now if c["id"] != cid]
            get_conv(st)
            save_state(st)
            return self._json({"ok": True})
        if path == "/api/config":
            drop_cfg_file()
            return self._json({"ok": True})
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()

        if path == "/api/chat":
            return self._chat(body)

        if path == "/api/shutdown":
            # 给桌面应用用：窗口关掉时让服务自己退场，顺手收掉 MCP 子进程
            self._json({"ok": True})

            def _bye():
                time.sleep(0.3)
                try:
                    MCP.stop_all()
                except Exception:
                    pass
                os._exit(0)

            threading.Thread(target=_bye, daemon=True).start()
            return

        st = load_state()
        if path == "/api/memory":
            content = (body.get("content") or "").strip()
            if not content:
                return self._json({"ok": False, "error": "empty"}, 400)
            mid = body.get("id")
            if mid:
                for m in st["memories"]:
                    if m["id"] == mid:
                        m["content"] = content
            else:
                st["memories"].append({
                    "id": "m_" + uuid.uuid4().hex[:8],
                    "content": content,
                    "category": body.get("category") or "其他",
                    "created": now_label(),
                })
            save_state(st)
            return self._json({"ok": True, "memories": st["memories"]})

        if path == "/api/task":
            tid = body.get("id")
            for t in st["tasks"]:
                if t["id"] == tid:
                    if "done" in body:
                        t["done"] = bool(body["done"])
                    if "title" in body:
                        t["title"] = body["title"]
            save_state(st)
            return self._json({"ok": True, "tasks": st["tasks"]})

        if path == "/api/task/new":
            title = (body.get("title") or "").strip()
            if not title:
                return self._json({"ok": False}, 400)
            st["tasks"].append({
                "id": "t_" + uuid.uuid4().hex[:8],
                "title": title,
                "due": body.get("due") or "",
                "priority": "中",
                "done": False,
                "created": now_label(),
            })
            save_state(st)
            return self._json({"ok": True, "tasks": st["tasks"]})

        if path == "/api/reset":
            save_state(_empty_state())
            return self._json({"ok": True})

        if path == "/api/conv":
            action = body.get("action") or "new"
            if action == "new":
                c = _new_conv()
                st["conversations"].insert(0, c)
                st["active"] = c["id"]
            elif action == "switch":
                cid = body.get("id") or ""
                if any(c["id"] == cid for c in st["conversations"]):
                    st["active"] = cid
            elif action == "rename":
                c = get_conv(st, body.get("id"))
                title = (body.get("title") or "").strip()
                if title:
                    c["title"] = title[:40]
            save_state(st)
            return self._json({"ok": True, "active": get_conv(st)["id"]})

        if path == "/api/config":
            cfg = merged_cfg(body)
            if not cfg.get("api_key"):
                drop_cfg_file()
                return self._json({"ok": True, "saved": False, "config": cfg_public()})
            save_cfg_file(cfg)
            return self._json({"ok": True, "saved": True, "config": cfg_public()})

        if path == "/api/idea":
            action = body.get("action") or "add"
            if action == "add":
                item = add_idea(body.get("text"), body.get("source") or "typed")
                st = load_state()
                return self._json({"ok": bool(item), "ideas": st.get("ideas") or [], "item": item})
            if action == "delete":
                st["ideas"] = [i for i in (st.get("ideas") or []) if i["id"] != body.get("id")]
                save_state(st)
                return self._json({"ok": True, "ideas": st["ideas"]})
            if action == "task":
                ok = idea_to_task(body.get("id") or "")
                st = load_state()
                return self._json({"ok": ok, "ideas": st.get("ideas") or [],
                                   "tasks": st["tasks"]})
            return self._json({"ok": False, "error": "未知操作"}, 400)

        if path == "/api/discover":
            action = body.get("action") or "refresh"
            if action == "refresh":
                try:
                    limit = int(body.get("limit") or 8)
                except Exception:
                    limit = 8
                cfg = merged_cfg(body.get("config"))
                picks, errors = discover_refresh(cfg, limit=max(1, min(8, limit)))
                st = load_state()
                return self._json({"ok": bool(picks), "picks": picks,
                                   "discover": st.get("discover") or [],
                                   "errors": errors})
            if action == "save":
                iid = body.get("id") or ""
                for it in st.get("discover") or []:
                    if it["id"] == iid:
                        it["saved"] = not it.get("saved")
                save_state(st)
                return self._json({"ok": True, "discover": st.get("discover") or []})
            if action == "delete":
                st["discover"] = [i for i in (st.get("discover") or [])
                                  if i.get("id") != (body.get("id") or "")]
                save_state(st)
                return self._json({"ok": True, "discover": st["discover"]})
            if action == "task":
                iid = body.get("id") or ""
                for it in st.get("discover") or []:
                    if it["id"] != iid:
                        continue
                    st["tasks"].append({
                        "id": "t_" + uuid.uuid4().hex[:8],
                        "title": ("看看 " + it.get("title", ""))[:60],
                        "due": "",
                        "priority": "低",
                        "done": False,
                        "created": now_label(),
                    })
                    it["done"] = True
                    save_state(st)
                    return self._json({"ok": True, "discover": st["discover"],
                                       "tasks": st["tasks"]})
                return self._json({"ok": False, "error": "没找到这条"}, 404)
            return self._json({"ok": False, "error": "未知操作"}, 400)

        if path == "/api/open":
            url = (body.get("url") or "").strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                return self._json({"ok": False, "error": "只允许 http/https 链接"}, 400)
            try:
                if os.name == "nt":
                    os.startfile(url)  # noqa: 默认浏览器打开，别让 WebView 自己导航
                else:
                    import webbrowser
                    webbrowser.open(url)
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:200]}, 500)

        if path == "/api/record":
            action = body.get("action") or "toggle"
            rec = recorder()
            if action == "toggle":
                rec.toggle()
            elif action == "start":
                rec.start()
            elif action == "stop":
                rec.stop()
            return self._json({"ok": True, "recorder": rec.status()})

        if path == "/api/mcp":
            action = body.get("action") or "status"
            try:
                mcp = MCP.restart() if action == "reload" else MCP.status()
                return self._json({"ok": True, "mcp": mcp})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:300]}, 500)

        if path == "/api/schedule":
            action = body.get("action") or "status"
            if action not in ("install", "remove", "status"):
                return self._json({"ok": False, "output": "未知操作"}, 400)
            ps1 = os.path.join(BASE, "安装定时简报.ps1")
            if not os.path.exists(ps1):
                return self._json({"ok": False, "output": "找不到 安装定时简报.ps1"}, 500)
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", ps1, "-Task", action],
                    capture_output=True, timeout=90, creationflags=0x08000000,
                )
                out = (r.stdout or b"").decode("utf-8", "replace")
                err = (r.stderr or b"").decode("utf-8", "replace")
                text = (out + err).strip()
                return self._json({"ok": r.returncode == 0, "output": text[-1500:]})
            except Exception as e:
                return self._json({"ok": False, "output": str(e)}, 500)

        return self._json({"ok": False, "error": "not found"}, 404)

    # ---------- 聊天（SSE） ----------
    def _chat(self, body):
        text = (body.get("text") or "").strip()
        cfg = merged_cfg(body.get("config"))   # 前端没带 Key 就用落盘那份
        if not text:
            return self._json({"ok": False, "error": "empty"}, 400)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # SSE 没有 Content-Length，也不是分块传输：必须靠关闭连接告诉浏览器「说完了」。
        # 否则 HTTP/1.1 长连接会让前端永远等不到流结束，发送键一直锁死。
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        def emit(obj):
            try:
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        st = load_state()
        conv = get_conv(st, body.get("conv"))
        conv["messages"].append({"role": "user", "content": [{"type": "text", "text": text}]})
        if (conv.get("title") or "新对话") in ("新对话", ""):
            conv["title"] = _msg_title(conv["messages"])
        conv["updated"] = now_label()
        save_state(st)

        try:
            run_agent(cfg, st, emit)
            emit({"t": "done", "messages": conv["messages"][-2:]})
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            emit({"t": "error", "message": msg})
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception:
            pass


def main():
    headless = "--headless" in sys.argv
    if not headless:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            # pythonw.exe 下 sys.stdout 是 None，任何 print 都会炸，这里兜个空流
            import io
            sys.stdout = io.StringIO()
            try:
                sys.stderr = io.StringIO()
            except Exception:
                pass
    os.makedirs(DATA_DIR, exist_ok=True)
    seed_mcp_config()
    try:
        srv = LocalServer(("127.0.0.1", PORT), Handler)
    except OSError:
        if headless:
            return
        print("端口 %d 已被占用——看起来「知更」已经在运行了。" % PORT)
        print("正在打开页面…")
        try:
            webbrowser.open("http://127.0.0.1:%d" % PORT)
        except Exception:
            pass
        time.sleep(2)
        return
    srv.daemon_threads = True
    # 灵感速记的全局热键（Ctrl+Alt+R）。注册失败不影响界面上的录音按钮
    try:
        recorder().start_hotkey()
    except Exception:
        pass
    if not headless:
        print("「知更」已启动： http://127.0.0.1:%d" % PORT)
        print("关闭这个窗口即可停止。")
        try:
            webbrowser.open("http://127.0.0.1:%d" % PORT)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            MCP.stop_all()
        except Exception:
            pass


if __name__ == "__main__":
    main()
