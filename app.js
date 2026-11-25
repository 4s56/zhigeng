/* ---------------------------------------------------------------
   「今天」前端
   --------------------------------------------------------------- */

const $ = (s) => document.querySelector(s);

const streamEl = $('#stream');
const inputEl = $('#input');
const sendEl = $('#send');
const panelEl = $('#panel');
const panelBody = $('#panelBody');
const panelFoot = $('#panelFoot');
const panelTitle = $('#panelTitle');

let state = { memories: [], tasks: [], ideas: [], discover: [], messages: [], conversations: [], active: '', recorder: {} };
let cfg = loadCfg();
// 服务端 data/config.json 里那份配置（只含 has_key，不含 Key 本身）
let savedCfg = { has_key: false };
let busy = false;
let panelMode = null;

/* ---------------- 配置 ---------------- */

function loadCfg() {
  try {
    return JSON.parse(localStorage.getItem('today.cfg') || '{}');
  } catch (e) {
    return {};
  }
}

function saveCfg() {
  localStorage.setItem('today.cfg', JSON.stringify(cfg));
}

// 有 Key 就能发请求：本地存的，或者服务端已经存过的都算
function keyReady() {
  return !!(cfg.api_key || savedCfg.has_key);
}

const MODEL_PRESETS = {
  anthropic: ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-opus-5'],
  openai: ['deepseek-chat', 'deepseek-reasoner', 'moonshot-v1-8k', 'qwen-plus', 'glm-4-plus', 'gpt-4o', 'gpt-4o-mini'],
};

const BASE_PRESETS = {
  anthropic: '',
  openai: 'https://api.deepseek.com/v1',
};

/* ---------------- 主题 ---------------- */

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem('today.theme', t);
}

applyTheme(localStorage.getItem('today.theme') ||
  (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

$('#themeBtn').onclick = () => {
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
};

/* ---------------- 极简 Markdown ---------------- */

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function inline(s) {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}

function block(txt) {
  const lines = txt.split('\n');
  let html = '';
  let list = null;
  let para = [];

  const flushPara = () => {
    if (para.length) {
      html += '<p>' + para.map(inline).join('<br>') + '</p>';
      para = [];
    }
  };
  const flushList = () => {
    if (list) { html += '</' + list + '>'; list = null; }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) { flushPara(); flushList(); continue; }

    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
      flushPara(); flushList();
      const n = m[1].length;
      html += '<h' + n + '>' + inline(m[2]) + '</h' + n + '>';
      continue;
    }
    if ((m = line.match(/^[-*]\s+(.*)$/))) {
      flushPara();
      if (list !== 'ul') { flushList(); html += '<ul>'; list = 'ul'; }
      html += '<li>' + inline(m[1]) + '</li>';
      continue;
    }
    if ((m = line.match(/^\d+\.\s+(.*)$/))) {
      flushPara();
      if (list !== 'ol') { flushList(); html += '<ol>'; list = 'ol'; }
      html += '<li>' + inline(m[1]) + '</li>';
      continue;
    }
    if ((m = line.match(/^>\s?(.*)$/))) {
      flushPara(); flushList();
      html += '<blockquote>' + inline(m[1]) + '</blockquote>';
      continue;
    }
    flushList();
    para.push(line);
  }
  flushPara(); flushList();
  return html;
}

function md(src) {
  const parts = String(src || '').split('```');
  let out = '';
  parts.forEach((p, i) => {
    if (i % 2 === 1) {
      const lines = p.replace(/^\n/, '').split('\n');
      if (/^[\w+#.-]+$/.test(lines[0] || '') && lines.length > 1) lines.shift();
      out += '<pre><code>' + esc(lines.join('\n')) + '</code></pre>';
    } else {
      out += block(p);
    }
  });
  return out;
}

/* ---------------- 渲染对话 ---------------- */

function msgText(content) {
  if (typeof content === 'string') return content;
  return (content || [])
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('');
}

function renderAll() {
  streamEl.innerHTML = '';
  const list = (state.messages || []).filter((m) => {
    if (m.role === 'user') return msgText(m.content).trim() !== '';
    return true;
  });

  if (!list.length) {
    renderHello();
    return;
  }

  const thread = document.createElement('div');
  thread.className = 'thread';
  streamEl.appendChild(thread);

  for (const m of list) {
    if (m.role === 'user') {
      thread.appendChild(userNode(msgText(m.content)));
    } else {
      thread.appendChild(agentNode(m.content, m.brief));
    }
  }
  scrollBottom(true);
}

function renderHello() {
  const h = new Date().getHours();
  const greet = h < 5 ? '夜深了' : h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
  const hasKey = keyReady();

  const el = document.createElement('div');
  el.className = 'hello';
  el.innerHTML =
    '<h1>' + greet + '，我是知更。</h1>' +
    '<p>' + (hasKey
      ? '我不只是回答问题。你说过的事我会记住，你提到要做的事我会替你记在清单上。'
      : '先填一个 API Key，我就能开始工作了。点左下角的齿轮。') + '</p>' +
    '<div class="chips">' +
      '<button class="chip">帮我安排今天</button>' +
      '<button class="chip">我今天要做什么？</button>' +
      '<button class="chip">记住：我在做一个 3D 校园地图</button>' +
    '</div>';

  el.querySelectorAll('.chip').forEach((b) => {
    b.onclick = () => {
      if (!keyReady()) return openSettings();
      inputEl.value = b.textContent;
      autosize();
      send();
    };
  });

  streamEl.appendChild(el);
}

function userNode(text) {
  const d = document.createElement('div');
  d.className = 'msg user';
  const b = document.createElement('div');
  b.className = 'bubble-user';
  b.textContent = text;
  d.appendChild(b);
  return d;
}

const TOOL_LABEL = {
  remember: '记住了',
  forget: '忘掉',
  add_task: '加了任务',
  complete_task: '划掉任务',
  list_tasks: '看了看任务',
  list_files: '看了看目录',
  read_file: '读了文件',
  write_file: '写了文件',
  edit_file: '改了文件',
  make_dir: '建了目录',
};

function toolLabel(name, input) {
  const head = TOOL_LABEL[name] || name;
  if (!input) return head;
  if (name === 'remember' && input.content) return head + ' · ' + input.content;
  if (name === 'add_task' && input.title) return head + ' · ' + input.title;
  if (input.path) return head + ' · ' + input.path;
  return head;
}

const TOOL_ICON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7"/></svg>';

function agentNode(content, brief) {
  const d = document.createElement('div');
  d.className = 'msg agent';
  d.innerHTML = '<div class="avatar">今</div><div class="agent-body"></div>';
  const body = d.querySelector('.agent-body');

  if (brief) {
    const tag = document.createElement('div');
    tag.className = 'brief-tag';
    tag.textContent = brief === 'evening' ? '晚间收尾' : '早间简报';
    body.appendChild(tag);
  }

  const blocks = typeof content === 'string'
    ? [{ type: 'text', text: content }]
    : (content || []);

  let pendingTools = null;
  for (const b of blocks) {
    if (b.type === 'tool_use') {
      if (!pendingTools) {
        pendingTools = document.createElement('div');
        pendingTools.className = 'tools';
        body.appendChild(pendingTools);
      }
      const chip = document.createElement('div');
      chip.className = 'tool done';
      chip.innerHTML = TOOL_ICON + '<span>' + esc(toolLabel(b.name, b.input)) + '</span>';
      pendingTools.appendChild(chip);
    } else if (b.type === 'text' && b.text) {
      const t = document.createElement('div');
      t.className = 'agent-text';
      t.innerHTML = md(b.text);
      body.appendChild(t);
    }
  }

  if (!body.children.length) {
    const t = document.createElement('div');
    t.className = 'agent-text';
    t.innerHTML = md('…');
    body.appendChild(t);
  }
  return d;
}

function scrollBottom(force) {
  const near = streamEl.scrollHeight - streamEl.scrollTop - streamEl.clientHeight < 220;
  if (force || near) streamEl.scrollTop = streamEl.scrollHeight;
}

/* ---------------- 流式对话 ---------------- */

let live = null; // { body, curText, curBuf, toolsEl, toolMap }

function beginAssistant() {
  const d = document.createElement('div');
  d.className = 'msg agent';
  d.innerHTML = '<div class="avatar">今</div><div class="agent-body"></div>';
  const body = d.querySelector('.agent-body');
  const typing = document.createElement('div');
  typing.className = 'typing';
  typing.innerHTML = '<i></i><i></i><i></i>';
  body.appendChild(typing);

  newThread().appendChild(d);
  live = { body, typing, curText: null, curBuf: '', toolMap: {} };
  scrollBottom(true);
}

function newThread() {
  let t = streamEl.querySelector('.thread');
  if (!t) {
    streamEl.innerHTML = '';
    t = document.createElement('div');
    t.className = 'thread';
    streamEl.appendChild(t);
  }
  return t;
}

function dropTyping() {
  if (live && live.typing) {
    live.typing.remove();
    live.typing = null;
  }
}

function appendDelta(text) {
  if (!live) return;
  dropTyping();
  if (!live.curText || live.curText.nextSibling) {
    live.curText = document.createElement('div');
    live.curText.className = 'agent-text';
    live.curBuf = '';
    live.body.appendChild(live.curText);
  }
  live.curBuf += text;
  live.curText.innerHTML = md(live.curBuf);
  scrollBottom();
}

function appendTool(id, name, input) {
  if (!live) return;
  dropTyping();
  const tools = document.createElement('div');
  tools.className = 'tools';
  const chip = document.createElement('div');
  chip.className = 'tool running';
  chip.innerHTML = TOOL_ICON + '<span>' + esc(toolLabel(name, input)) + '</span>';
  tools.appendChild(chip);
  live.body.appendChild(tools);
  live.curText = null;
  if (id) live.toolMap[id] = chip;
  scrollBottom();
}

function finishTool(id) {
  const chip = live && live.toolMap[id];
  if (chip) {
    chip.classList.remove('running');
    chip.classList.add('done');
  }
}

async function send() {
  const text = inputEl.value.trim();
  if (!text || busy) return;
  if (!keyReady()) return openSettings();

  busy = true;
  sendEl.disabled = true;
  inputEl.value = '';
  autosize();

  state.messages.push({ role: 'user', content: [{ type: 'text', text }] });

  const thread = newThread();
  thread.appendChild(userNode(text));
  beginAssistant();
  scrollBottom(true);

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, config: cfg, conv: state.active }),
    });

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const payload = line.slice(5).trim();
          if (!payload || payload === '[DONE]') continue;
          let ev;
          try { ev = JSON.parse(payload); } catch (e) { continue; }
          handleEvent(ev);
        }
      }
    }
  } catch (e) {
    showError('连接中断：' + e.message);
  } finally {
    busy = false;
    sendEl.disabled = false;
    dropTyping();
    scrollBottom();
    refreshState();
  }
}

function handleEvent(ev) {
  if (ev.t === 'delta') {
    appendDelta(ev.text);
  } else if (ev.t === 'tool') {
    appendTool(ev.id, ev.name, ev.input);
  } else if (ev.t === 'tool_result') {
    finishTool(ev.id);
  } else if (ev.t === 'state') {
    state.memories = ev.memories || [];
    state.tasks = ev.tasks || [];
    if (panelMode) renderPanel();
    updateBadges();
  } else if (ev.t === 'error') {
    showError(ev.message || '出错了');
  }
}

function showError(msg) {
  dropTyping();
  const box = document.createElement('div');
  box.className = 'err';
  box.textContent = msg;
  (live ? live.body : newThread()).appendChild(box);
  scrollBottom();
}

async function refreshState() {
  try {
    const r = await fetch('/api/state');
    const s = await r.json();
    state.memories = s.memories || [];
    state.tasks = s.tasks || [];
    state.messages = s.messages || [];
    state.conversations = s.conversations || [];
    state.ideas = s.ideas || [];
    state.discover = s.discover || [];
    state.recorder = s.recorder || {};
    state.active = s.active || '';
    if (s.config) savedCfg = s.config;
    if (panelMode) renderPanel();
    updateBadges();
  } catch (e) { /* ignore */ }
}

/* ---------------- 输入框 ---------------- */

function autosize() {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + 'px';
}

inputEl.addEventListener('input', autosize);
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
sendEl.addEventListener('click', send);

/* ---------------- 右侧面板 ---------------- */

function openPanel(mode) {
  if (panelMode === mode) return closePanel();
  panelMode = mode;
  panelTitle.textContent =
    mode === 'memory' ? '记忆' : mode === 'caps' ? '能力'
    : mode === 'chats' ? '对话' : mode === 'ideas' ? '灵感'
    : mode === 'discover' ? '发现' : '任务';
  panelEl.classList.add('open');
  document.querySelectorAll('.rail-btn[data-nav]').forEach((b) => {
    b.classList.toggle('active', b.dataset.nav === mode || (mode === null && b.dataset.nav === 'today'));
  });
  renderPanel();
  renderPanelFoot();
  startIdeasPolling();
}

function closePanel() {
  panelMode = null;
  panelEl.classList.remove('open');
  document.querySelectorAll('.rail-btn[data-nav]').forEach((b) => {
    b.classList.toggle('active', b.dataset.nav === 'today');
  });
  stopIdeasPolling();
}

document.querySelectorAll('.rail-btn[data-nav]').forEach((b) => {
  if (b.dataset.nav === 'today') {
    b.onclick = () => { closePanel(); renderAll(); };
  } else {
    b.onclick = () => openPanel(b.dataset.nav);
  }
});

$('#panelClose').onclick = closePanel;

function renderPanel() {
  if (!panelMode) return;
  panelBody.innerHTML = '';

  if (panelMode === 'caps') {
    renderCaps();
    return;
  }

  if (panelMode === 'chats') {
    renderChats();
    return;
  }

  if (panelMode === 'ideas') {
    renderIdeas();
    return;
  }

  if (panelMode === 'discover') {
    renderDiscover();
    return;
  }

  if (panelMode === 'memory') {
    if (!state.memories.length) {
      panelBody.innerHTML = '<div class="empty">还没有记忆。<br>和我聊聊，我会自己记下来。</div>';
      return;
    }    for (const m of state.memories.slice().reverse()) {
      const c = document.createElement('div');
      c.className = 'card';
      c.innerHTML =
        '<div class="tag">' + esc(m.category || '其他') + '</div>' +
        '<div class="body">' + esc(m.content) + '</div>' +
        '<div class="meta">' + esc(m.created || '') + '</div>' +
        '<button class="del" title="删除">×</button>';
      c.querySelector('.del').onclick = async () => {
        await fetch('/api/memory/' + m.id, { method: 'DELETE' });
        state.memories = state.memories.filter((x) => x.id !== m.id);
        renderPanel();
        updateBadges();
      };
      panelBody.appendChild(c);
    }
  } else {
    const list = state.tasks.slice().reverse();
    if (!list.length) {
      panelBody.innerHTML = '<div class="empty">今天还没有任务。<br>跟我说话，我会把要做的事记下来。</div>';
      return;
    }
    for (const t of list) {
      const d = document.createElement('div');
      d.className = 'task' + (t.done ? ' done' : '');
      d.innerHTML =
        '<div class="box">' + TOOL_ICON + '</div>' +
        '<div class="t">' +
          '<div class="title">' + esc(t.title) + '</div>' +
          (t.due || t.priority !== '中'
            ? '<div class="sub">' +
                (t.due ? esc(t.due) : '') +
                (t.priority !== '中' ? ' <span class="pri">· ' + esc(t.priority) + '优先</span>' : '') +
              '</div>'
            : '') +
        '</div>';
      d.querySelector('.box').onclick = async () => {
        t.done = !t.done;
        await fetch('/api/task', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: t.id, done: t.done }),
        });
        renderPanel();
        updateBadges();
      };
      panelBody.appendChild(d);
    }
  }
}

/* ---------------- 灵感 ---------------- */

let ideasTimer = null;

function startIdeasPolling() {
  stopIdeasPolling();
  if (panelMode !== 'ideas') return;
  // 热键录音在服务端完成，面板靠轮询把新灵感捞回来
  ideasTimer = setInterval(async () => {
    if (panelMode !== 'ideas') return stopIdeasPolling();
    try {
      const r = await fetch('/api/state');
      const s = await r.json();
      const before = (state.ideas || []).length;
      state.ideas = s.ideas || [];
      state.recorder = s.recorder || {};
      if (state.recorder.recording !== (state.recorderShown || false)) {
        renderPanel();
        renderPanelFoot();
      } else if (state.ideas.length !== before) {
        renderPanel();
        renderPanelFoot();
        updateBadges();
      } else if (state.recorder.recording) {
        renderPanelFoot();   // 让秒数往上走
      }
      state.recorderShown = !!state.recorder.recording;
    } catch (e) { /* 服务没起就算了 */ }
  }, 1500);
}

function stopIdeasPolling() {
  if (ideasTimer) clearInterval(ideasTimer);
  ideasTimer = null;
}

function renderIdeas() {
  const list = state.ideas || [];
  const rec = state.recorder || {};
  const head = document.createElement('div');
  head.className = 'idea-tip';
  head.innerHTML =
    (rec.error ? '⚠ ' + esc(rec.error) + '<br>' : '') +
    '按 <b>Ctrl+Alt+R</b> 说一句，再按一次结束，自动转成文字存下来。' +
    (rec.hotkey_on ? '' : '<br>⚠ 热键没注册上，用下面的按钮录音。');
  panelBody.appendChild(head);

  if (!list.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.innerHTML = '还没有灵感。<br>按快捷键说一句，或者下面手写一条。';
    panelBody.appendChild(e);
    return;
  }

  for (const it of list) {
    const d = document.createElement('div');
    d.className = 'idea' + (it.done ? ' done' : '');
    d.innerHTML =
      '<div class="t">' + esc(it.text) + '</div>' +
      '<div class="m">' +
        esc((it.created || '').replace(/^\d+年\d+月\d+日\s*/, '')) +
        (it.source === 'voice' ? ' · 🎙 口述' : it.source === 'chat' ? ' · 对话' : ' · 手写') +
        (it.done ? ' · 已转任务' : '') +
      '</div>' +
      '<div class="acts">' +
        (it.done ? '' : '<button class="mini-btn" data-a="task">转任务</button>') +
        '<button class="mini-btn danger" data-a="del">删除</button>' +
      '</div>';

    const t = d.querySelector('[data-a="task"]');
    if (t) t.onclick = async () => {
      const r = await fetch('/api/idea', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'task', id: it.id }),
      });
      const j = await r.json();
      if (j.ideas) state.ideas = j.ideas;
      if (j.tasks) state.tasks = j.tasks;
      renderPanel();
      renderPanelFoot();
      updateBadges();
      notify('已转成任务');
    };

    d.querySelector('[data-a="del"]').onclick = async () => {
      const r = await fetch('/api/idea', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', id: it.id }),
      });
      const j = await r.json();
      if (j.ideas) state.ideas = j.ideas;
      renderPanel();
      renderPanelFoot();
      updateBadges();
    };

    panelBody.appendChild(d);
  }
}

/* ---------------- 发现 ---------------- */

function fmtStars(n) {
  n = Number(n) || 0;
  return n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(n);
}

function renderDiscover() {
  const list = (state.discover || []).slice().reverse();   // 新的在前

  const head = document.createElement('div');
  head.className = 'idea-tip';
  head.innerHTML =
    '每天 09:00 从 GitHub、Hacker News、Product Hunt、阮一峰周刊、少数派、InfoQ、' +
    '爱范儿、量子位、掘金里挑几个有意思的推给你。收藏的会一直留在最上面。';
  panelBody.appendChild(head);

  if (!list.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.innerHTML = '还没有推荐。<br>点下面的「换一批」抓一轮。';
    panelBody.appendChild(e);
    return;
  }

  const saved = list.filter((x) => x.saved);
  const fresh = list.filter((x) => !x.saved);

  if (saved.length) {
    const h = document.createElement('div');
    h.className = 'disc-sec';
    h.textContent = '收藏 · ' + saved.length;
    panelBody.appendChild(h);
    saved.forEach((it) => panelBody.appendChild(discoverCard(it)));
    if (fresh.length) {
      const h2 = document.createElement('div');
      h2.className = 'disc-sec';
      h2.textContent = '最近推荐';
      panelBody.appendChild(h2);
    }
  }
  fresh.forEach((it) => panelBody.appendChild(discoverCard(it)));
}

function discoverCard(it) {
  const d = document.createElement('div');
  d.className = 'disc' + (it.done ? ' done' : '');

  const meta = [];
  if (it.src_name) meta.push(esc(it.src_name));
  else if (it.source === 'github') meta.push('GitHub');
  if (it.hot_label) meta.push(esc(it.hot_label));
  else if (it.source === 'github' && it.stars) meta.push('★ ' + fmtStars(it.stars));
  if (it.lang) meta.push(esc(it.lang));
  if (it.date) meta.push(esc(it.date));

  d.innerHTML =
    '<div class="dt">' + esc(it.title) + '</div>' +
    '<div class="dw">' + esc(it.why || it.desc || '') + '</div>' +
    '<div class="dm">' +
      (it.tag ? '<span class="dchip">' + esc(it.tag) + '</span>' : '') +
      '<span>' + meta.join(' · ') + '</span>' +
    '</div>' +
    '<div class="acts">' +
      '<button class="mini-btn" data-a="open">打开</button>' +
      '<button class="mini-btn" data-a="save">' + (it.saved ? '取消收藏' : '收藏') + '</button>' +
      (it.done ? '<span class="dchip">已转任务</span>'
               : '<button class="mini-btn" data-a="task">转任务</button>') +
      '<button class="mini-btn danger" data-a="del">删除</button>' +
    '</div>';

  d.querySelector('[data-a="open"]').onclick = () => {
    fetch('/api/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: it.url }),
    });
  };

  const post = async (action) => {
    const r = await fetch('/api/discover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, id: it.id }),
    });
    const j = await r.json();
    if (j.discover) state.discover = j.discover;
    if (j.tasks) state.tasks = j.tasks;
    renderPanel();
    renderPanelFoot();
    updateBadges();
  };

  d.querySelector('[data-a="save"]').onclick = () => post('save');
  const t = d.querySelector('[data-a="task"]');
  if (t) t.onclick = () => post('task').then(() => notify('已转成任务'));
  d.querySelector('[data-a="del"]').onclick = () => post('delete');

  return d;
}

async function toggleRecord(btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'toggle' }),
    });
    const j = await r.json();
    if (j.recorder) state.recorder = j.recorder;
  } catch (e) {}
  if (btn) btn.disabled = false;
  renderPanel();
  renderPanelFoot();
}

/* ---------------- 对话列表 ---------------- */

function renderChats() {
  const list = state.conversations || [];
  if (!list.length) {
    panelBody.innerHTML = '<div class="empty">还没有对话。点下面的按钮开一个。</div>';
    return;
  }
  for (const c of list) {
    const d = document.createElement('div');
    d.className = 'conv' + (c.id === state.active ? ' on' : '');
    d.innerHTML =
      '<div class="t">' +
        (c.kind === 'brief' ? '<span class="mini">简报</span>' : '') +
        esc(c.title || '新对话') +
      '</div>' +
      '<div class="m">' + esc(c.updated || '') + (c.count ? ' · ' + c.count + ' 条' : '') + '</div>' +
      '<button class="del" title="删除">×</button>';
    d.onclick = () => switchConv(c.id);
    d.querySelector('.del').onclick = (e) => {
      e.stopPropagation();
      delConv(c.id);
    };
    panelBody.appendChild(d);
  }
}

async function convPost(payload) {
  await fetch('/api/conv', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  await refreshState();
  renderAll();
  if (panelMode === 'chats') renderPanel();
}

async function switchConv(id) {
  if (id === state.active) return;
  if (busy) return notify('等这条回答说完再切');
  await convPost({ action: 'switch', id });
}

async function newConv() {
  if (busy) return notify('等这条回答说完再开新对话');
  await convPost({ action: 'new' });
  inputEl.focus();
}

async function delConv(id) {
  if (!confirm('删除这个对话？')) return;
  await fetch('/api/conv/' + id, { method: 'DELETE' });
  await refreshState();
  renderAll();
  if (panelMode === 'chats') renderPanel();
}

async function renderCaps() {
  panelBody.innerHTML = '<div class="empty">正在检查能力…</div>';
  let data;
  try {
    data = await (await fetch('/api/capabilities')).json();
  } catch (e) {
    panelBody.innerHTML = '<div class="empty">读不到能力列表：' + esc(e.message) + '</div>';
    return;
  }
  if (panelMode !== 'caps') return;
  panelBody.innerHTML = '';

  const sk = document.createElement('div');
  sk.className = 'cap-sec';
  sk.innerHTML = '<div class="cap-head">技能 · ' + (data.skills || []).length + ' 个</div>';
  for (const s of data.skills || []) {
    const d = document.createElement('div');
    d.className = 'cap-item';
    d.innerHTML =
      '<div class="n">' + esc(s.name) + '</div>' +
      '<div class="m">' + esc(s.description) + '</div>';
    sk.appendChild(d);
  }
  panelBody.appendChild(sk);

  const mc = document.createElement('div');
  mc.className = 'cap-sec';
  mc.innerHTML = '<div class="cap-head">MCP 服务器 · ' + (data.mcp || []).length + ' 个</div>';
  for (const m of data.mcp || []) {
    const names = (m.tools || []).slice(0, 6).join('、');
    const more = (m.tools || []).length > 6 ? ' …' : '';
    const d = document.createElement('div');
    d.className = 'cap-item';
    d.innerHTML =
      '<div class="n"><span class="dot ' + (m.ready ? 'ok' : 'bad') + '"></span>' + esc(m.name) + '</div>' +
      '<div class="m">' +
        (m.ready
          ? esc((m.tools || []).length + ' 个工具：' + names + more)
          : esc(m.error || '未连接')) +
      '</div>';
    mc.appendChild(d);
  }
  panelBody.appendChild(mc);
}

function renderPanelFoot() {
  panelFoot.innerHTML = '';

  if (panelMode === 'chats') {
    const btn = document.createElement('button');
    btn.className = 'primary';
    btn.textContent = '＋ 新对话';
    btn.style.width = '100%';
    btn.onclick = newConv;
    panelFoot.appendChild(btn);
    return;
  }

  if (panelMode === 'caps') {
    const btn = document.createElement('button');
    btn.className = 'ghost';
    btn.textContent = '重新连接 MCP';
    btn.style.width = '100%';
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = '连接中…';
      try {
        await fetch('/api/mcp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'reload' }),
        });
      } catch (e) {}
      renderCaps();
      btn.disabled = false;
      btn.textContent = '重新连接 MCP';
    };
    panelFoot.appendChild(btn);
    return;
  }

  if (panelMode === 'discover') {
    const btn = document.createElement('button');
    btn.className = 'primary';
    btn.textContent = '✦ 换一批';
    btn.style.width = '100%';
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = '正在找…（二十秒左右）';
      try {
        const r = await fetch('/api/discover', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'refresh', config: cfg }),
        });
        const j = await r.json();
        if (j.discover) state.discover = j.discover;
        if (!j.ok) notify((j.errors || []).join('；').slice(0, 90) || '这轮没找到新项目');
      } catch (e) {
        notify('服务没响应，稍后再试');
      }
      renderPanel();
      renderPanelFoot();
    };
    panelFoot.appendChild(btn);
    return;
  }

  if (panelMode === 'ideas') {
    const rec = state.recorder || {};
    const row = document.createElement('div');
    row.className = 'idea-foot';

    const recBtn = document.createElement('button');
    recBtn.className = rec.recording ? 'primary rec-on' : 'primary';
    recBtn.textContent = rec.recording
      ? ('■ 停止录音 ' + (rec.seconds || 0) + 's')
      : (rec.transcribing ? '转写中…' : '🎙 说一句');
    if (rec.error) recBtn.disabled = true;
    recBtn.onclick = () => toggleRecord(recBtn);
    row.appendChild(recBtn);

    const addBtn = document.createElement('button');
    addBtn.className = 'ghost';
    addBtn.textContent = '✍ 手写';
    addBtn.onclick = () => {
      const v = prompt('记一条灵感：');
      if (!v || !v.trim()) return;
      fetch('/api/idea', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'add', text: v.trim(), source: 'typed' }),
      }).then((r) => r.json()).then((j) => {
        if (j.ideas) state.ideas = j.ideas;
        renderPanel();
        renderPanelFoot();
        updateBadges();
      });
    };
    row.appendChild(addBtn);

    panelFoot.appendChild(row);
    return;
  }

  const inp = document.createElement('input');
  inp.placeholder = panelMode === 'memory' ? '添加一条记忆，回车保存' : '添加一个任务，回车保存';
  inp.onkeydown = async (e) => {
    if (e.key !== 'Enter') return;
    const v = inp.value.trim();
    if (!v) return;
    if (panelMode === 'memory') {
      const r = await fetch('/api/memory', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: v }),
      });
      const j = await r.json();
      if (j.memories) state.memories = j.memories;
    } else {
      const r = await fetch('/api/task/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: v }),
      });
      const j = await r.json();
      if (j.tasks) state.tasks = j.tasks;
    }
    inp.value = '';
    renderPanel();
    updateBadges();
  };
  panelFoot.appendChild(inp);
}

function updateBadges() {
  const mem = document.querySelector('[data-nav="memory"]');
  const tsk = document.querySelector('[data-nav="tasks"]');
  const open = state.tasks.filter((t) => !t.done).length;
  mem.style.opacity = state.memories.length ? '1' : '.55';
  tsk.style.opacity = open ? '1' : '.55';
}

/* ---------------- 设置 ---------------- */

function openSettings() {
  $('#fProvider').value = cfg.provider || 'anthropic';
  $('#fKey').value = cfg.api_key || '';
  $('#fKey').placeholder = (!cfg.api_key && savedCfg.has_key)
    ? '已保存过，留空就用原来那个'
    : 'sk-…';
  $('#fBase').value = cfg.base_url || BASE_PRESETS[cfg.provider || 'anthropic'] || '';
  $('#fModel').value = cfg.model || '';
  $('#fWorkspace').value = cfg.workspace || '';
  $('#fBg').checked = localStorage.getItem('today.bg') === '1';
  $('#schedTip').textContent = '';
  syncProviderFields();
  $('#modal').classList.add('show');
}

function syncProviderFields() {
  const p = $('#fProvider').value;
  $('#baseField').style.display = 'block';
  $('#fBase').placeholder = p === 'anthropic'
    ? '留空即用官方 https://api.anthropic.com'
    : 'https://api.deepseek.com/v1';
  const dl = $('#modelList');
  dl.innerHTML = MODEL_PRESETS[p].map((m) => '<option value="' + m + '">').join('');
  $('#fModel').placeholder = p === 'anthropic' ? 'claude-opus-5-5' : 'deepseek-chat';
  if (!$('#fBase').value) $('#fBase').value = BASE_PRESETS[p] || '';
}

$('#fProvider').addEventListener('change', () => {
  const others = Object.values(BASE_PRESETS).filter(Boolean);
  if (others.indexOf($('#fBase').value.trim()) >= 0) $('#fBase').value = '';
  syncProviderFields();
});

$('#eyeBtn').onclick = () => {
  const k = $('#fKey');
  k.type = k.type === 'password' ? 'text' : 'password';
};

$('#setBtn').onclick = openSettings;
$('#modalClose').onclick = () => $('#modal').classList.remove('show');
$('#modal').addEventListener('click', (e) => {
  if (e.target.id === 'modal') $('#modal').classList.remove('show');
});

$('#saveBtn').onclick = async () => {
  cfg = {
    provider: $('#fProvider').value,
    api_key: $('#fKey').value.trim(),
    base_url: $('#fBase').value.trim(),
    model: $('#fModel').value.trim(),
    workspace: $('#fWorkspace').value.trim(),
  };
  saveCfg();

  const wantBg = $('#fBg').checked;
  localStorage.setItem('today.bg', wantBg ? '1' : '0');
  const tip = $('#schedTip');
  tip.textContent = '';
  const btn = $('#saveBtn');
  btn.disabled = true;

  try {
    // 1. 配置落到 data/config.json —— 以后打开就不用再填 Key 了
    const cr = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    const cj = await cr.json().catch(() => ({}));
    if (cj.config) savedCfg = cj.config;

    // 2. 后台简报开关只管计划任务，跟 Key 存不存着没关系
    if (wantBg) {
      tip.textContent = '正在注册计划任务…';
      const r = await fetch('/api/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'install' }),
      });
      const j = await r.json();
      if (j.ok) {
        $('#modal').classList.remove('show');
        notify('后台定时任务已启用：08:00 / 22:00 简报，09:00 推送新项目');
      } else {
        tip.textContent = '启用失败：' + (j.output || '未知原因');
      }
    } else {
      await fetch('/api/schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'remove' }),
      });
      $('#modal').classList.remove('show');
    }
  } catch (e) {
    tip.textContent = '操作失败：' + e.message;
  } finally {
    btn.disabled = false;
  }

  if (!state.messages.length) renderAll();
};

function notify(text) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

$('#resetBtn').onclick = async () => {
  if (!confirm('清空所有对话、记忆和任务？这个操作不可撤销。')) return;
  await fetch('/api/reset', { method: 'POST' });
  state = { memories: [], tasks: [], messages: [], conversations: [], active: '' };
  panelMode = null;
  closePanel();
  renderAll();
  updateBadges();
};

$('#newChat').onclick = newConv;

/* ---------------- 启动 ---------------- */

function setDate() {
  const n = new Date();
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][n.getDay()];
  $('#dateMain').textContent = (n.getMonth() + 1) + '月' + n.getDate() + '日';
  $('#dateSub').textContent = wd;
}

(async function init() {
  setDate();
  try {
    const r = await fetch('/api/state');
    const s = await r.json();
    state.memories = s.memories || [];
    state.tasks = s.tasks || [];
    state.messages = s.messages || [];
    state.conversations = s.conversations || [];
    state.ideas = s.ideas || [];
    state.discover = s.discover || [];
    state.recorder = s.recorder || {};
    state.active = s.active || '';
    savedCfg = s.config || { has_key: false };
    // 服务器上存过配置，浏览器这边空着就补上（Key 本身不回传，留空即用服务器那份）
    if (savedCfg.has_key && !cfg.api_key) {
      cfg = {
        provider: cfg.provider || savedCfg.provider || 'anthropic',
        api_key: '',
        base_url: cfg.base_url || savedCfg.base_url || '',
        model: cfg.model || savedCfg.model || '',
        workspace: cfg.workspace || savedCfg.workspace || '',
      };
      saveCfg();
    }
  } catch (e) { /* 服务未启动 */ }

  renderAll();
  updateBadges();
  autosize();

  // 方便直接打开某个面板：#ideas / #discover / #chats / #memory / #tasks / #caps
  const h = (location.hash || '').replace('#', '');
  if (['ideas', 'discover', 'chats', 'memory', 'tasks', 'caps'].indexOf(h) >= 0) openPanel(h);

  if (!keyReady()) setTimeout(openSettings, 420);
})();
