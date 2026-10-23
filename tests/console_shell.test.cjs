'use strict';

/*
 * tests/console_shell.test.cjs
 * P2 前端公共壳层 Node 自动化单元测试
 * 使用 Node built-in node:test, node:assert/strict, node:vm
 * 配合轻量薄 DOM 模拟真实浏览器环境，装载真实 core.js 与 shell.js
 *
 * 覆盖：
 * 1. 顶部五指标共用与未知态展示（pending 计数未知显示—非伪零、今日通关/出发 0分母/未知无伪进度、比例上限100%）
 * 2. 生成于 HH:MM · 页面不会自动刷新
 * 3. 四个 Tab 切换（pending/dispatch/runs/roles）及未注册模块显示“页面模块尚未合入”
 * 4. 挂载 mount 与销毁 dispose：切页时不重复事件、正确执行销毁回调
 * 5. selection 内存跨 Tab 保留、重开重置，且包含 environment 与 roleDefinitions
 * 6. 安全 DOM 创建 ui.el：拒绝 HTML 字符串、拒绝 on* 属性、拒绝非白名单属性、拒绝 javascript: URL
 * 7. 头像 ui.avatar(roleId)：6 个固定角色渲染 SVG、未知角色渲染中性占位
 * 8. 导出框 ui.openExport(lines)：空指令提示、只读 textarea 永久可手动复制
 * 9. 剪贴板复制功能：Clipboard 成功、拒绝（catch/reject）、API 不存在（undefined）各分支降级与提示
 * 10. 页面跳转 ui.navigate(pageId, itemId)：仅允许合法页面与真实关联，非法跳转返回 false
 * 11. AssistantConsole 对象保持冻结，无额外扩展或篡改
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL, URL } = require('node:url');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..');
const CORE_PATH = path.join(ROOT, 'tools', 'console_assets', 'core.js');
const SHELL_PATH = path.join(ROOT, 'tools', 'console_assets', 'shell.js');
const AVATARS_PATH = path.join(ROOT, 'tools', 'console_assets', 'avatars.json');

const CORE_SOURCE = fs.readFileSync(CORE_PATH, 'utf8');
const SHELL_SOURCE = fs.readFileSync(SHELL_PATH, 'utf8');
const AVATARS_DATA = JSON.parse(fs.readFileSync(AVATARS_PATH, 'utf8'));

// ---------------------------------------------------------------- 薄 DOM 辅助实现
class MockNode {
  constructor(nodeName) {
    this.nodeName = String(nodeName).toUpperCase();
    this.attributes = Object.create(null);
    this.childNodes = [];
    this.parentNode = null;
    this.classList = new Set();
    this.classList.add = (c) => Set.prototype.add.call(this.classList, c);
    this.classList.remove = (c) => Set.prototype.delete.call(this.classList, c);
    this.classList.contains = (c) => Set.prototype.has.call(this.classList, c);
    this.style = {};
    this.listeners = Object.create(null);
    this._textContent = '';
    this.value = '';
    this.selected = false;
  }

  get textContent() {
    if (this.childNodes.length > 0) {
      return this.childNodes.map(n => (typeof n.textContent === 'string' ? n.textContent : '')).join('');
    }
    return this._textContent;
  }

  set textContent(val) {
    this.childNodes = [];
    this._textContent = String(val);
  }

  setAttribute(name, val) {
    this.attributes[name] = String(val);
    if (name === 'class') {
      this.classList.clear();
      String(val).split(/\s+/).filter(Boolean).forEach(c => this.classList.add(c));
    }
  }

  getAttribute(name) {
    if (name === 'class') {
      return Array.from(this.classList).join(' ');
    }
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name);
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  removeChild(child) {
    const idx = this.childNodes.indexOf(child);
    if (idx !== -1) {
      this.childNodes.splice(idx, 1);
      child.parentNode = null;
    }
    return child;
  }

  addEventListener(type, handler) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(handler);
  }

  dispatchEvent(event) {
    const handlers = this.listeners[event.type] || [];
    event.target = this;
    for (const h of handlers) {
      h(event);
    }
  }

  focus() { this.focused = true; }
  select() { this.selectedText = true; }
  scrollIntoView() { this.scrolled = true; }

  closest(selector) {
    let cur = this;
    while (cur) {
      if (cur.matches && cur.matches(selector)) return cur;
      cur = cur.parentNode;
    }
    return null;
  }

  contains(other) {
    let cur = other;
    while (cur) {
      if (cur === this) return true;
      cur = cur.parentNode;
    }
    return false;
  }

  matches(selector) {
    // 处理多段组合选择器，例如：.stat[data-metric="pending"]
    if (selector.includes('[')) {
      const match = selector.match(/^([a-zA-Z0-9_.-]*?)\[([a-zA-Z0-9_-]+)(?:="([^"]*)")?\]$/);
      if (match) {
        const prefix = match[1];
        const attr = match[2];
        const val = match[3];
        if (prefix && !this.matches(prefix)) return false;
        if (val !== undefined) return this.getAttribute(attr) === val;
        return this.hasAttribute(attr);
      }
    }
    if (selector.startsWith('.')) {
      return this.classList.contains(selector.slice(1));
    }
    if (selector.startsWith('#')) {
      return this.getAttribute('id') === selector.slice(1);
    }
    return this.nodeName.toLowerCase() === selector.toLowerCase();
  }

  querySelector(selector) {
    const parts = selector.trim().split(/\s+/);
    if (parts.length === 1) {
      const results = this.querySelectorAll(parts[0]);
      return results.length > 0 ? results[0] : null;
    }
    // 层级后代查询
    let currentNodes = [this];
    for (const part of parts) {
      const nextNodes = [];
      for (const node of currentNodes) {
        const found = node.querySelectorAll(part);
        for (const f of found) {
          if (!nextNodes.includes(f)) nextNodes.push(f);
        }
      }
      currentNodes = nextNodes;
    }
    return currentNodes.length > 0 ? currentNodes[0] : null;
  }

  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/);
    if (parts.length > 1) {
      let currentNodes = [this];
      for (const part of parts) {
        const nextNodes = [];
        for (const node of currentNodes) {
          const found = node.querySelectorAll(part);
          for (const f of found) {
            if (!nextNodes.includes(f)) nextNodes.push(f);
          }
        }
        currentNodes = nextNodes;
      }
      return currentNodes;
    }
    const matches = [];
    function walk(node) {
      for (const child of node.childNodes) {
        if (child.matches && child.matches(selector)) {
          matches.push(child);
        }
        walk(child);
      }
    }
    walk(this);
    return matches;
  }
}

class MockDocument extends MockNode {
  constructor() {
    super('#document');
    this.documentElement = new MockNode('html');
    this.appendChild(this.documentElement);
    this.body = new MockNode('body');
    this.documentElement.appendChild(this.body);
    this.readyState = 'complete';
  }

  createElement(tag) {
    return new MockNode(tag);
  }

  getElementById(id) {
    return this.querySelector('#' + id);
  }

  importNode(node, deep) {
    return node;
  }
}

// 基于真实 shell.html 骨架搭建 DOM 树
function setupMockDom(snapshot, avatars = AVATARS_DATA) {
  const doc = new MockDocument();
  const root = doc.createElement('div');
  root.setAttribute('id', 'assistant-console-root');
  doc.body.appendChild(root);

  // HUD
  const hud = doc.createElement('header');
  hud.setAttribute('class', 'hud');
  root.appendChild(hud);

  const hudIn = doc.createElement('div');
  hudIn.setAttribute('class', 'hud-in');
  hud.appendChild(hudIn);

  const hudRow = doc.createElement('div');
  hudRow.setAttribute('class', 'hud-row');
  hudIn.appendChild(hudRow);

  const genSpan = doc.createElement('span');
  genSpan.setAttribute('id', 'console-gen-time');
  genSpan.setAttribute('class', 'gen');
  hudRow.appendChild(genSpan);

  // Stats
  const statsDiv = doc.createElement('div');
  statsDiv.setAttribute('class', 'stats');
  statsDiv.setAttribute('id', 'console-hud-stats');
  hudIn.appendChild(statsDiv);

  function addStat(metric, initialVal = '—', hasBar = false) {
    const st = doc.createElement('div');
    st.setAttribute('class', 'stat');
    st.setAttribute('data-metric', metric);
    const b = doc.createElement('b');
    b.setAttribute('class', 'stat-val');
    b.textContent = initialVal;
    st.appendChild(b);
    const span = doc.createElement('span');
    span.setAttribute('class', 'stat-lbl');
    st.appendChild(span);
    if (hasBar) {
      const bar = doc.createElement('div');
      bar.setAttribute('class', 'bar');
      const i = doc.createElement('i');
      i.style.width = '0%';
      bar.appendChild(i);
      st.appendChild(bar);
    }
    statsDiv.appendChild(st);
  }

  addStat('pending');
  addStat('running');
  addStat('today-progress', '—', true);
  addStat('week-completed');
  addStat('clear-streak');

  // Tabs
  const nav = doc.createElement('nav');
  nav.setAttribute('class', 'tabs');
  nav.setAttribute('id', 'console-tabs');
  hudIn.appendChild(nav);

  ['pending', 'dispatch', 'runs', 'roles'].forEach(p => {
    const btn = doc.createElement('button');
    btn.setAttribute('class', 'tab-btn' + (p === 'pending' ? ' on' : ''));
    btn.setAttribute('data-page', p);
    const sp = doc.createElement('span');
    sp.textContent = p;
    btn.appendChild(sp);
    if (p === 'pending') {
      const bg = doc.createElement('span');
      bg.setAttribute('class', 'badge');
      bg.setAttribute('id', 'tab-badge-pending');
      bg.textContent = '—';
      btn.appendChild(bg);
    }
    nav.appendChild(btn);
  });

  // Main
  const main = doc.createElement('main');
  main.setAttribute('id', 'console-main');
  main.setAttribute('class', 'main-viewport');
  root.appendChild(main);

  // Dock
  const dock = doc.createElement('div');
  dock.setAttribute('class', 'dock');
  dock.setAttribute('id', 'console-dock');
  dock.style.display = 'none';
  doc.body.appendChild(dock);

  const dockIn = doc.createElement('div');
  dockIn.setAttribute('class', 'dock-in');
  dock.appendChild(dockIn);

  const title = doc.createElement('span');
  title.setAttribute('id', 'console-dock-title');
  dockIn.appendChild(title);

  const status = doc.createElement('span');
  status.setAttribute('id', 'console-dock-status');
  dockIn.appendChild(status);

  const copyBtn = doc.createElement('button');
  copyBtn.setAttribute('id', 'console-dock-copy');
  dockIn.appendChild(copyBtn);

  const closeBtn = doc.createElement('button');
  closeBtn.setAttribute('id', 'console-dock-close');
  dockIn.appendChild(closeBtn);

  const txt = doc.createElement('textarea');
  txt.setAttribute('id', 'console-dock-text');
  dockIn.appendChild(txt);

  // Data scripts
  const snapScript = doc.createElement('script');
  snapScript.setAttribute('type', 'application/json');
  snapScript.setAttribute('id', 'console-snapshot');
  snapScript.textContent = JSON.stringify(snapshot);
  doc.body.appendChild(snapScript);

  const avatarsScript = doc.createElement('script');
  avatarsScript.setAttribute('type', 'application/json');
  avatarsScript.setAttribute('id', 'console-avatars');
  avatarsScript.textContent = JSON.stringify(avatars);
  doc.body.appendChild(avatarsScript);

  return doc;
}

// 模拟 DOMParser，能真实体现 mask 与 id 重命名
class MockDOMParser {
  parseFromString(xmlString, mimeType) {
    const doc = new MockDocument();
    if (xmlString && xmlString.includes('<svg')) {
      const svg = new MockNode('svg');
      doc.documentElement = svg;

      // 解析 mask id
      const maskMatch = xmlString.match(/<mask\s+[^>]*id="([^"]+)"/);
      if (maskMatch) {
        const maskNode = new MockNode('mask');
        maskNode.setAttribute('id', maskMatch[1]);
        svg.appendChild(maskNode);
      }

      // 解析 g mask="url(#...)"
      const gMatch = xmlString.match(/<g\s+[^>]*mask="([^"]+)"/);
      if (gMatch) {
        const gNode = new MockNode('g');
        gNode.setAttribute('mask', gMatch[1]);
        svg.appendChild(gNode);
      }

      return doc;
    }
    return doc;
  }
}

// 环境装载器
function bootEnvironment(snapshotData, options = {}) {
  const doc = setupMockDom(snapshotData);
  const context = {
    document: doc,
    DOMParser: MockDOMParser,
    // Node vm 上下文默认没有 URL；注入 Node 真实 URL 实现，不用自造 stub。
    URL: URL,
    navigator: {
      clipboard: options.clipboard !== undefined ? options.clipboard : {
        writeText: (val) => Promise.resolve()
      }
    }
  };
  context.window = context;
  context.globalThis = context;

  // 1. 运行 core.js
  vm.runInNewContext(CORE_SOURCE, context, { filename: CORE_PATH });
  assert.ok(context.AssistantConsole, 'core.js 必须成功初始化');

  // 2. 运行 shell.js
  vm.runInNewContext(SHELL_SOURCE, context, { filename: SHELL_PATH });
  return context;
}

// ---------------------------------------------------------------- 测试用例

test('shell.js：从数据节点装载并在页面启动时正确展示生成时间与指标（含未知态）', () => {
  const fixture = {
    generatedAt: '2026-10-20T10:42:00+08:00',
    stats: {
      pendingCount: { value: null, reason: '待处理缺失' }, // 未知不能为伪零
      runningCount: { value: 3 },
      completedToday: { value: 0 },
      dispatchedToday: { value: 0 }, // 0 分母
      completedWeek: { value: null },
      clearStreak: { value: null }
    },
    environment: { inOrca: true, executorsAvailable: ['claude'] },
    roles: [{ id: 'dev', name: '开发' }],
    pending: [],
    dispatchTasks: [],
    runs: []
  };

  const env = bootEnvironment(fixture);
  const doc = env.document;

  // 生成时间展示
  const genEl = doc.getElementById('console-gen-time');
  assert.equal(genEl.textContent, '生成于 10:42 · 页面不会自动刷新');

  // 指标 1：待处理未知显示“—”而不是 0
  const pVal = doc.querySelector('.stat[data-metric="pending"] .stat-val');
  assert.equal(pVal.textContent, '—');
  const badge = doc.getElementById('tab-badge-pending');
  assert.equal(badge.textContent, '—');

  // 指标 2：进行中正常数字
  const rVal = doc.querySelector('.stat[data-metric="running"] .stat-val');
  assert.equal(rVal.textContent, '3');

  // 指标 3：今日通关/出发分母为 0 时不画伪进度条
  const tpVal = doc.querySelector('.stat[data-metric="today-progress"] .stat-val');
  assert.equal(tpVal.textContent, '0 / 0');
  const tpBar = doc.querySelector('.stat[data-metric="today-progress"] .bar i');
  assert.equal(tpBar.style.width, '0%');

  // 指标 4、5：未知显示 —
  const wVal = doc.querySelector('.stat[data-metric="week-completed"] .stat-val');
  assert.equal(wVal.textContent, '—');
  const csVal = doc.querySelector('.stat[data-metric="clear-streak"] .stat-val');
  assert.equal(csVal.textContent, '—');
});

test('shell.js：今日通关/出发只知其一时显示 N / — 或 — / N，已知数不丢失且不画伪进度', () => {
  // 1. 已知通关，未知出发：显示 3 / —，进度条 0%
  {
    const fixture1 = {
      stats: {
        completedToday: { value: 3 },
        dispatchedToday: { value: null }
      },
      pending: [], runs: [], roles: []
    };
    const env1 = bootEnvironment(fixture1);
    const tpVal1 = env1.document.querySelector('.stat[data-metric="today-progress"] .stat-val');
    assert.equal(tpVal1.textContent, '3 / —');
    const tpBar1 = env1.document.querySelector('.stat[data-metric="today-progress"] .bar i');
    assert.equal(tpBar1.style.width, '0%');
  }

  // 2. 未知通关，已知出发：显示 — / 4，进度条 0%
  {
    const fixture2 = {
      stats: {
        completedToday: { value: null },
        dispatchedToday: { value: 4 }
      },
      pending: [], runs: [], roles: []
    };
    const env2 = bootEnvironment(fixture2);
    const tpVal2 = env2.document.querySelector('.stat[data-metric="today-progress"] .stat-val');
    assert.equal(tpVal2.textContent, '— / 4');
    const tpBar2 = env2.document.querySelector('.stat[data-metric="today-progress"] .bar i');
    assert.equal(tpBar2.style.width, '0%');
  }

  // 3. 两者皆未知：显示 —
  {
    const fixture3 = {
      stats: {
        completedToday: { value: null },
        dispatchedToday: { value: null }
      },
      pending: [], runs: [], roles: []
    };
    const env3 = bootEnvironment(fixture3);
    const tpVal3 = env3.document.querySelector('.stat[data-metric="today-progress"] .stat-val');
    assert.equal(tpVal3.textContent, '—');
  }
});

test('shell.js：今日通关大于出发时保留真实数字且比例条上限为 100%', () => {
  const fixture = {
    generatedAt: '2026-10-20T10:00:00+08:00',
    stats: {
      completedToday: { value: 5 },
      dispatchedToday: { value: 2 } // 跨日完成可能大于派出
    },
    pending: [],
    runs: [],
    roles: []
  };

  const env = bootEnvironment(fixture);
  const tpVal = env.document.querySelector('.stat[data-metric="today-progress"] .stat-val');
  assert.equal(tpVal.textContent, '5 / 2');
  const tpBar = env.document.querySelector('.stat[data-metric="today-progress"] .bar i');
  assert.equal(tpBar.style.width, '100%');
});

test('shell.js：未注册页面模块显示“页面模块尚未合入”而不伪造页面业务', () => {
  const fixture = {
    stats: {},
    pending: [],
    runs: [],
    roles: []
  };
  const env = bootEnvironment(fixture);
  const main = env.document.getElementById('console-main');

  // 初始进入 pending 页，由于 P3 未注册，显示模块尚未合入
  const card = main.querySelector('.placeholder-card');
  assert.ok(card, '应显示占位提示卡');
  assert.ok(card.textContent.includes('页面模块尚未合入'));
  assert.ok(card.textContent.includes('当前页签标识：pending'));
});

test('shell.js：注册页面 mount/dispose 正常执行且切页时不重复绑定事件', () => {
  const fixture = {
    stats: {},
    pending: [{ id: 'P-101', title: '示例' }],
    runs: [],
    roles: []
  };
  const env = bootEnvironment(fixture);
  const NC = env.AssistantConsole;

  let mountCallCount = 0;
  let disposeCallCount = 0;
  let receivedSnapshot = null;
  let receivedUi = null;

  NC.registerPage('dispatch', {
    mount: (container, snap, ui) => {
      mountCallCount += 1;
      receivedSnapshot = snap;
      receivedUi = ui;
      container.appendChild(ui.el('div', { 'class': 'dispatch-view' }, '派发视图'));
      return {
        dispose: () => {
          disposeCallCount += 1;
        }
      };
    }
  });

  // 切换到 dispatch
  env.__assistantShell.renderTab('dispatch');
  assert.equal(mountCallCount, 1);
  assert.equal(disposeCallCount, 0);
  assert.ok(receivedSnapshot, '应收到 snapshot');
  assert.ok(receivedUi, '应收到 ui 对象');

  const view = env.document.querySelector('.dispatch-view');
  assert.ok(view);
  assert.equal(view.textContent, '派发视图');

  // 再次切回 pending
  env.__assistantShell.renderTab('pending');
  assert.equal(disposeCallCount, 1, '切走时必须执行 dispose');
});

test('shell.js：selection 内存保留且包含 environment 与 roleDefinitions', () => {
  const fixture = {
    environment: { inOrca: true, executorsAvailable: ['claude', 'codex'] },
    roles: [
      { id: 'dev', name: '开发' },
      { id: 'pm', name: '跟进' }
    ],
    pending: [],
    runs: []
  };
  const env = bootEnvironment(fixture);
  const ui = env.__assistantShell.ui;

  assert.deepEqual(JSON.parse(JSON.stringify(ui.selection.environment)), fixture.environment);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.selection.roleDefinitions)), fixture.roles);

  // 写入选择
  ui.selection.pending['N-01'] = { choiceId: 'accept' };
  ui.selection.dispatch['T-01'] = { selected: true, roleId: 'dev' };

  // 模拟切页
  env.__assistantShell.renderTab('dispatch');
  env.__assistantShell.renderTab('roles');
  env.__assistantShell.renderTab('pending');

  // 选择在内存中跨页保留
  assert.equal(ui.selection.pending['N-01'].choiceId, 'accept');
  assert.equal(ui.selection.dispatch['T-01'].roleId, 'dev');
});

test('shell.js：安全 DOM 创建 ui.el 拒绝非法标签、拒绝 style、正确处理布尔属性与 safe href', () => {
  const env = bootEnvironment({ stats: {} });
  const ui = env.__assistantShell.ui;

  // 1. 正常创建合法标签与白名单属性
  const btn = ui.el('button', { 'class': 'primary', 'data-action': 'save' }, '确认');
  assert.equal(btn.getAttribute('class'), 'primary');
  assert.equal(btn.getAttribute('data-action'), 'save');
  assert.equal(btn.textContent, '确认');

  // 2. 拒绝非白名单标签（如 script, style, iframe, img, link, object 等）
  ['script', 'style', 'iframe', 'img', 'link', 'object', 'embed', 'video'].forEach(tag => {
    assert.throws(() => {
      ui.el(tag, {}, '');
    }, /拒绝非白名单标签/);
  });

  // 3. 拒绝 on* 事件属性（不回显原值）
  assert.throws(() => {
    ui.el('button', { onclick: 'alert(1)' }, '点击');
  }, /拒绝 on\* 事件属性/);

  // 4. 拒绝 style 属性
  assert.throws(() => {
    ui.el('div', { style: 'color:red' }, '内容');
  }, /拒绝 style 属性/);

  // 5. 拒绝第三参数传递 HTML 结构或非文本对象
  assert.throws(() => {
    ui.el('div', {}, { text: 'bad' });
  }, /只接受安全文本或数字/);

  // 6. 拒绝非白名单属性（如 formaction）
  assert.throws(() => {
    ui.el('input', { formaction: '/leak' }, '');
  }, /属性不在白名单/);

  // 7. 布尔属性处理：false 省略，true 设置为空字符串（避免 disabled=false 也禁用的问题）
  const enabledBtn = ui.el('button', { disabled: false }, '可用');
  assert.equal(enabledBtn.hasAttribute('disabled'), false, 'disabled: false 必须省略属性');

  const disabledBtn = ui.el('button', { disabled: true }, '禁用');
  assert.equal(disabledBtn.hasAttribute('disabled'), true, 'disabled: true 必须设置属性');
  assert.equal(disabledBtn.getAttribute('disabled'), '');

  // 8. href 属性限制：仅 a 标签允许，非 a 标签抛错
  assert.throws(() => {
    ui.el('div', { href: 'http://localhost:3000' }, '伪链接');
  }, /仅 a 标签允许 href/);

  // 9. href URL 安全白名单：允许安全 loopback HTTP 与 file URI
  const aLoopback = ui.el('a', { href: 'http://localhost:5173/export?test=1#hash' }, '本地服务');
  assert.equal(aLoopback.getAttribute('href'), 'http://localhost:5173/export?test=1#hash');

  const aFile = ui.el('a', { href: 'file:///C:/projects/index.html' }, '本地文件');
  assert.equal(aFile.getAttribute('href'), 'file:///C:/projects/index.html');

  // 10. href 拒绝 javascript: / data: / 外网 http 以及利用 CR/LF/tab 绕过的伪协议
  assert.throws(() => {
    ui.el('a', { href: 'javascript:alert(1)' }, '危险链接');
  }, /拒绝不安全 URL/);

  assert.throws(() => {
    ui.el('a', { href: 'java\nscript:alert(1)' }, '换行混淆链接');
  }, /拒绝不安全 URL/);

  assert.throws(() => {
    ui.el('a', { href: 'java\tscript:alert(1)' }, '制表符混淆链接');
  }, /拒绝不安全 URL/);

  assert.throws(() => {
    ui.el('a', { href: 'http://example.com/steal' }, '外网链接');
  }, /拒绝不安全 URL/);
});

test('shell.js：ui.avatar 渲染 6 角色 SVG 且每实例重命名 mask id 避免冲突，未知角色中性占位', () => {
  const env = bootEnvironment({ stats: {} });
  const ui = env.__assistantShell.ui;

  const maskIds = new Set();
  const maskRefs = new Set();

  // 已知 6 角色
  ['research', 'product', 'pm', 'investigate', 'dev', 'accept'].forEach(roleId => {
    const av = ui.avatar(roleId);
    assert.ok(av.classList.contains('console-avatar'));
    const svg = av.querySelector('svg');
    assert.ok(svg, `${roleId} 必须渲染为 SVG`);

    const maskNode = svg.querySelector('mask');
    assert.ok(maskNode, '必须包含 mask 元素');
    const idVal = maskNode.getAttribute('id');
    assert.ok(idVal.startsWith('viewboxMask-'), 'mask id 必须按实例重命名');
    assert.notEqual(idVal, 'viewboxMask', '不能沿用原始通用 id');
    maskIds.add(idVal);

    const gNode = svg.querySelector('g');
    assert.ok(gNode, '必须包含 g 元素');
    const maskAttr = gNode.getAttribute('mask');
    assert.equal(maskAttr, `url(#${idVal})`, 'g 元素的 mask 引用必须与重命名后的 mask id 严格匹配');
    maskRefs.add(maskAttr);
  });

  // 同一角色再次渲染，实例 id 依然互不相同
  const dev1 = ui.avatar('dev');
  const dev2 = ui.avatar('dev');
  const id1 = dev1.querySelector('mask').getAttribute('id');
  const id2 = dev2.querySelector('mask').getAttribute('id');
  assert.notEqual(id1, id2, '同一角色多次创建头像，mask id 也必须独立唯一');

  // 未知角色中性占位
  const unknownAv = ui.avatar('unknown-alien');
  assert.ok(unknownAv.querySelector('.console-avatar-placeholder'));
  assert.equal(unknownAv.querySelector('.console-avatar-placeholder').textContent, '?');
});

test('shell.js：从可导出页切到 runs 时隐藏并清空 dock，内存选择保留，重开当前页重新填充', () => {
  const env = bootEnvironment({ stats: {} });
  const ui = env.__assistantShell.ui;
  const doc = env.document;
  const dock = doc.getElementById('console-dock');
  const txt = doc.getElementById('console-dock-text');

  // 在待处理页生成导出指令
  ui.selection.pending['P-1'] = { choiceId: 'accept' };
  ui.openExport(['【控制台指令】验收：示例项目：T1 · 待验收 → 验收通过']);
  assert.equal(dock.style.display, 'block');
  assert.ok(txt.value.includes('【控制台指令】验收'));

  // 切换到运行监督页（runs 不导出）
  env.__assistantShell.renderTab('runs');
  assert.equal(dock.style.display, 'none', '切到 runs 时 dock 必须隐藏');
  assert.equal(txt.value, '', '切到 runs 时 dock textarea 必须清空');

  // 验证内存选择未丢失
  assert.equal(ui.selection.pending['P-1'].choiceId, 'accept');

  // 切回 pending 时重新触发导出，能重新填充指令
  env.__assistantShell.renderTab('pending');
  ui.openExport(['【控制台指令】验收：示例项目：T1 · 待验收 → 验收通过']);
  assert.equal(dock.style.display, 'block');
  assert.ok(txt.value.includes('【控制台指令】验收'));
});

test('shell.js：剪贴板分支（成功 / 异步拒绝 / 同步抛错 / API 不存在）均给出明确提示并选中文本', async () => {
  // 1. 成功分支：提示“已复制到剪贴板”
  {
    let copiedText = '';
    const env = bootEnvironment({ stats: {} }, {
      clipboard: {
        writeText: (val) => {
          copiedText = val;
          return Promise.resolve();
        }
      }
    });
    const ui = env.__assistantShell.ui;
    ui.openExport(['【控制台指令】拍板：T1 → A']);
    const copyBtn = env.document.getElementById('console-dock-copy');
    copyBtn.dispatchEvent({ type: 'click' });
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(copiedText, '【控制台指令】拍板：T1 → A');
    assert.equal(env.document.getElementById('console-dock-status').textContent, '已复制到剪贴板');
  }

  // 2. 异步拒绝分支（例如权限受阻）：提示拒绝并提示 Ctrl+C 手动复制
  {
    const env = bootEnvironment({ stats: {} }, {
      clipboard: {
        writeText: () => Promise.reject(new Error('Permission denied'))
      }
    });
    const ui = env.__assistantShell.ui;
    ui.openExport(['【控制台指令】拍板：T1 → A']);
    const copyBtn = env.document.getElementById('console-dock-copy');
    copyBtn.dispatchEvent({ type: 'click' });
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(env.document.getElementById('console-dock-status').textContent.includes('剪贴板访问被拒绝'));
    assert.ok(env.document.getElementById('console-dock-status').textContent.includes('Ctrl+C'));
  }

  // 3. 同步抛错分支（writeText 直接同步 throw）：不宣称成功，不吞异常，正确降级
  {
    const env = bootEnvironment({ stats: {} }, {
      clipboard: {
        writeText: () => {
          throw new Error('Sync clipboard error');
        }
      }
    });
    const ui = env.__assistantShell.ui;
    ui.openExport(['【控制台指令】拍板：T1 → A']);
    const copyBtn = env.document.getElementById('console-dock-copy');
    copyBtn.dispatchEvent({ type: 'click' });
    assert.ok(env.document.getElementById('console-dock-status').textContent.includes('剪贴板访问被拒绝'));
    assert.ok(env.document.getElementById('console-dock-status').textContent.includes('Ctrl+C'));
  }

  // 4. API 不存在分支（例如非安全上下文）：提示不可用并提示 Ctrl+C 手动复制
  {
    const env = bootEnvironment({ stats: {} }, {
      clipboard: null
    });
    const ui = env.__assistantShell.ui;
    ui.openExport(['【控制台指令】拍板：T1 → A']);
    const copyBtn = env.document.getElementById('console-dock-copy');
    copyBtn.dispatchEvent({ type: 'click' });
    assert.ok(env.document.getElementById('console-dock-status').textContent.includes('剪贴板 API 不可用'));
    assert.ok(env.document.getElementById('console-dock-status').textContent.includes('Ctrl+C'));
  }
});

test('shell.js：ui.navigate 仅允许合法页面与真实关联，且对包含引号/括号等特殊字符的 ID 准确查找不抛 SyntaxError', () => {
  const specialId1 = 'runs/2026-10-20-alpha["test"].md';
  const specialId2 = "runs/2026-10-20-beta'single'.md";
  const fixture = {
    pending: [{ id: 'P-1' }, { id: 'item[100]' }],
    runs: [{ id: specialId1 }, { id: specialId2 }]
  };
  const env = bootEnvironment(fixture);
  const ui = env.__assistantShell.ui;

  // 1. 普通 ID 正常跳转
  assert.equal(ui.navigate('pending', 'P-1'), true);

  // 2. 带方括号 ID 正常跳转，不抛 SyntaxError
  assert.equal(ui.navigate('pending', 'item[100]'), true);

  // 3. 带双引号与方括号的特殊 ID 正常跳转
  assert.equal(ui.navigate('runs', specialId1), true);

  // 4. 带单引号的特殊 ID 正常跳转
  assert.equal(ui.navigate('runs', specialId2), true);

  // 5. 非法页面 ID 拒绝
  assert.equal(ui.navigate('secret-page'), false);

  // 6. 关联不存在的 ID 拒绝
  assert.equal(ui.navigate('pending', 'NON_EXISTENT_ID'), false);
});

test('shell.js：P0 AssistantConsole 对象保持冻结，无额外扩展或篡改', () => {
  const env = bootEnvironment({ stats: {} });
  const NC = env.AssistantConsole;

  assert.ok(Object.isFrozen(NC), 'AssistantConsole 必须保持冻结');
  assert.throws(() => {
    NC.newProperty = 'leak';
  });
});

test('shell.js：体验链接接受性与 P0 safe_experience_url 同组一致（含 IPv6 本机）', () => {
  // 动态调用真实 P0；不在 Node 侧复制 P0 规则，也不硬编码 Python 输出。
  const PY_SCRIPT = [
    'import json, sys',
    'from pathlib import Path',
    "sys.path.insert(0, 'tools')",
    'import console_contract as cc',
    'root = Path(sys.argv[1])',
    'urls = json.loads(sys.argv[2])',
    'print(json.dumps([cc.safe_experience_url(u, root=root) for u in urls]))'
  ].join('\n');

  function runPython(args) {
    const options = {
      cwd: ROOT,
      encoding: 'utf8',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
    };
    try {
      return execFileSync('python', args, options);
    } catch (error) {
      if (error && error.code === 'ENOENT') {
        return execFileSync('python3', args, options);
      }
      throw error;
    }
  }

  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'assistant-url-consistency-'));
  try {
    // file 样例使用临时根内真实普通文件；其余全部为虚构 URL，不访问网络。
    const localFile = path.join(root, 'preview.html');
    fs.writeFileSync(localFile, '<!doctype html><title>preview</title>', 'utf8');

    // expected 是独立预期：P0 与 shell 都必须各自命中，不能靠两端同错“对齐”。
    const cases = [
      { label: 'IPv6 HTTP 带端口', href: 'http://[::1]:8801/console', expected: true },
      { label: 'IPv6 HTTPS 带端口', href: 'https://[::1]:8801/console', expected: true },
      { label: 'IPv6 HTTPS 无端口', href: 'https://[::1]/console', expected: true },
      { label: 'IPv6 HTTP 空端口', href: 'http://[::1]:/console', expected: true },
      { label: 'IPv6 HTTPS 空端口', href: 'https://[::1]:/console', expected: true },
      { label: 'IPv6 HTTP 空端口带 query/fragment', href: 'http://[::1]:/console?view=summary#top', expected: true },
      { label: 'IPv6 HTTPS 空端口带 query/fragment', href: 'https://[::1]:/console?view=summary#top', expected: true },
      { label: 'localhost 带端口与 query/fragment', href: 'http://localhost:8801/console?view=summary#top', expected: true },
      { label: 'localhost 无端口', href: 'http://localhost/console', expected: true },
      { label: 'localhost 空端口', href: 'http://localhost:/console', expected: true },
      { label: '127.0.0.1 loopback', href: 'http://127.0.0.1:8801/console', expected: true },
      { label: '127.0.0.1 空端口', href: 'http://127.0.0.1:/console', expected: true },
      { label: '临时根内 file 产物', href: pathToFileURL(localFile).href, expected: true },
      { label: '外网 http', href: 'http://example.com/console', expected: false },
      { label: '外网 https', href: 'https://evil.example/path', expected: false },
      { label: 'javascript 伪协议', href: 'javascript:alert(1)', expected: false },
      { label: 'data 伪协议', href: 'data:text/html,<script>alert(1)</script>', expected: false },
      { label: 'userinfo 用户名密码', href: 'http://user:pass@localhost:8801/console', expected: false },
      { label: 'userinfo 仅用户名', href: 'http://user@localhost:8801/console', expected: false },
      { label: '非本机 IPv6 (::2)', href: 'http://[::2]:8801/console', expected: false },
      { label: '非本机 IPv6 (文档地址)', href: 'http://[2001:db8::1]/console', expected: false },
      { label: '端口越界', href: 'http://localhost:99999/console', expected: false },
      { label: '非数字端口', href: 'http://[::1]:abc/console', expected: false },
      { label: '百分号编码主机', href: 'http://%6cocalhost/console', expected: false },
      { label: '反斜线规范化绕过', href: 'http://[::1]\\@evil.example/console', expected: false },
      { label: 'ftp 协议', href: 'ftp://[::1]/console', expected: false },
      { label: 'file 带远程主机', href: 'file://evil.example/preview.html', expected: false },
      { label: '协议相对地址', href: '//[::1]:8801/console', expected: false },
      { label: 'IPv6 zone id', href: 'http://[::1%25lo0]/console', expected: false },
      { label: 'IPv6 主机混入外网 userinfo', href: 'http://[::1]@evil.example/console', expected: false }
    ];

    const p0Accepted = JSON.parse(
      runPython(['-B', '-c', PY_SCRIPT, root, JSON.stringify(cases.map(c => c.href))]).trim()
    );

    const env = bootEnvironment({ stats: {} });
    const ui = env.__assistantShell.ui;
    function shellAccepts(href) {
      try {
        const anchor = ui.el('a', { href }, '打开体验');
        return anchor.getAttribute('href') === href;
      } catch (_error) {
        return false;
      }
    }

    const violations = [];
    cases.forEach((item, index) => {
      const p0Value = p0Accepted[index];
      const p0 = p0Value !== null && p0Value !== undefined;
      const shell = shellAccepts(item.href);
      if (p0 !== item.expected) {
        violations.push(`P0 预期不一致：${item.label} (${item.href}) → ${p0Value}`);
      }
      if (shell !== item.expected) {
        violations.push(`shell 预期不一致：${item.label} (${item.href}) → ${shell}`);
      }
      if (shell !== p0) {
        violations.push(`P0/shell 同组不一致：${item.label} (${item.href}) P0=${p0} shell=${shell}`);
      }
      // P0 接受时，把它实际返回的 URL 交给 ui.el；生产保留原 href，不回写 url.href。
      if (item.expected && p0 && !shellAccepts(p0Value)) {
        violations.push(`P0 返回值被 shell 拒绝：${item.label} (${p0Value})`);
      }
    });
    assert.deepEqual(violations, [], `同组 URL 接受性不一致：\n${violations.join('\n')}`);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('shell.js：注册页 mount 构建本机空端口体验链接不进入错误卡', () => {
  const hrefs = [
    'http://[::1]:/console',
    'https://[::1]:/console',
    'http://[::1]:/console?view=summary#top',
    'https://[::1]:/console?view=summary#top',
    'http://localhost:/console',
    'http://127.0.0.1:/console'
  ];
  const env = bootEnvironment({ stats: {} });
  const NC = env.AssistantConsole;
  const main = env.document.getElementById('console-main');

  NC.registerPage('pending', {
    mount: (container, _snap, ui) => {
      hrefs.forEach(href => container.appendChild(ui.el('a', { href }, '打开体验')));
      return { dispose: () => {} };
    }
  });

  // 薄 DOM 未实现 innerHTML 清空；按审查复现说明显式清掉启动时的占位内容。
  main.textContent = '';
  env.__assistantShell.renderTab('pending');

  const text = main.textContent;
  assert.ok(!text.includes('页面加载出错'), '不应显示页面加载出错');
  assert.ok(!text.includes('页面模块执行异常'), '不应显示页面模块执行异常');
  const links = main.querySelectorAll('a');
  assert.equal(links.length, hrefs.length);
  links.forEach((link, index) => {
    assert.equal(link.getAttribute('href'), hrefs[index]);
  });
});
