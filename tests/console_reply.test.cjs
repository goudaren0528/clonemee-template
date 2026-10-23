'use strict';
/*
 * tests/console_reply.test.cjs — v1.1 P6b reply.js（AssistantReply）的 Node 单测：
 * 两种打开方式（本机文件 / 通道页面）、回复列的状态优先级、回复框（字数、送往、发送这一条 / 复制指令、反馈、回复记录与三格进度）、
 * 发送成功 / 没发出去 / 过期、轮询合并与「记录有更新」、草稿、令牌不进页面。用假 fetch 和极简节点，不联网。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ASSETS = path.join(__dirname, '..', 'tools', 'console_assets');
const CORE = fs.readFileSync(path.join(ASSETS, 'core.js'), 'utf8');
const CHANNEL = fs.readFileSync(path.join(ASSETS, 'channel.js'), 'utf8');
const REPLY = fs.readFileSync(path.join(ASSETS, 'reply.js'), 'utf8');
const TOKEN = 'tok_' + 'b'.repeat(40);
const LIMITS = { maxChars: 500, perMinute: 10, dedupeSeconds: 10, pollSeconds: 20, maxLines: 20 };
const CHANNEL_NODE = { mode: 'channel', url: 'http://127.0.0.1:17318/', token: TOKEN, instance: 'abcdef12', limits: LIMITS, recordsVersion: 'aaaa0000' };

// ------------------------------------------------------------ 极简节点（只够 reply.js 的 h / refresh 用）

function el(tag) {
  const node = {
    tagName: tag.toUpperCase(),
    attrs: {},
    children: [],
    parentNode: null,
    value: '',
    setAttribute: function (k, v) { node.attrs[k] = String(v); },
    getAttribute: function (k) { return Object.prototype.hasOwnProperty.call(node.attrs, k) ? node.attrs[k] : null; },
    removeAttribute: function (k) { delete node.attrs[k]; },
    appendChild: function (child) { node.children.push(child); if (child && typeof child === 'object') { child.parentNode = node; } return child; },
    querySelectorAll: function (selector) { return select(node, selector); }
  };
  Object.defineProperty(node, 'textContent', {
    get: function () { return node.children.map(function (c) { return typeof c === 'string' ? c : c.textContent; }).join(''); },
    set: function (v) { node.children = [String(v)]; }
  });
  return node;
}

function h(tag, attrs, children) {
  const node = el(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    const v = attrs[k];
    if (v === null || v === undefined || v === false) {
      return;
    }
    node.setAttribute(k, v === true ? '' : v);
  });
  (Array.isArray(children) ? children : (children === undefined || children === null ? [] : [children])).forEach(function (c) {
    if (c !== null && c !== undefined) {
      node.appendChild(c);
    }
  });
  return node;
}

function all(node) {
  const out = [];
  (node.children || []).forEach(function (c) {
    if (typeof c !== 'string') {
      out.push(c);
      out.push.apply(out, all(c));
    }
  });
  return out;
}

function hasClass(node, name) {
  return (node.getAttribute('class') || '').split(/\s+/).indexOf(name) !== -1;
}

function select(root, selector) {
  return all(root).filter(function (node) {
    return selector.split(',').some(function (part) {
      const s = part.trim();
      if (s.charAt(0) === '[') {
        return node.getAttribute(s.slice(1, -1)) !== null;
      }
      if (s.charAt(0) === '.') {
        return hasClass(node, s.slice(1));
      }
      return node.tagName === s.toUpperCase();
    });
  });
}

function byClass(root, name) {
  return all(root).filter(function (node) { return hasClass(node, name); });
}

function buttons(root) {
  return all(root).filter(function (node) { return node.tagName === 'BUTTON'; });
}

function everyString(root) {
  const out = [];
  [root].concat(all(root)).forEach(function (node) {
    Object.keys(node.attrs).forEach(function (k) { out.push(node.attrs[k]); });
    node.children.forEach(function (c) { if (typeof c === 'string') { out.push(c); } });
  });
  return out.join('\n');
}

// ------------------------------------------------------------ 装载

function boot(node, options) {
  const opts = options || {};
  const calls = [];
  const timers = [];
  const store = new Map();
  const sandbox = {
    document: {
      hidden: false,
      getElementById: function (id) {
        return id === 'console-channel' && node ? { textContent: JSON.stringify(node) } : null;
      }
    },
    fetch: function (url, init) {
      calls.push({ url: url, init: init });
      const answer = typeof opts.answer === 'function' ? opts.answer(url, init) : opts.answer;
      if (answer instanceof Error) {
        return Promise.reject(answer);
      }
      if (answer && typeof answer.then === 'function') {
        return answer; // 让测试自己决定什么时候返回（发送中的用例）
      }
      return Promise.resolve({ json: function () { return Promise.resolve(answer); } });
    },
    setInterval: function (fn, ms) { timers.push({ fn: fn, ms: ms }); return timers.length; },
    clearInterval: function () {},
    localStorage: {
      getItem: function (k) { return store.has(k) ? store.get(k) : null; },
      setItem: function (k, v) { store.set(k, String(v)); },
      removeItem: function (k) { store.delete(k); }
    },
    Promise: Promise,
    JSON: JSON,
    Date: Date
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(CORE, sandbox, { filename: 'core.js' });
  vm.runInContext(CHANNEL, sandbox, { filename: 'channel.js' });
  vm.runInContext(REPLY, sandbox, { filename: 'reply.js' });
  return { R: sandbox.AssistantReply, NC: sandbox.AssistantConsole, calls: calls, timers: timers, store: store, sandbox: sandbox };
}

function slot(overrides) {
  return Object.assign({
    card: 'pending:N1020-04',
    allowed: true,
    reason: null,
    kind: 'main',
    receiver: '主会话',
    association: 'N1020-04',
    choices: [{ id: 'accept', label: '验收通过' }, { id: 'reject', label: '打回' }, { id: 'later', label: '稍后' }],
    needText: ['reject'],
    pageOnly: ['later'],
    device: { id: null, name: null, local: null }
  }, overrides || {});
}

function reply(overrides) {
  return Object.assign({
    id: 'N1021-07', createdAt: '2026-10-21T12:24:00+08:00', card: 'run:runs/a.md', page: 'runs', item: '项目：事项',
    association: '本仓库/runs/a.md', quick: '补充', text: '先做待处理', status: 'acked', statusNote: null,
    forwardedTo: '跟进 Agent', ack: { at: '2026-10-21T12:26:00+08:00', text: '已收到，按这个顺序排' },
    device: { id: null, name: null, local: null }
  }, overrides || {});
}

const VIEW = { page: 'pending', item: '示例项目A：导出指令', association: 'N1020-04', choices: slot().choices, quickLabel: '打回' };

function box(R, opts) {
  return R.box(h, Object.assign({
    card: 'pending:N1020-04', slot: slot(), quick: 'reject', text: '对比度不够', field: h('textarea', { 'class': 'cx-field' }),
    sendAction: 'pending-reply-send', copyAction: 'pending-reply-copy', forId: 'N1020-04'
  }, opts || {}));
}

/** vm 里的数组原型不同，deepStrictEqual 前先转成本 realm 的值。 */
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function settle() {
  return new Promise(function (resolve) { setImmediate(resolve); });
}

// ------------------------------------------------------------ 本机文件打开

test('本机文件打开：不能发，回复框只给「复制指令」；复制生成【控制台指令】回复一行，回复列变「已复制指令」（AC-20）', () => {
  const { R } = boot(null);
  assert.equal(R.mode(), 'file');
  assert.equal(R.canSend(), false);
  assert.equal(R.stale(), false);
  const node = box(R);
  const labels = buttons(node).map(function (b) { return b.textContent; });
  assert.deepEqual(labels, ['复制指令']);
  assert.equal(byClass(node, 'cx-route')[0].textContent, '送到主会话 → 主会话自己处理');
  assert.equal(byClass(node, 'cx-assoc')[0].textContent, '关联 N1020-04');
  assert.equal(byClass(node, 'cx-count')[0].textContent, '5 / 500');

  const opened = [];
  const line = R.copy('pending:N1020-04', VIEW, 'reject', '对比度不够', function (lines) { opened.push(plain(lines)); });
  assert.equal(line, '【控制台指令】回复：示例项目A：导出指令 · 打回：对比度不够 · 关联 N1020-04');
  assert.deepEqual(opened, [[line]]);
  assert.deepEqual(Object.assign({}, R.statusView('pending:N1020-04', {})), { text: '已复制指令', tone: 'gray', key: 'copied' });
  const after = box(R);
  assert.equal(byClass(after, 'cx-line')[0].textContent, line);
});

test('复制指令：没选也没写、或内容不合格时不生成，回复列「没发出去」并说明原因', () => {
  const { R } = boot(null);
  const opened = [];
  assert.equal(R.copy('pending:N1020-04', VIEW, null, '   ', function (lines) { opened.push(lines); }), null);
  assert.equal(opened.length, 0);
  assert.equal(R.statusView('pending:N1020-04', {}).text, '没发出去');
  assert.match(R.local('pending:N1020-04').message, /生成不了指令/);
});

// ------------------------------------------------------------ 回复列

test('回复列：另一台设备 > 本地状态 > 最新一条回复 > 草稿 > 「—」；回复按时间新的在前', () => {
  const { R } = boot(CHANNEL_NODE);
  R.seed({ replies: [
    reply({ id: 'N1021-01', createdAt: '2026-10-21T10:00:00+08:00', status: 'acked' }),
    reply({ id: 'N1021-05', createdAt: '2026-10-21T11:00:00+08:00', status: 'lost', statusNote: '会话不在了' })
  ] });
  assert.deepEqual(plain(R.records('run:runs/a.md').map(function (r) { return r.id; })), ['N1021-05', 'N1021-01']);
  assert.equal(R.statusView('run:runs/a.md', {}).text, '转不到');
  assert.equal(R.statusView('run:runs/a.md', {}).tone, 'orange');
  assert.equal(R.statusView('run:runs/a.md', { slot: slot({ allowed: false, reason: 'other_device' }) }).text, '另一台设备');
  assert.equal(R.statusView('pending:N1020-04', { hasDraft: true }).text, '草稿');
  assert.deepEqual(Object.assign({}, R.statusView('pending:N1020-04', {})), { text: '—', tone: 'gray', key: 'none' });
  R.seed({ replies: [reply({ card: 'x' })] }); // 只打底一次
  assert.equal(R.records('x').length, 0);
});

test('回复列构件：文字和色调一起表达（类名带色调，title 给全文）', () => {
  const { R } = boot(null);
  const cell = R.cell(h, { text: '已送达主会话', tone: 'blue', key: 'delivered' }, 'act-c-reply');
  assert.equal(cell.getAttribute('class'), 'act-c-reply cx-reply cx-tone-blue');
  assert.equal(cell.getAttribute('title'), '已送达主会话');
  assert.equal(cell.getAttribute('data-reply-state'), 'delivered');
  assert.equal(cell.textContent, '已送达主会话');
});

// ------------------------------------------------------------ 回复框

test('回复框：字数实时、超 500 变红且发不出去；必填没写给提示；稍后只在页面标记（AC-15、AC-26）', () => {
  const { R } = boot(CHANNEL_NODE);
  const over = box(R, { text: '字'.repeat(501) });
  assert.ok(hasClass(byClass(over, 'cx-count')[0], 'cx-over'));
  assert.equal(byClass(over, 'cx-count')[0].textContent, '501 / 500');
  assert.equal(byClass(over, 'cx-send')[0].getAttribute('disabled'), '');
  assert.equal(byClass(over, 'cx-feedback')[0].textContent, '超过 500 字，删短一点再发');

  const need = box(R, { text: '' });
  assert.equal(byClass(need, 'cx-send')[0].getAttribute('disabled'), '');
  assert.equal(byClass(need, 'cx-feedback')[0].textContent, '这个决定要写一句原因');

  const later = box(R, { quick: 'later', text: '' });
  assert.equal(byClass(later, 'cx-feedback')[0].textContent, '「稍后」只在页面上标记，不发送');

  const ok = box(R);
  assert.equal(byClass(ok, 'cx-send')[0].getAttribute('disabled'), null);
  assert.equal(byClass(ok, 'cx-send')[0].textContent, '发送这一条');
  assert.equal(byClass(ok, 'cx-copy').length, 0, '通道开着且没失败时不给复制指令');
});

test('回复框 refresh：打字时只改字数和按钮可用状态，不重建节点', () => {
  const { R } = boot(CHANNEL_NODE);
  const container = h('div', null, [box(R, { text: '' })]);
  const send = byClass(container, 'cx-send')[0];
  assert.equal(send.getAttribute('disabled'), '');
  R.refresh(container, 'pending:N1020-04', slot(), 'reject', '深色看不清');
  assert.equal(byClass(container, 'cx-send')[0], send);
  assert.equal(send.getAttribute('disabled'), null);
  assert.equal(byClass(container, 'cx-count')[0].textContent, '5 / 500');
  R.refresh(container, 'pending:N1020-04', slot(), 'reject', '字'.repeat(600));
  assert.equal(send.getAttribute('disabled'), '');
  assert.ok(hasClass(byClass(container, 'cx-count')[0], 'cx-over'));
});

test('另一台设备：回复框不给输入和按钮，只提示去那台设备的面板上发，回复记录照样显示（AC-33）', () => {
  const { R } = boot(CHANNEL_NODE);
  const other = slot({ allowed: false, reason: 'other_device', kind: 'forward', receiver: '跟进 Agent', device: { id: 'x', name: 'LAPTOP-乙', local: false } });
  const node = box(R, { slot: other });
  assert.equal(buttons(node).length, 0);
  assert.equal(all(node).filter(function (n) { return n.tagName === 'TEXTAREA'; }).length, 0);
  assert.equal(byClass(node, 'cx-warn')[0].textContent, '这件事在 LAPTOP-乙 上跑，回复请到那台设备的面板上发');
  assert.equal(byClass(node, 'cx-route')[0].textContent, '送到主会话 → 转给 跟进 Agent');
});

test('回复记录：时间、快捷评价和正文、编号、三格进度；转不到第三格标橙并写怎么办；回执原文（AC-18、AC-21、AC-22）', () => {
  const { R } = boot(CHANNEL_NODE);
  R.seed({ replies: [
    reply({ id: 'N1021-07', card: 'pending:N1020-04', status: 'lost', statusNote: '跟进 Agent 的会话不在了', ack: null }),
    reply({ id: 'N1021-05', card: 'pending:N1020-04', createdAt: '2026-10-21T11:58:00+08:00', quick: '问进度', text: '什么时候交审查？', status: 'acked' })
  ] });
  const node = box(R, { slot: slot({ kind: 'forward', receiver: '跟进 Agent' }) });
  const items = byClass(node, 'cx-record');
  assert.equal(items.length, 2);
  assert.equal(byClass(items[0], 'cx-record-said')[0].textContent, '补充：先做待处理');
  assert.equal(byClass(items[0], 'cx-record-id')[0].textContent, 'N1021-07');
  const lostSteps = byClass(items[0], 'cx-step').map(function (s) { return s.getAttribute('class').replace('cx-step cx-step-', '') + ':' + s.textContent; });
  assert.deepEqual(lostSteps, ['done:已送达主会话', 'done:已转给跟进', 'lost:跟进已收到']);
  assert.match(byClass(items[0], 'cx-record-lost')[0].textContent, /^转不到：跟进 Agent 的会话不在了。.*重开跟进接着做 \/ 我自己处理/);
  const ackedSteps = byClass(items[1], 'cx-step').map(function (s) { return s.getAttribute('class').replace('cx-step cx-step-', ''); });
  assert.deepEqual(ackedSteps, ['done', 'done', 'done']);
  assert.equal(byClass(items[1], 'cx-record-ack')[0].textContent, '回执：已收到，按这个顺序排');

  const main = R.box(h, { card: 'pending:N1020-04', slot: slot(), quick: null, text: '', field: null, sendAction: 'a', copyAction: 'b', forId: 'x' });
  const mainSteps = byClass(byClass(main, 'cx-record')[0], 'cx-step').map(function (s) { return s.textContent; });
  assert.deepEqual(mainSteps, ['已送达主会话', '主会话已处理'], '主会话自己处理的卡片是两格');
});

// ------------------------------------------------------------ 发送

test('发送成功：POST /api/replies 带令牌头和正文；回复列先「发送中」再「已送达主会话」，本地先记一条、草稿清掉（AC-14、AC-17）', async () => {
  const env = boot(CHANNEL_NODE, { answer: { ok: true, id: 'N1021-07', status: 'delivered', message: '已送达主会话 · 编号 N1021-07', duplicate: false } });
  const { R } = env;
  R.saveDraft('pending:N1020-04', '对比度不够');
  assert.equal(R.draft('pending:N1020-04'), '对比度不够');
  const seen = [];
  const off = R.subscribe(function () { seen.push(R.statusView('pending:N1020-04', {}).text); });
  const pending = R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'reject', text: '对比度不够' }, VIEW);
  assert.equal(R.statusView('pending:N1020-04', {}).text, '发送中');
  const sending = box(R);
  assert.equal(byClass(sending, 'cx-send')[0].textContent, '发送中…');
  assert.equal(byClass(sending, 'cx-send')[0].getAttribute('disabled'), '');
  const result = await pending;
  off();
  assert.equal(result.ok, true);
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0].url, '/api/replies');
  assert.equal(env.calls[0].init.method, 'POST');
  assert.equal(env.calls[0].init.headers['X-Assistant-Token'], TOKEN);
  assert.deepEqual(JSON.parse(env.calls[0].init.body), { card: 'pending:N1020-04', quick: 'reject', text: '对比度不够', lines: null });
  assert.deepEqual(seen, ['发送中', '已送达主会话']);
  const latest = R.records('pending:N1020-04')[0];
  assert.equal(latest.id, 'N1021-07');
  assert.equal(latest.quick, '打回');
  assert.equal(latest.text, '对比度不够');
  assert.equal(R.draft('pending:N1020-04'), '', '发出去后草稿清掉');
  const after = box(R, { text: '' });
  assert.ok(hasClass(byClass(after, 'cx-feedback')[0], 'cx-good'));
  assert.equal(byClass(after, 'cx-feedback')[0].textContent, '已送达主会话 · 编号 N1021-07');
});

test('派发确认 / 角色管理：lines 原样作为一条发出', async () => {
  const env = boot(CHANNEL_NODE, { answer: { ok: true, id: 'N1021-09', status: 'delivered', message: 'ok' } });
  await env.R.send('dispatch', { card: 'dispatch', quick: null, text: '', lines: ['【控制台指令】确认派发：a', '【控制台指令】确认派发：b'] },
    { page: 'dispatch', item: '指挥台：派发确认', association: '—' });
  assert.deepEqual(JSON.parse(env.calls[0].init.body).lines, ['【控制台指令】确认派发：a', '【控制台指令】确认派发：b']);
  assert.equal(env.R.statusView('dispatch', {}).text, '已送达主会话');
});

test('没发出去：网络失败给「重试」和「复制指令」，回复列「没发出去」（AC-17）', async () => {
  const { R } = boot(CHANNEL_NODE, { answer: new Error('net') });
  const result = await R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'reject', text: '对比度不够' }, VIEW);
  assert.equal(result.ok, false);
  assert.equal(result.code, 'offline');
  assert.deepEqual(Object.assign({}, R.statusView('pending:N1020-04', {})), { text: '没发出去', tone: 'red', key: 'failed' });
  const node = box(R);
  // 连不上就是断线：按钮改复制指令（AC-20、P6b-r1 R3），重新连上（轮询成功）才回到「重试」
  assert.deepEqual(buttons(node).map(function (b) { return b.textContent; }), ['复制指令']);
  assert.equal(R.offline(), true);
  assert.equal(R.stale(), false);
  assert.ok(hasClass(byClass(node, 'cx-feedback')[0], 'cx-bad'));
  assert.equal(byClass(node, 'cx-feedback')[0].textContent, '本地通道连不上，改用复制指令');
  assert.equal(R.records('pending:N1020-04').length, 0, '失败不记回复');
});

test('页面过期：通道回 stale 后不能再发，回复框提示刷新后重发，草稿还在（AC-25）', async () => {
  const { R } = boot(CHANNEL_NODE, { answer: { ok: false, code: 'stale', message: '通道重启过，刷新页面后重发' } });
  R.saveDraft('pending:N1020-04', '对比度不够');
  await R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'reject', text: '对比度不够' }, VIEW);
  assert.equal(R.stale(), true);
  assert.equal(R.canSend(), false);
  assert.equal(R.mode(), 'channel');
  const node = box(R);
  assert.deepEqual(buttons(node).map(function (b) { return b.textContent; }), ['复制指令']);
  assert.equal(byClass(node, 'cx-feedback')[0].textContent, '通道重启过，刷新页面后重发（写的字刷新后还在）');
  assert.equal(R.draft('pending:N1020-04'), '对比度不够');
  const again = await R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'reject', text: 'x' }, VIEW);
  assert.equal(again.code, 'stale');
});

// ------------------------------------------------------------ 轮询与记录有更新

test('轮询：回复状态变了就通知页面；记录版本和页面不同时「记录有更新」（AC-18）', async () => {
  const env = boot(CHANNEL_NODE, {
    answer: { ok: true, replies: [reply({ status: 'forwarded' })], recordsVersion: 'aaaa0000' }
  });
  const { R } = env;
  R.seed({ replies: [reply({ status: 'delivered', ack: null })] });
  let notified = 0;
  R.subscribe(function () { notified += 1; });
  R.startPolling();
  R.startPolling();
  assert.equal(env.timers.length, 1, '只起一个轮询');
  assert.equal(env.timers[0].ms, 20000);
  env.timers[0].fn();
  await settle();
  assert.equal(R.statusView('run:runs/a.md', {}).text, '已转给跟进');
  assert.equal(notified, 1);
  assert.equal(R.recordsChanged(), false);
  env.timers[0].fn();
  await settle();
  assert.equal(notified, 1, '没变化不通知');
});

test('轮询：服务端记录版本变了 → recordsChanged()；发出但还没被收录的本地记录保留', async () => {
  const env = boot(CHANNEL_NODE, { answer: function (url, init) {
    if (init.method === 'POST') {
      return { ok: true, id: 'N1021-11', status: 'waiting', message: '已记清单' };
    }
    return { ok: true, replies: [], recordsVersion: 'bbbb1111' };
  } });
  const { R } = env;
  R.seed({ replies: [] });
  R.startPolling();
  await R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'accept', text: '' }, VIEW);
  env.timers[0].fn();
  await settle();
  assert.equal(R.recordsChanged(), true);
  assert.deepEqual(plain(R.records('pending:N1020-04').map(function (r) { return r.id; })), ['N1021-11']);
  assert.equal(R.statusView('pending:N1020-04', {}).text, '等主会话');
});

test('本机文件打开不轮询', () => {
  const env = boot(null);
  env.R.seed({ replies: [] });
  env.R.startPolling();
  assert.equal(env.timers.length, 0);
});

// ------------------------------------------------------------ 安全

test('令牌不进回复框的文字、属性和复制的指令', async () => {
  const { R } = boot(CHANNEL_NODE, { answer: new Error('net') });
  await R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'reject', text: '对比度不够' }, VIEW);
  R.copy('pending:N1020-04', VIEW, 'reject', '对比度不够', function () {});
  const node = box(R);
  assert.equal(everyString(node).indexOf(TOKEN), -1);
  assert.equal(JSON.stringify(R.local('pending:N1020-04')).indexOf(TOKEN), -1);
});

// ------------------------------------------------------------ P6b-r1 审查回归

test('R1 同一张卡片发送中不能重复发：第二次直接返回 sending，只有一个 POST；输入刷新也保持禁用', async () => {
  let release = null;
  const gate = { then: function () { return this; } };
  const deferred = new Promise(function (resolve) { release = resolve; });
  const env = boot(CHANNEL_NODE, { answer: function () { return deferred; } });
  const R = env.R;
  const first = R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'reject', text: 'first reason' }, VIEW);
  const second = await R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'reject', text: 'second reason while sending' }, VIEW);
  assert.equal(second.ok, false);
  assert.equal(second.code, 'sending');
  assert.equal(env.calls.length, 1, '发送中不能再发第二个请求');
  const container = h('div', null, [box(R)]);
  const send = byClass(container, 'cx-send')[0];
  assert.equal(send.getAttribute('disabled'), '');
  R.refresh(container, 'pending:N1020-04', slot(), 'reject', 'second reason while sending');
  assert.equal(send.getAttribute('disabled'), '', '输入事件不能把发送中的按钮放开');
  release({ json: function () { return Promise.resolve({ ok: true, id: 'N1021-11', status: 'delivered', message: '已送达主会话' }); } });
  await first;
  assert.equal(R.statusView('pending:N1020-04', {}).text, '已送达主会话');
  assert.equal(await R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'accept', text: '' }, VIEW).then(function (r) { return r.ok; }), true,
    '这次回来了，可以再发');
  void gate;
});

test('R1 发送中写的字不会被旧请求清掉：只清这次发出去的内容', async () => {
  let release = null;
  const deferred = new Promise(function (resolve) { release = resolve; });
  const env = boot(CHANNEL_NODE, { answer: function () { return deferred; } });
  const R = env.R;
  const pending = R.send('pending:N1020-04', { card: 'pending:N1020-04', quick: 'reject', text: 'submitted text' }, VIEW);
  R.setForm('pending:N1020-04', { text: 'new text while sending' });
  release({ json: function () { return Promise.resolve({ ok: true, id: 'N1021-12', status: 'delivered', message: 'ok' }); } });
  await pending;
  assert.equal(R.draft('pending:N1020-04'), 'new text while sending', '等结果时写的字要留着');
  assert.equal(R.form('pending:N1020-04').text, 'new text while sending');
});

test('R2 轮询发现旧令牌：通知页面进入过期态，发送按钮变复制指令（AC-25）', async () => {
  const env = boot(CHANNEL_NODE, { answer: { ok: false, code: 'stale', message: '通道重启过，刷新页面后重发' } });
  const R = env.R;
  R.seed({ replies: [] });
  let notified = 0;
  R.subscribe(function () { notified += 1; });
  R.startPolling();
  env.timers[0].fn();
  await settle();
  assert.equal(notified, 1, '过期要通知页面重画');
  assert.equal(R.stale(), true);
  assert.equal(R.canSend(), false);
  const node = box(R);
  assert.deepEqual(buttons(node).map(function (b) { return b.textContent; }), ['复制指令']);
  assert.equal(byClass(node, 'cx-feedback')[0].textContent, '通道重启过，刷新页面后重发（写的字刷新后还在）');
});

test('R3 中途断线：通知页面降级（offline，不是过期），恢复后回到能发（AC-20）', async () => {
  let down = true;
  const env = boot(CHANNEL_NODE, { answer: function () { return down ? new Error('net') : { ok: true, replies: [], recordsVersion: 'aaaa0000' }; } });
  const R = env.R;
  R.seed({ replies: [] });
  const states = [];
  R.subscribe(function () { states.push([R.offline(), R.stale(), R.canSend()]); });
  R.startPolling();
  env.timers[0].fn();
  await settle();
  assert.deepEqual(states, [[true, false, false]]);
  const node = box(R);
  assert.deepEqual(buttons(node).map(function (b) { return b.textContent; }), ['复制指令'], '断线时只有复制指令');
  down = false;
  env.timers[0].fn();
  await settle();
  assert.deepEqual(states, [[true, false, false], [false, false, true]], '连上了要通知恢复');
  assert.equal(byClass(box(R), 'cx-send')[0].textContent, '发送这一条');
});

test('R4 keepFocus：重画时保住输入框焦点和光标，回复状态照样更新（AC-18、AC-14）', () => {
  const env = boot(CHANNEL_NODE);
  const R = env.R;
  const doc = env.sandbox.document;
  const field = h('textarea', { 'data-action': 'pending-note', 'data-for': 'N1020-04' });
  field.selectionStart = 3;
  field.selectionEnd = 5;
  const container = h('div', null, [field]);
  doc.activeElement = field;
  let renders = 0;
  const focused = [];
  const view = R.keepFocus(container, function () {
    renders += 1;
    const next = h('textarea', { 'data-action': 'pending-note', 'data-for': 'N1020-04' });
    next.focus = function () { focused.push('focus'); doc.activeElement = next; };
    next.setSelectionRange = function (a, b) { next.sel = [a, b]; };
    container.children = [next];
    next.parentNode = container;
  });
  view.request();
  assert.equal(renders, 1, '有焦点也要重画（回复列、底栏计数要更新）');
  assert.deepEqual(focused, ['focus']);
  assert.deepEqual(container.children[0].sel, [3, 5], '光标位置保持');

  doc.activeElement = null;
  view.request();
  assert.equal(renders, 2);
  const other = h('div');
  doc.activeElement = other;
  const plain = R.keepFocus(container, function () { renders += 1; });
  plain.request();
  assert.equal(renders, 3, '焦点不在这个容器里：直接重画');
  plain.blur();
});

test('reply.js 不用 innerHTML / eval / 外部地址', () => {
  assert.equal(/innerHTML|outerHTML|insertAdjacentHTML|\beval\(|new Function/.test(REPLY), false);
  assert.equal(/https?:\/\//.test(REPLY), false);
});
