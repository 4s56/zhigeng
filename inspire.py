# -*- coding: utf-8 -*-
"""发现 —— 从一堆平台上采灵感，挑几个推给用户。

信源（能直连的走直连，墙外的配了代理才抓）：
    墙外：GitHub（搜索 + Trending）、Hacker News、Product Hunt、Reddit、阮一峰周刊
    国内：少数派、InfoQ 中文、爱范儿、量子位、V2EX、掘金

流程：并发抓各平台 → 去重 → 每个来源限量 → 模型挑 8 个写推荐语
     → 存进 state["discover"]，气泡推送。

用法：
    py inspire.py run            抓一轮（定时任务 / 手动）
    py inspire.py run --force    忽略「推过了」的记录
    py inspire.py sources        看看每个信源通不通
"""

import hashlib
import html as htmllib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import ideas  # noqa: E402  （非阻塞气泡）

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 兴趣画像的默认值：策展时告诉模型，别推一堆跟用户不相干的东西。
# 想换成自己的口味，在 data/config.json 里写 "interests": "……" 覆盖即可。
DEFAULT_PROFILE = (
    "一个喜欢动手做东西的学生。喜欢写代码、做网页和小游戏、AI 工具、"
    "3D 与可视化。偏好：能直接上手玩的、开源可跑的小而美项目，"
    "以及有启发的技术文章；不喜欢纯商业新闻和营销榜单。"
)

MAX_CANDIDATES = 100
PICK_COUNT = 8
# 让模型多挑几个（按推荐程度排序），再按「一家最多 3 条」裁到 8 个，
# 否则它容易一口气挑 6 个 GitHub，别的平台白抓了
RAW_PICK = 12
MAX_PER_SRC = 3

# 每个来源最多留几条，防止 GitHub 一家把候选池塞满
PER_SOURCE = {
    "GitHub": 26, "Hacker News": 12, "Product Hunt": 10, "Reddit": 10,
    "阮一峰周刊": 8, "少数派": 10, "InfoQ": 8, "爱范儿": 10, "量子位": 8,
    "V2EX": 8, "掘金": 8,
}

_OPENER = {}


# --------------------------------------------------------------------------
# 网络
# --------------------------------------------------------------------------

def proxy_setting():
    """代理：环境变量优先，其次 data/config.json 里的 "proxy"。"""
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    try:
        import server
        return (server.load_cfg_file().get("proxy") or "").strip()
    except Exception:
        return ""


def _opener():
    p = proxy_setting()
    if p not in _OPENER:
        handlers = []
        if p:
            handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
        _OPENER[p] = urllib.request.build_opener(*handlers)
    return _OPENER[p]


def _http(url, headers=None, timeout=15, data=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with _opener().open(req, timeout=timeout) as r:
        return r.read()


def _json(url, headers=None, timeout=15, data=None):
    return json.loads(_http(url, headers, timeout, data).decode("utf-8", "replace"))


def gh_token():
    """优先用本机 gh 的登录态（5000 次/时），没有就匿名（经常被限流）。"""
    tok = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, timeout=10,
                           creationflags=0x08000000)
        if r.returncode == 0:
            return (r.stdout or b"").decode("utf-8", "replace").strip()
    except Exception:
        pass
    return ""


def _strip_track(url):
    """去掉 utm_* 之类的跟踪参数，打开时干净一点。"""
    try:
        u = urllib.parse.urlsplit(url)
        q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("from", "spm", "share_source")]
        return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path,
                                        urllib.parse.urlencode(q), u.fragment))
    except Exception:
        return url


def _mk(src, src_name, title, url, desc="", score=0, hot_label="", lang="",
        created="", label=""):
    url = _strip_track(url or "")
    key = (url or title).strip()
    return {
        "id": "%s:%s" % (src, hashlib.md5(key.encode("utf-8")).hexdigest()[:12]),
        "source": src,
        "src_name": src_name,
        "title": (title or "").strip()[:160],
        "url": (url or "").strip(),
        "desc": re.sub(r"\s+", " ", (desc or "")).strip()[:180],
        "stars": int(score or 0),
        "hot_label": hot_label or "",
        "lang": lang or "",
        "created": (created or "")[:10],
        "label": label or src_name,
    }


def _clean_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(re.sub(r"\s+", " ", s)).strip()
    # 各家 feed 的尾巴：PH 的 "Discussion | Link"、爱范儿的公众号宣传、量子位的「— 完 —」
    s = re.sub(r"\s*(Discussion\s*\|\s*Link|Comments? URL|Read more)\s*$", "", s, flags=re.I)
    for cut in ("#欢迎关注", "— 完 —", "- 完 -", "版权所有，未经授权"):
        if cut in s:
            s = s.split(cut)[0].strip()
    return s


# --------------------------------------------------------------------------
# 各信源
# --------------------------------------------------------------------------

def gh_search_queries():
    today = date.today()
    subs = {
        "d21": (today - timedelta(days=21)).isoformat(),
        "d90": (today - timedelta(days=90)).isoformat(),
        "d180": (today - timedelta(days=180)).isoformat(),
        "d365": (today - timedelta(days=365)).isoformat(),
    }
    return [
        ("新项目", "created:>{d21} stars:>60", 20),
        ("三个月内冒头", "created:>{d90} stars:>600", 14),
        ("AI 工具", "topic:llm created:>{d180} stars:>150", 10),
        ("网页/可视化", "topic:webgl created:>{d365} stars:>120", 8),
        ("游戏", "topic:game created:>{d180} stars:>150", 8),
    ], subs


def fetch_github_search(token):
    """GitHub 搜索 API —— 不需要代理。"""
    queries, subs = gh_search_queries()
    out = []

    def one(q):
        label, query, n = q
        url = ("https://api.github.com/search/repositories?"
               + urllib.parse.urlencode({"q": query.format(**subs), "sort": "stars",
                                         "order": "desc", "per_page": n}))
        h = {"Accept": "application/vnd.github+json"}
        if token:
            h["Authorization"] = "Bearer " + token
        return label, (_json(url, h).get("items") or [])

    with ThreadPoolExecutor(max_workers=3) as ex:
        for label, items in ex.map(one, queries):
            for r in items:
                full = r.get("full_name") or ""
                if not full:
                    continue
                out.append(_mk(
                    "github", "GitHub", full,
                    r.get("html_url") or ("https://github.com/" + full),
                    r.get("description") or "",
                    r.get("stargazers_count") or 0,
                    "★ %s" % _short(r.get("stargazers_count") or 0),
                    r.get("language") or "",
                    (r.get("created_at") or "")[:10],
                    label,
                ))
    return out


def fetch_github_trending():
    """GitHub Trending 网页 —— github.com 国内被墙，配了代理才抓得到。"""
    page = _http("https://github.com/trending?since=daily", timeout=20).decode("utf-8", "replace")
    out = []
    for block in page.split('<article class="Box-row"')[1:]:
        # 仓库链接在 <h2> 里的第一个 <a>；别用全文第一个 href，
        # 赞助位 / 头像会抢先匹配，抓出 /sponsors/xxx 这种东西
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="(/[^/"]+/[^/"]+)"', block)
        if not m:
            continue
        full = m.group(1).strip("/")
        if full.count("/") != 1 or full.split("/")[0] in (
                "sponsors", "topics", "collections", "trending", "marketplace",
                "features", "about", "pricing", "login", "settings", "orgs"):
            continue
        d = re.search(r'<p[^>]*class="[^"]*col-9[^"]*"[^>]*>([\s\S]*?)</p>', block)
        s = re.search(r'href="/[^"]+/stargazers"[^>]*>([\s\S]*?)</a>', block)
        stars = 0
        if s:
            num = re.sub(r"[^\d]", "", _clean_html(s.group(1)).replace(",", ""))
            stars = int(num or 0)
        out.append(_mk(
            "github", "GitHub", full, "https://github.com/" + full,
            _clean_html(d.group(1)) if d else "",
            stars, "★ %s" % _short(stars), "", date.today().isoformat(), "Trending",
        ))
    return out


def fetch_hn():
    """Hacker News 首页（Algolia 一次拿全，不需要代理）。"""
    d = _json("https://hn.algolia.com/api/v1/search?"
              + urllib.parse.urlencode({"tags": "front_page", "hitsPerPage": 30}),
              timeout=20)
    out = []
    for h in d.get("hits") or []:
        title = (h.get("title") or "").strip()
        pts = int(h.get("points") or 0)
        if not title or pts < 60:
            continue
        oid = str(h.get("objectID") or "")
        out.append(_mk(
            "hn", "Hacker News", title,
            (h.get("url") or "").strip() or ("https://news.ycombinator.com/item?id=" + oid),
            "", pts, "%d 分" % pts, "", (h.get("created_at") or "")[:10], "HN",
        ))
    return out


def _rss(xml_bytes, limit):
    """RSS 2.0 / Atom 通用解析，返回 [(标题, 链接, 摘要, 时间)]。"""
    root = ET.fromstring(xml_bytes)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for it in root.findall(".//item")[:limit]:
        out.append((
            it.findtext("title") or "",
            (it.findtext("link") or "").strip(),
            _clean_html(it.findtext("description") or "")[:200],
            it.findtext("pubDate") or "",
        ))
    for e in root.findall(".//a:entry", ns)[:limit]:
        link = ""
        for ln in e.findall("a:link", ns):
            if ln.get("rel") in (None, "alternate"):
                link = ln.get("href") or link
        out.append((
            e.findtext("a:title", default="", namespaces=ns),
            link,
            _clean_html(e.findtext("a:summary", default="", namespaces=ns)
                        or e.findtext("a:content", default="", namespaces=ns))[:200],
            e.findtext("a:updated", default="", namespaces=ns),
        ))
    return out


def fetch_rss_source(src, src_name, feed_url, limit, label=None, hot_label=""):
    items = _rss(_http(feed_url, timeout=20), limit)
    out = []
    for title, link, desc, pub in items:
        if not title or not link:
            continue
        out.append(_mk(src, src_name, title, link, desc, 0, hot_label, "",
                       _fmt_date(pub), label or src_name))
    return out


def fetch_ruanyf():
    """阮一峰周刊：feedburner 被墙，官网列表页能直连。"""
    page = _http("https://www.ruanyifeng.com/blog/weekly/", timeout=20).decode("utf-8", "replace")
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(https://www\.ruanyifeng\.com/blog/\d{4}/\d{2}/weekly-issue-(\d+)\.html)"[^>]*>([\s\S]*?)</a>', page):
        url, num, text = m.group(1), m.group(2), _clean_html(m.group(3))
        if not text or url in seen:
            continue
        seen.add(url)
        out.append(_mk("ruanyf", "阮一峰周刊", text, url, "", int(num),
                       "第 %s 期" % num, "", "", "周刊"))
        if len(out) >= 6:
            break
    return out


def fetch_reddit():
    """Reddit —— 国内被墙，配了代理才抓得到。"""
    subs = "SideProject+LocalLLaMA+webdev+InternetIsBeautiful+gamedev"
    d = _json("https://www.reddit.com/r/%s/top.json?t=day&limit=25" % subs, timeout=20)
    out = []
    for c in (d.get("data") or {}).get("children") or []:
        it = c.get("data") or {}
        title = (it.get("title") or "").strip()
        if not title or it.get("over_18"):
            continue
        out.append(_mk(
            "reddit", "Reddit", title,
            "https://www.reddit.com" + (it.get("permalink") or ""),
            it.get("selftext") or "",
            it.get("score") or 0,
            "▲ %s" % _short(it.get("score") or 0),
            "", "", "r/" + (it.get("subreddit") or ""),
        ))
    return out


def fetch_v2ex():
    """V2EX 热门 —— 国内不少网络也会被挡，抓不到就算了。"""
    d = _json("https://www.v2ex.com/api/topics/hot.json", timeout=20)
    out = []
    for t in d[:20]:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        node = ((t.get("node") or {}).get("title") or "").strip()
        out.append(_mk(
            "v2ex", "V2EX", title,
            t.get("url") or ("https://www.v2ex.com/t/" + str(t.get("id"))),
            t.get("content") or "",
            t.get("replies") or 0,
            "%d 回复" % (t.get("replies") or 0),
            "", "", node or "V2EX",
        ))
    return out


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------

def _short(n):
    n = int(n or 0)
    return ("%.1fk" % (n / 1000.0)).replace(".0k", "k") if n >= 1000 else str(n)


def _fmt_date(s):
    s = (s or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return "%s-%s-%s" % m.groups()
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return ""


# (键, 中文名, 抓取函数, 是否需要代理)
SOURCES = [
    ("github_search", "GitHub", "search", False),
    ("gh_trending", "GitHub Trending", "trending", True),
    ("hn", "Hacker News", "hn", False),
    ("ph", "Product Hunt", "ph", False),
    ("ruanyf", "阮一峰周刊", "ruanyf", False),
    ("sspai", "少数派", "sspai", False),
    ("infoq", "InfoQ", "infoq", False),
    ("ifanr", "爱范儿", "ifanr", False),
    ("qbitai", "量子位", "qbitai", False),
    ("v2ex", "V2EX", "v2ex", True),
    ("juejin", "掘金", "juejin", False),
    ("reddit", "Reddit", "reddit", True),
]


def _run_source(fn, token=None):
    if fn == "search":
        return fetch_github_search(token)
    if fn == "trending":
        return fetch_github_trending()
    if fn == "hn":
        return fetch_hn()
    if fn == "ph":
        return fetch_rss_source("ph", "Product Hunt", "https://www.producthunt.com/feed", 12,
                                "Product Hunt", "今日上新")
    if fn == "ruanyf":
        return fetch_ruanyf()
    if fn == "sspai":
        return fetch_rss_source("sspai", "少数派", "https://sspai.com/feed", 14, "少数派")
    if fn == "infoq":
        return fetch_rss_source("infoq", "InfoQ", "https://www.infoq.cn/feed", 10, "InfoQ 中文")
    if fn == "ifanr":
        return fetch_rss_source("ifanr", "爱范儿", "https://www.ifanr.com/feed", 12, "爱范儿")
    if fn == "qbitai":
        return fetch_rss_source("qbitai", "量子位", "https://www.qbitai.com/feed", 10, "量子位")
    if fn == "v2ex":
        return fetch_v2ex()
    if fn == "juejin":
        return fetch_rss_source("juejin", "掘金", "https://juejin.cn/rss", 12, "掘金")
    if fn == "reddit":
        return fetch_reddit()
    raise RuntimeError("未知来源 " + fn)


def collect(token=None):
    """并发抓所有信源。返回 (候选, 错误列表)。软失败（某个平台挂了）不算错。"""
    token = token if token is not None else gh_token()

    def one(src):
        key, name, fn, _needs_proxy = src
        try:
            return name, _run_source(fn, token), ""
        except Exception as e:
            # 国内网络经常「一阵风」：TLS 被掐、连接被拒，隔一两秒再来可能就通了
            try:
                time.sleep(1.5)
                return name, _run_source(fn, token), ""
            except Exception as e2:
                msg = str(e2) or e2.__class__.__name__
                return name, [], "%s：%s" % (name, msg[:100])

    buckets, errors = {}, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for name, got, err in ex.map(one, SOURCES):
            if err:
                errors.append(err)
            # 同一来源里按 id 去重
            seen, keep = set(), []
            for c in got:
                if c["id"] in seen:
                    continue
                seen.add(c["id"])
                keep.append(c)
            buckets[name] = keep[:PER_SOURCE.get(name, 8)]

    # 按来源轮流取（每家第 1 名、再每家第 2 名…），别让 GitHub 把候选池占满
    order = [name for _k, name, _f, _p in SOURCES]
    cands, i = [], 0
    while len(cands) < MAX_CANDIDATES and any(i < len(buckets.get(n, [])) for n in order):
        for n in order:
            b = buckets.get(n, [])
            if i < len(b):
                cands.append(b[i])
                if len(cands) >= MAX_CANDIDATES:
                    break
        i += 1
    return cands, errors


# --------------------------------------------------------------------------
# 策展
# --------------------------------------------------------------------------

CURATE_SYSTEM = (
    "你是一个给个人用户挑灵感的策展人。你只从给定候选里挑，不编造候选之外的东西。"
)

CURATE_PROMPT = """这位用户是：%s

下面是从各平台抓来的候选（项目、文章、帖子混在一起）：
%s

请按推荐程度从高到低挑出 %d 个最值得推给他的，要求：
1. 覆盖面广：至少来自 4 个不同平台，同一个平台最多 3 个；
2. 优先「有意思、有启发、能上手玩/用/改」的：开源小项目、创意工具、有洞见的技术文章；
3. 跳过：纯公司新闻、融资消息、跑分榜单、营销软文、标题党、明显是赞助位的；
4. 用户是电子信息大一、爱做网页和游戏，能对上他口味的优先。

只输出一个 JSON 数组，不要解释、不要 markdown 代码块。每项格式：
{"n": 候选编号, "why": "20-40字中文，直接说他能拿它干什么或哪里有意思", "tag": "2-6字类别"}
"""


def _profile():
    try:
        import server
        p = (server.load_cfg_file().get("interests") or "").strip()
        if p:
            return p
    except Exception:
        pass
    return DEFAULT_PROFILE


def _candidate_line(i, c):
    bits = []
    if c["hot_label"]:
        bits.append(c["hot_label"])
    if c["lang"]:
        bits.append(c["lang"])
    if c["created"]:
        bits.append(c["created"])
    tail = (" " + c["desc"]) if c["desc"] else ""
    return "%d. [%s] %s（%s）%s" % (i + 1, c["src_name"], c["title"],
                                    " · ".join(bits), tail)


def _parse_picks(text, n):
    m = re.search(r"\[[\s\S]*\]", text or "")
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for it in arr:
        try:
            idx = int(it.get("n")) - 1
        except Exception:
            continue
        if 0 <= idx < n:
            out.append((idx, str(it.get("why") or "").strip(), str(it.get("tag") or "").strip()))
    return out


def chat_once(cfg, system, prompt, max_tokens=3000):
    """一次普通对话调用（不带工具），返回纯文本。"""
    if not cfg.get("api_key"):
        raise RuntimeError("没有配置 API Key")

    if (cfg.get("provider") or "anthropic") == "anthropic":
        import anthropic
        kw = {"api_key": cfg["api_key"]}
        if cfg.get("base_url"):
            kw["base_url"] = cfg["base_url"]
        client = anthropic.Anthropic(**kw)
        r = client.messages.create(
            model=cfg.get("model") or "claude-opus-5-5",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()

    base = (cfg.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
    body = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["api_key"]},
        method="POST",
    )
    try:
        with _opener().open(req, timeout=120) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise RuntimeError("接口返回 %s：%s" % (e.code, raw[:200]))


def curate(cfg, cands):
    """返回 picks 列表；模型不可用就按热度兜底。"""
    lines = "\n".join(_candidate_line(i, c) for i, c in enumerate(cands))
    prompt = CURATE_PROMPT % (_profile(), lines, RAW_PICK)
    raw = []
    # 带思考的模型（deepseek-flash 等）思考太长时正文会空，给两次机会
    for attempt in range(2):
        try:
            text = chat_once(cfg, CURATE_SYSTEM, prompt, max_tokens=3000 + attempt * 1500)
        except Exception:
            break
        raw = _parse_picks(text, len(cands))
        if raw:
            break

    ranked = []
    for idx, why, tag in raw:
        c = dict(cands[idx])
        c["why"] = why or c["desc"][:60] or "看看这个"
        c["tag"] = tag or c["label"]
        ranked.append(c)

    picks = _diversify(ranked, PICK_COUNT) if ranked else []
    if picks:
        return picks

    # 兜底：按来源轮流取前几个，至少别让面板空着
    for c in cands[:PICK_COUNT]:
        c = dict(c)
        c["why"] = c["desc"][:80] or "热度很高，值得扫一眼"
        c["tag"] = c["label"]
        picks.append(c)
    return picks


def _diversify(ranked, limit):
    """按模型给的顺序取，但一家最多 MAX_PER_SRC 条；不够再放宽补满。"""
    out, per, rest = [], {}, []
    for c in ranked:
        k = c.get("src_name") or c.get("source") or ""
        if len(out) < limit and per.get(k, 0) < MAX_PER_SRC:
            per[k] = per.get(k, 0) + 1
            out.append(c)
        else:
            rest.append(c)
    for c in rest:
        if len(out) >= limit:
            break
        out.append(c)
    return out


# --------------------------------------------------------------------------
# 一轮采集
# --------------------------------------------------------------------------

def run_once(cfg, state=None, limit=PICK_COUNT, use_seen=True):
    """完整跑一轮：抓 → 挑 → 写 state。返回 (picks, errors)。"""
    import server
    own = state is None
    st = state if state is not None else server.load_state()

    seen = set(st.get("seen") or []) if use_seen else set()
    all_cands, errors = collect()
    cands = [c for c in all_cands if c["id"] not in seen]

    # 候选不够（今天没新东西 / 都推过了）：退一步，允许推过的，
    # 但当前面板上已经有的仍然排除，免得点一次「换一批」原地转圈。
    if len(cands) < limit:
        on_board = set(x["id"] for x in (st.get("discover") or []))
        cands = [c for c in all_cands if c["id"] not in on_board]

    if not cands:
        return [], errors or ["没有抓到候选（网络不通？）"]

    picks = curate(cfg, cands)

    now = server.now_label()
    day = date.today().isoformat()
    stamped = []
    for p in picks:
        p["created"] = now
        p["date"] = day
        p.setdefault("saved", False)
        p["done"] = False
        stamped.append(p)

    board = st.get("discover") or []
    have = set(x["id"] for x in board)
    board.extend(p for p in stamped if p["id"] not in have)

    # 面板只留最近 120 条，收藏的一条都不丢（从最旧的开始丢）
    if len(board) > 120:
        over = len(board) - 120
        keep = []
        for it in board:
            if over > 0 and not it.get("saved"):
                over -= 1
                continue
            keep.append(it)
        board = keep
    st["discover"] = board

    seen_now = list(st.get("seen") or [])
    have_seen = set(seen_now)
    for p in stamped:
        if p["id"] not in have_seen:
            seen_now.append(p["id"])
            have_seen.add(p["id"])
    st["seen"] = seen_now[-4000:]

    if own:
        server.save_state(st)
    return stamped, errors


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def log(line):
    try:
        import server
        os.makedirs(server.DATA_DIR, exist_ok=True)
        with open(os.path.join(server.DATA_DIR, "inspire.log"), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M"), line))
    except Exception:
        pass


def check_sources():
    """逐个人源探活：给 README / 排查用。"""
    token = gh_token()
    print("代理：%s" % (proxy_setting() or "（直连）"))
    for key, name, fn, needs_proxy in SOURCES:
        t0 = time.time()
        try:
            got = _run_source(fn, token)
            print("✓ %-14s %2d 条  %.1fs%s" % (name, len(got), time.time() - t0,
                                              "" if got else "（空）"))
        except Exception as e:
            print("✗ %-14s %s%s" % (name, str(e)[:80], "（需要代理）" if needs_proxy else ""))


def main():
    if "--sources" in sys.argv or (len(sys.argv) > 1 and sys.argv[1] == "sources"):
        check_sources()
        return 0

    force = "--force" in sys.argv
    import server
    cfg = server.load_cfg_file()
    if not cfg.get("api_key"):
        log("跳过：没有配置 API Key")
        print("还没有配置 API Key（在「知更」设置里填一次即可）")
        return 1

    picks, errors = [], []
    for attempt in range(3):
        try:
            picks, errors = run_once(cfg, use_seen=not force)
            if picks:
                break
        except Exception as e:
            errors = [str(e)[:200]]
        if attempt < 2:
            time.sleep(20)

    if errors:
        log("部分来源失败：" + "；".join(errors)[:500])

    if not picks:
        log("本轮没有产出")
        ideas.toast("知更 · 发现没更新", ("没抓到新项目：" + "；".join(errors))[:150])
        print("没有产出：" + "；".join(errors))
        return 1

    lines = ["· " + p["title"] + "：" + p["why"] for p in picks[:3]]
    ideas.toast("知更 · 今日发现 %d 个" % len(picks), "\n".join(lines)[:300])
    log("推送 %d 个：%s" % (len(picks), "、".join(p["title"] for p in picks)))
    for p in picks:
        print("- [%s] %s  %s\n  %s" % (p["src_name"], p["title"], p["url"], p["why"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
