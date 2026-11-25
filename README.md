#知更 · 个人 AI 智能体

一个本地运行的私人智能体：**有记忆、会记事、能读写文件、会按点播报**，
装 7 个技能、接 3 个 MCP 服务器。所有数据都在自己电脑上。

名字取自「知更鸟」和「守更」：早上叫你、晚上收尾、白天替你留意。

> 仓库里只有代码。`data/` 整个目录（对话、记忆、日记、API Key、浏览器配置）被 `.gitignore` 排除，
> 第一次运行才会在本机生成。

---

## 从源码跑起来

```bash
pip install -r requirements.txt      # Python 3.10+
cd mcp && npm install                # 三个 MCP 服务器（需要 Node.js 18+）
cd ..
python app.py                        # 桌面窗口；或 python server.py 用浏览器打开
```

第一次打开，在左下角设置里填一次 API Key 就能用。

---

## 怎么打开

| 方式 | 说明 |
|---|---|
| **双击桌面「知更」** | 推荐。原生窗口（WebView2），没有地址栏、没有标签页、没有命令行 |
| 双击 `zhigeng.exe` | 同上（桌面快捷方式指向的就是它） |
| 双击 `启动.bat` | 调试用：命令行起服务，用默认浏览器打开 `http://127.0.0.1:8899` |

窗口关掉，后台服务跟着退出，进程干净。
> 界面和服务是两个进程：`app.py` 只看窗口，`server.py` 管服务。
> 分开是因为 pywebview 的 WinForms 消息循环会把同进程的后台线程饿死（端口连得上、请求不回包）。
**多个对话**：左上「＋ 新对话」开一条新的；左侧对话图标里能看历史、切换、删除。
第一次打开填一次 API Key 就好，之后存在本机 `data/config.json`，重开窗口自动带出。

---

## 支持的服务商

| 服务商 | 接口地址 | 模型填什么 |
|---|---|---|
| Claude（Anthropic 官方） | 留空即可 | `claude-opus-5-5` / `claude-sonnet-5-5` / `claude-haiku-5-5` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` / `deepseek-reasoner` |
| Kimi（月之暗面） | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-plus` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |

只要是「兼容 OpenAI 接口」的服务，把地址和模型填对都能用。
> Claude 官方接口在国内直连大概率不通，需要开代理。

---

## 它能做什么

- **多对话**：每条对话各自一条上下文，互不干扰；标题自动取第一句话；定时简报落在独立的「简报」对话里
- **聊天**：一条连续的对话流，不分窗口、不分话题
- **记忆**：人物、项目、偏好、承诺，自己调用工具存下来；左侧「记忆」可看、可改、可删
- **任务**：说「明天要交作业」，自己加任务；左侧「任务」可勾掉
- **灵感采集**：在任何地方按 `Ctrl+Alt+R` 说一句，本地转成文字存进「灵感」面板，一条灵感可以转成任务
- **发现**：每天 09:00 自动从 GitHub、Hacker News、Product Hunt、阮一峰周刊、少数派、InfoQ、爱范儿、量子位、掘金等十几个地方挑几个新颖、有意思、能上手的项目和文章，附一句中文推荐语推到「发现」面板
- **读写文件**：列目录、读、新建、局部修改、建目录。改之前先读，改完说清改了哪个文件
- **定时简报**：每天 08:00 / 22:00 自动生成早间简报、晚间收尾，不用开着网页
- **技能**：7 个专业作业指南，用到时自己取（见下）
- **MCP**：外接 3 个官方 MCP 服务器，工具直接进它的工具箱

左侧那个 ✦ 图标是「能力」面板，能看到技能列表和每个 MCP 服务器的连接状态。

---

## 灵感采集（Ctrl+Alt+R）

想到什么、来不及打字的时候用：

1.知更开着的情况下，**任何程序里**按 `Ctrl+Alt+R` → 弹出「🎙 正在录音」气泡
2. 对着麦克风说一句，说完**再按一次** `Ctrl+Alt+R` 结束
3. 几秒内本地转成文字（离线，不联网、不上传），弹「✅ 灵感已记下」
4. 左侧 💡 图标打开「灵感」面板就能看到；可点「转任务」变成待办，或「删除」

细节：

- 转文字用本机 `faster-whisper` 模型，默认在 `models/` 或同级项目的 `models/` 下找 small（约 464 MB），不重新下载；也可以设 `TODAY_WHISPER_MODEL` 直接指路
- 录太短（< 0.6 秒）或没录到声音会自动丢掉，不会存一堆误触
- 面板底部也有「🎙 说一句」按钮（热键被别的软件占了时用）和「✍ 手写」按钮
- 和知更 聊天时随口说「记一下……」它也会调用工具往灵感里存；灵感内容会进它的提示词，聊到相关事情它自己会想起来
- 热键只在知更 运行时有效；录完立刻关窗口的话，最后一条可能还在转写
- 模型路径想换：设环境变量 `TODAY_WHISPER_MODEL` 指向模型目录

---

## 发现（每天 09:00 推项目）

「灵感」是你自己想出来的，「发现」是从外面捞回来的。

- 每天 09:00 自动跑一轮（Windows 计划任务 `TodayAgent_Inspire`），抓完弹气泡告诉你今天推了哪几个
- 左侧 🧭 图标打开「发现」面板：标题、中文推荐语、来源、热度（star / 分数 / 期数）、日期
- 每条四个按钮：**打开**（默认浏览器）、**收藏**（置顶，不会被轮换掉）、**转任务**、**删除**
- 底部「✦ 换一批」随时再抓一轮，一次二十秒左右
- 在对话里说「找点好玩的」「有什么新项目」也会现抓（工具 `discover_projects`），结果同样进面板

抓什么：

| 来源 | 取什么 | 国内直连 |
|---|---|---|
| GitHub | 搜索近 3 周新项目（★>60）、近 3 个月冒头（★>600）、`llm` / `webgl` / `game` 话题；外加 Trending 日榜 | ✓ |
| Hacker News | 首页热度 ≥60 分的条目（走 Algolia，比 hnrss 稳） | ✓ |
| Product Hunt | 当日新品（官方 Atom） | ✓ |
| 阮一峰周刊 | 官网列表页最新的 6 期（feedburner 被墙，改抓网页） | ✓ |
| 少数派 | 官方 RSS | ✓ |
| InfoQ 中文 | 官方 RSS | ✓ |
| 爱范儿 | 官方 RSS | ✓ |
| 量子位 | 官方 RSS | ✓ |
| 掘金 | 官方 RSS | ✓ |
| V2EX | 热门主题 API | 通常要代理 |
| Reddit | r/SideProject、r/LocalLLaMA、r/webdev、r/InternetIsBeautiful、r/gamedev 日榜 | 要代理 |

各平台并发抓，每个来源限量（GitHub 26 条，其余 6–12 条），再按来源轮流混成一份候选，
交给模型挑 8 个、按你的兴趣（`inspire.py` 里的 `DEFAULT_PROFILE`，可在 `data/config.json` 加 `"interests"` 覆盖）
每个写一句「你能拿它干什么」。要求至少覆盖 3 个不同平台，推过的记进 `state.json` 的 `seen`，不会重复推；
收藏的一直留着。抓不到的来源**直接跳过**（日志里记一行），不影响其它平台。

> GitHub 抓取优先用本机 `gh` 的登录态（`gh auth login` 过就行，5000 次/时），没有就匿名——匿名经常被限流。
> 代理不用另配：程序跟着系统代理走，Clash 开着且设了系统代理，V2EX / Reddit / 打开的 github.com 就都能用。
> 想看看每个平台通不通，跑 `python inspire.py sources`（会逐个打印条数和耗时）。

---

## 7 个技能

技能放在 `skills/<名字>/SKILL.md`，模型先看简介，需要时用 `use_skill` 读全文，
所以不占常驻上下文，也不会「假装读过」。想加技能：新建一个目录 + 一份 `SKILL.md`，重启即可。

| 技能 | 什么时候用 |
|---|---|
| 播报 | 早晚简报 / 收尾的四件套格式，带「不许编造」铁律 |
| 文件整理 | 目录太乱、要归档、批量改名 |
| 单文件网页 | 做网页、小工具、小游戏，单文件离线可开 |
| 学习计划 | 期末复习、课程跟不上、制定每日安排 |
| 写作打磨 | 改简历、申请书、推文、演讲稿 |
| 日记复盘 | 写日记、做复盘，存进 `data/diary/日记/日期.md` |
| 行程安排 | 排时间、看这周怎么安排、找冲突 |

## 3 个 MCP 服务器

来自 GitHub 上星标最高的官方仓库 [`modelcontextprotocol/servers`](https://github.com/modelcontextprotocol/servers)
（9.1 万 ★），用 `node` 起子进程，本程序内置了 MCP 客户端（`mcp_client.py`，纯标准库）。

| 服务器 | 提供什么 | 工具数 |
|---|---|---|
| `filesystem` | 文件系统：读、写、改、移动、搜索、目录树（限定在工作目录内） | 16 |
| `memory` | 知识图谱记忆：实体、关系、观察，落盘 `data/memory.json` | 9 |
| `sequential-thinking` | 分步推理：把复杂问题拆成编号思路一步步推 | 1 |

配置在 `data/mcp.json`，可以自己加服务器、改参数、把某个 `enabled` 改成 `false`。
改完在「能力」面板点「重新连接 MCP」即可，不用重启。

> MCP 服务器依赖 Node.js。仓库不带 `node_modules`，先装一次：
> `npm install --prefix mcp @modelcontextprotocol/server-filesystem @modelcontextprotocol/server-memory @modelcontextprotocol/server-sequential-thinking`
> 装完离线可用；缺依赖时「能力」面板会显示连接失败，不影响其它功能。

---

## 文件工具的边界

- 所有内置文件操作**限制在「工作目录」内**（设置可改，默认是本程序上一层目录）
- `..` 往外跳、指向外面的绝对路径，一律拒绝
- 覆盖已有文件前自动留一份 `xxx.bak`
- MCP 的 filesystem 服务器另有一层白名单，启动时只授权了同一个工作目录

默认工作目录是程序所在的上一层目录，可以在设置里改。

---

## 后台定时简报

设置里勾选「启用后台定时简报」→ 保存，会注册两个 Windows 计划任务：

| 任务名 | 时间 | 内容 |
|---|---|---|
| `TodayAgent_Morning` | 每天 08:00 | 早间简报 |
| `TodayAgent_Evening` | 每天 22:00 | 晚间收尾 |
| `TodayAgent_Inspire` | 每天 09:00 | 今日发现 · 项目推送 |

- 生成的内容直接追加进对话流，带「早间简报 / 晚间收尾」标签
- 同时弹一个 Windows 桌面通知
- 不需要网页开着，`pythonw.exe` 静默运行；关机错过的那次，开机后补跑
- 取消：设置里取消勾选 → 保存

手动试跑：`py brief.py morning` / `py brief.py evening`

> 计划任务读不到窗口里的设置，所以 Key 会存在本机 `data/config.json`（明文）。
> 开机时段网络常常还没起来（以前 08:00 那次就是 `getaddrinfo failed`），失败会自动隔 60 秒重试两次，仍然失败会弹一条「简报没生成」的气泡，原因写在 `data/brief.log`。

---

## 数据在哪

```
zhigeng/
  data/state.json     ← 对话、记忆、任务、灵感、发现、推过的项目 id
  data/state.json.bak ← 上一版状态（每次写盘前自动留，写坏了能捞回来）
  data/config.json    ← 服务商配置 + API Key（明文，只在这台电脑上），填一次就记住
  data/mcp.json       ← MCP 服务器配置
  data/memory.json    ← MCP 记忆服务器（知识图谱）的数据
  data/brief.log      ← 后台简报日志，生成失败看这里
  data/inspire.log    ← 每日「发现」日志，抓取失败看这里
  data/app.log        ← 桌面应用的启动日志（服务起不来看这里）
  data/app-window/    ← 应用窗口的浏览器配置（可删）
```

备份复制整个 `data` 目录；清空在设置里点「清空全部数据」。

这个目录**不在仓库里**（`.gitignore` 排除了 `data/`），换台电脑要从头填 Key、重新积累记忆。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `zhigeng.exe` | 桌面启动器：找 Python → 静默拉起 `app.py` |
| `app.py` | 桌面应用本体：起服务子进程 + 打开原生 WebView2 窗口 |
| `zhigeng.ico` | 应用图标（窗口和 exe 都用它） |
| `launcher.cs` | 上面那个 exe 的源码 |
| `启动.bat` | 老式启动（命令行 + 浏览器） |
| `server.py` | 后端：静态页面 + Agent 循环 + 工具执行 + 存储（`--headless` 供 app.py 调用） |
| `mcp_client.py` | MCP 客户端（标准库实现，stdio JSON-RPC） |
| `ideas.py` | 灵感采集：全局热键 + 麦克风录音 + 本地 faster-whisper 转写 |
| `inspire.py` | 发现：11 个平台并发抓取（GitHub / HN / PH / 阮一峰 / 少数派 / InfoQ / 爱范儿 / 量子位 / V2EX / 掘金 / Reddit）+ 模型策展 |
| `skills/` | 7 个技能，每个一份 `SKILL.md` |
| `mcp/package.json` | 三个 MCP 服务器的依赖声明（`npm install` 后生成 `node_modules/`） |
| `index.html` / `style.css` / `app.js` | 界面 |
| `brief.py` / `toast.ps1` / `安装定时简报.ps1` | 定时简报三件套 |

---

## 已知限制

- 桌面窗口依赖 WebView2 运行时（Win11 和装了 Edge 的机器基本都有；真没有就先 `启动.bat`）
- 只能本机用，手机访问不了（除非把 `127.0.0.1` 改成局域网 IP）
- 没接日历 / 邮箱 / 微信，那些要各自的开放接口和审核
- 后台只有到点的定时简报，不会随时主动找你
- 灵感热键只在知更 运行期间生效；要一直能按，就把知更 常开着（或加进开机启动）
- 「发现」里 GitHub 搜索走官方 API，匿名额度紧张时可能抓不到；用 `gh auth login` 登录一次就稳了
- V2EX / Reddit 国内通常连不上，不开代理就少两个来源；点「打开」跳到 github.com 也要代理
- 面板里的 star 数、推荐语是抓取那一刻的快照
- 记忆是全文塞进提示词的，还没有语义检索
