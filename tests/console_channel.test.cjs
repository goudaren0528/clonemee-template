'use strict';
/*
 * tests/console_channel.test.cjs — v1.1 P3 channel.js（AssistantChannel）的 Node 单测：
 * 页面数据节点解析、send 的成功 / 失败 / 过期 / 离线、poll 的请求头和停止、草稿暂存。用假 fetch，不联网。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'tools', 'console_assets', 'channel.js'), 'utf8');
const TOKEN = 'tok_' + 'a'.repeat(40);

function boot(node, options) {
  const opts = options || {};
  const calls = [];
  const timers = [];
  const store = new Map();
  const context = {
    document: {
      hidden: false,
      getElementById: function (id) {
        return id === 'console-channel' && node !== undefined ? { textContent: JSON.stringify(node) } : null;
      }
    },
    fetch: opts.noFetch ? undefined : function (url, init) {
      calls.push({ url: url, init: init });
      if (opts.fetchThrows) {
        return Promise.reject(new Error('net'));
      }
      const reply = typeof opts.reply === 'function' ? opts.reply(url, init) : (opts.reply || { ok: true, id: 'N1021-07', status: 'delivered', message: '已送达主会话 · 编号 N1021-07' });
      return Promise.resolve({ json: function () { return Promise.resolve(reply); } });
    },
    setInterval: function (fn, ms) { timers.push({ fn: fn, ms: ms }); return timers.length; },
    clearInterval: function (id) { timers[id - 1].cleared = true; },
    localStorage: opts.noStorage ? undefined : {
      getItem: function (k) { return store.has(k) ? store.get(k) : null; },
      setItem: function (k, v) { store.set(k, String(v)); },
      removeItem: function (k) { store.delete(k); }
    },
    Promise: Promise,
    JSON: JSON
  };
  vm.runInNewContext(SOURCE, context, { filename: 'channel.js' });
  return { api: context.AssistantChannel, calls: calls, timers: timers, store: store, context: context };
}

const CHANNEL = { mode: 'channel', url: 'http://127.0.0.1:17318/', token: TOKEN, instance: 'abcdef12', limits: { maxChars: 500, perMinute: 10, dedupeSeconds: 10, pollSeconds: 20, maxLines: 20 } };

test('info：通道页面是 channel 模式且不暴露令牌；文件页面 / 缺节点 / 坏地址都是 file', () => {
  const ch = boot(CHANNEL).api;
  assert.equal(ch.info().mode, 'channel');
  assert.equal(ch.info().url, 'http://127.0.0.1:17318/');
  assert.equal(JSON.stringify(ch.info()).indexOf(TOKEN), -1);
  assert.equal(ch.canSend(), true);
  assert.ok(Object.isFrozen(ch));
  assert.equal(boot({ mode: 'file', url: 'http://127.0.0.1:17318/' }).api.info().mode, 'file');
  assert.equal(boot(undefined).api.info().mode, 'file');
  assert.equal(boot(Object.assign({}, CHANNEL, { url: 'http://evil.example:17318/' })).api.canSend(), false);
  assert.equal(boot(Object.assign({}, CHANNEL, { token: 'short' })).api.canSend(), false);
  assert.equal(boot(CHANNEL, { noFetch: true }).api.canSend(), false);
});

test('send：同源 POST、令牌只在请求头、JSON 正文；成功返回编号和状态', async () => {
  const env = boot(CHANNEL);
  const result = await env.api.send({ card: 'pending:N1021-03', quick: 'reject', text: '看不清' });
  assert.equal(result.ok, true);
  assert.equal(result.id, 'N1021-07');
  assert.equal(result.status, 'delivered');
  const call = env.calls[0];
  assert.equal(call.url, '/api/replies');
  assert.equal(call.init.method, 'POST');
  assert.equal(call.init.headers['X-Assistant-Token'], TOKEN);
  assert.equal(call.init.headers['Content-Type'], 'application/json');
  assert.equal(call.url.indexOf(TOKEN), -1);
  assert.deepEqual(JSON.parse(call.init.body), { card: 'pending:N1021-03', quick: 'reject', text: '看不清', lines: null });
});

test('send：服务端拒绝给原因；stale 之后不能再发；网络失败给 offline；从不 reject', async () => {
  const rejected = boot(CHANNEL, { reply: { ok: false, code: 'too_long', message: '正文超过 500 字' } });
  assert.deepEqual(Object.assign({}, await rejected.api.send({ card: 'pending:N1021-03', text: 'x' })), { ok: false, code: 'too_long', message: '正文超过 500 字' });
  const stale = boot(CHANNEL, { reply: { ok: false, code: 'stale', message: '通道重启过，刷新页面后重发' } });
  const first = await stale.api.send({ card: 'pending:N1021-03', text: 'x' });
  assert.equal(first.code, 'stale');
  assert.equal(stale.api.canSend(), false);
  const second = await stale.api.send({ card: 'pending:N1021-03', text: 'x' });
  assert.equal(second.code, 'stale');
  assert.equal(stale.calls.length, 1, '过期后不再请求');
  const offline = boot(CHANNEL, { fetchThrows: true });
  assert.equal((await offline.api.send({ card: 'dispatch', lines: ['【控制台指令】确认派发：x'] })).code, 'offline');
  assert.equal((await boot({ mode: 'file', url: null }).api.send({ card: 'dispatch' })).code, 'offline');
});

test('poll：按间隔拉回复、带令牌头和卡片参数；页面隐藏时跳过；stop 清掉定时器；文件模式不轮询', async () => {
  const replies = [{ id: 'N1021-07', card: 'pending:N1021-03', status: 'acked' }];
  const env = boot(CHANNEL, { reply: { ok: true, replies: replies } });
  const got = [];
  const stop = env.api.poll(['pending:N1021-03', 'run:runs/a b.md'], function (list) { got.push(list); });
  assert.equal(env.timers.length, 1);
  assert.equal(env.timers[0].ms, 20000);
  env.timers[0].fn();
  await new Promise(function (r) { setTimeout(r, 0); });
  assert.equal(env.calls[0].url, '/api/replies?cards=pending%3AN1021-03,run%3Aruns%2Fa%20b.md');
  assert.equal(env.calls[0].init.method, 'GET');
  assert.equal(env.calls[0].init.headers['X-Assistant-Token'], TOKEN);
  assert.equal(got.length, 1);
  env.context.document.hidden = true;
  env.timers[0].fn();
  assert.equal(env.calls.length, 1, '页面隐藏时不拉');
  stop();
  assert.equal(env.timers[0].cleared, true);
  const file = boot({ mode: 'file', url: null });
  file.api.poll(['x'], function () {});
  assert.equal(file.timers.length, 0);
});

test('drafts：存 localStorage，存不了就放内存；clear 清掉', () => {
  const env = boot(CHANNEL);
  env.api.drafts.set('pending:N1021-03', '草稿');
  assert.equal(env.store.get('assistant-draft:pending:N1021-03'), '草稿');
  assert.equal(env.api.drafts.get('pending:N1021-03'), '草稿');
  env.api.drafts.clear('pending:N1021-03');
  assert.equal(env.api.drafts.get('pending:N1021-03'), '');
  const memory = boot(CHANNEL, { noStorage: true });
  memory.api.drafts.set('roles', '只在内存');
  assert.equal(memory.api.drafts.get('roles'), '只在内存');
});

test('subscribe：轮询发现旧令牌通知一次、断线通知、恢复再通知（P6b-r1 R2、R3）', async () => {
  const answers = [];
  const env = boot(CHANNEL, { reply: function () { return answers.shift(); } });
  const seen = [];
  env.api.subscribe(function () { seen.push({ stale: env.api.info().stale, offline: env.api.info().offline, canSend: env.api.canSend() }); });
  env.api.poll([], function () {});
  answers.push({ ok: false, code: 'stale' });
  env.timers[0].fn();
  await new Promise(function (r) { setTimeout(r, 0); });
  assert.deepEqual(seen, [{ stale: true, offline: false, canSend: false }]);
  assert.equal(env.api.canSend(), false);
  env.timers[0].fn();
  await new Promise(function (r) { setTimeout(r, 0); });
  assert.equal(seen.length, 1, '过期后不再自动轮询，也不重复通知');

  const net = boot(CHANNEL, { fetchThrows: true });
  const states = [];
  net.api.subscribe(function () { states.push([net.api.info().stale, net.api.info().offline, net.api.canSend()]); });
  net.api.poll([], function () {});
  net.timers[0].fn();
  await new Promise(function (r) { setTimeout(r, 0); });
  assert.deepEqual(states, [[false, true, false]], '断线：offline，不是过期');
  assert.equal(net.api.info().url, CHANNEL.url, '断线后还留着地址，恢复时用');
  net.timers[0].fn();
  await new Promise(function (r) { setTimeout(r, 0); });
  assert.equal(states.length, 1, '一直断线不重复通知');
});

test('subscribe：发送时断线 / 过期会通知，能回话时恢复', async () => {
  const offline = boot(CHANNEL, { fetchThrows: true });
  const seen = [];
  offline.api.subscribe(function () { seen.push(offline.api.canSend()); });
  assert.equal((await offline.api.send({ card: 'dispatch', lines: [] })).code, 'offline');
  assert.deepEqual(seen, [false]);
  assert.equal(offline.api.info().offline, true);

  const answers = [{ ok: false, code: 'stale' }, { ok: true, id: 'N1021-07', status: 'delivered', message: 'ok' }];
  const env = boot(CHANNEL, { reply: function () { return answers.shift(); } });
  const flags = [];
  env.api.subscribe(function () { flags.push(env.api.info().stale); });
  await env.api.send({ card: 'x' });
  assert.deepEqual(flags, [true]);
});

test('channel.js 不用 eval / innerHTML / 外部地址', () => {
  ['eval(', 'innerHTML', 'new Function', 'http://', 'https://'].forEach(function (token) {
    const hits = SOURCE.split('\n').filter(function (line) {
      return line.indexOf(token) !== -1 && !/^\s*(\*|\/\/)/.test(line) && line.indexOf('URL_RE') === -1;
    });
    assert.deepEqual(hits, [], token);
  });
});
