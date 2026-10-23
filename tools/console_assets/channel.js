/*
 * AssistantChannel（v1.1 P3）— 指挥台页面和本地通道之间的传输层：发送回复、轮询回复状态、草稿暂存。
 *
 * 全局入口：window.AssistantChannel（Node vm 无 window 时挂到 globalThis）
 *   info()                       -> {mode: 'channel'|'file', url, limits}（不暴露令牌）
 *   canSend()                    -> 通道给的页面且没被判定过期 / 断开
 *   send({card, quick, text, lines}) -> Promise<{ok, id?, status?, message, code?, duplicate?}>，从不 reject
 *   poll(cards, onReplies)       -> 每 limits.pollSeconds 秒拉一次回复状态，回调 (replies, {recordsVersion})，返回 stop()；页面隐藏时跳过
 *   drafts.get(card) / set(card, text) / clear(card) -> 草稿存 localStorage，存不了只在内存
 *   subscribe(fn) -> unsubscribe          通道状态变了（过期 stale / 断线 offline / 恢复）就通知页面重画
 * 数据来源：页面里 id 为 console-channel 的 JSON 数据节点（console_render 写入）。
 * 安全：只请求同源的 /api/replies；令牌只放请求头，不进 URL、不进页面文字；不用 eval / innerHTML。
 */
(function (root) {
  'use strict';

  var NODE_ID = 'console-channel';
  var URL_RE = /^http:\/\/127\.0\.0\.1:\d{2,5}\/$/;
  var DEFAULT_LIMITS = { maxChars: 500, perMinute: 10, dedupeSeconds: 10, pollSeconds: 20, maxLines: 20 };
  var MESSAGES = {
    offline: '本地通道连不上，改用复制指令',
    stale: '通道重启过，刷新页面后重发',
    internal: '通道内部出错，刷新页面后重试'
  };

  var state = { mode: 'file', url: null, token: null, limits: DEFAULT_LIMITS, stale: false, offline: false, recordsVersion: null };
  var memoryDrafts = Object.create(null);
  var listeners = [];

  function readNode() {
    try {
      var doc = root.document;
      var node = doc && typeof doc.getElementById === 'function' ? doc.getElementById(NODE_ID) : null;
      return node ? JSON.parse(node.textContent || 'null') : null;
    } catch (_e) {
      return null;
    }
  }

  function load() {
    var raw = readNode();
    if (!raw || typeof raw !== 'object') {
      return;
    }
    var url = typeof raw.url === 'string' && URL_RE.test(raw.url) ? raw.url : null;
    var limits = {};
    Object.keys(DEFAULT_LIMITS).forEach(function (key) {
      var value = raw.limits && raw.limits[key];
      limits[key] = typeof value === 'number' && isFinite(value) && value > 0 ? value : DEFAULT_LIMITS[key];
    });
    state.limits = limits;
    state.url = url;
    state.recordsVersion = typeof raw.recordsVersion === 'string' && /^[0-9a-f]{8,64}$/.test(raw.recordsVersion)
      ? raw.recordsVersion : null;
    if (raw.mode === 'channel' && url && typeof raw.token === 'string' && raw.token.length >= 20) {
      state.mode = 'channel';
      state.token = raw.token;
    }
  }

  function info() {
    return { mode: state.mode, url: state.url, limits: Object.assign({}, state.limits), recordsVersion: state.recordsVersion,
      stale: state.stale, offline: state.offline };
  }

  function canSend() {
    return state.mode === 'channel' && !state.stale && !state.offline && typeof root.fetch === 'function';
  }

  function notifyState() {
    listeners.slice().forEach(function (fn) {
      try {
        fn();
      } catch (_e) {}
    });
  }

  function subscribe(fn) {
    listeners.push(fn);
    return function () {
      listeners = listeners.filter(function (other) { return other !== fn; });
    };
  }

  /** 状态变了才通知（轮询每 20 秒一次，不能每次都重画） */
  function setFlags(stale, offline) {
    var changed = state.stale !== stale || state.offline !== offline;
    state.stale = stale;
    state.offline = offline;
    if (changed) {
      notifyState();
    }
  }

  function markFailed(code) {
    if (code === 'stale') {
      setFlags(true, false);
    } else if (code === 'offline') {
      setFlags(state.stale, true);
    }
  }

  function headers(json) {
    var out = { 'X-Assistant-Token': state.token || '' };
    if (json) {
      out['Content-Type'] = 'application/json';
    }
    return out;
  }

  function failure(code, message) {
    return { ok: false, code: code, message: message || MESSAGES[code] || '没发出去' };
  }

  function send(payload) {
    if (!canSend()) {
      return Promise.resolve(failure(state.stale ? 'stale' : 'offline'));
    }
    var body = {
      card: payload && payload.card,
      quick: payload && payload.quick !== undefined ? payload.quick : null,
      text: payload && typeof payload.text === 'string' ? payload.text : '',
      lines: payload && Array.isArray(payload.lines) ? payload.lines.slice() : null
    };
    var request;
    try {
      request = root.fetch('/api/replies', {
        method: 'POST',
        headers: headers(true),
        body: JSON.stringify(body),
        credentials: 'same-origin',
        cache: 'no-store'
      });
    } catch (_e) {
      return Promise.resolve(failure('offline'));
    }
    return Promise.resolve(request).then(function (response) {
      return Promise.resolve(response.json()).then(function (data) {
        if (!data || typeof data !== 'object') {
          return failure('internal');
        }
        if (data.code === 'stale') {
          setFlags(true, false);
        } else {
          setFlags(false, false); // 通道能回话：之前判过的断线恢复
        }
        if (data.ok === true) {
          return {
            ok: true,
            id: data.id,
            status: data.status,
            message: data.message || '',
            duplicate: data.duplicate === true
          };
        }
        markFailed(typeof data.code === 'string' ? data.code : 'internal');
        return failure(typeof data.code === 'string' ? data.code : 'internal', data.message);
      }, function () {
        return failure('internal');
      });
    }, function () {
      setFlags(state.stale, true); // 连不上：页头进「本地通道没开」，回复改复制指令（AC-20）
      return failure('offline');
    });
  }

  function poll(cards, onReplies) {
    if (state.mode !== 'channel' || typeof onReplies !== 'function' || typeof root.setInterval !== 'function') {
      return function () {};
    }
    var list = Array.isArray(cards) ? cards.filter(function (c) { return typeof c === 'string' && c; }) : [];
    var query = list.length ? '?cards=' + list.map(encodeURIComponent).join(',') : '';
    var stopped = false;
    function tick() {
      var doc = root.document;
      if (stopped || state.stale || (doc && doc.hidden === true) || typeof root.fetch !== 'function') {
        return;
      }
      try {
        Promise.resolve(root.fetch('/api/replies' + query, {
          method: 'GET', headers: headers(false), credentials: 'same-origin', cache: 'no-store'
        })).then(function (response) {
          return response.json();
        }).then(function (data) {
          if (stopped || !data) {
            return;
          }
          if (data.code === 'stale') {
            setFlags(true, false); // 旧令牌：通知页面进入过期态（AC-25）
            return;
          }
          setFlags(false, false);
          if (data.ok === true && Array.isArray(data.replies)) {
            onReplies(data.replies, { recordsVersion: typeof data.recordsVersion === 'string' ? data.recordsVersion : null });
          }
        }, function () {
          setFlags(state.stale, true); // 轮询连不上：通知页面降级
        });
      } catch (_e) {}
    }
    var timer = root.setInterval(tick, state.limits.pollSeconds * 1000);
    return function () {
      stopped = true;
      if (typeof root.clearInterval === 'function') {
        root.clearInterval(timer);
      }
    };
  }

  function storage() {
    try {
      return root.localStorage || null;
    } catch (_e) {
      return null;
    }
  }

  var drafts = Object.freeze({
    get: function (card) {
      var key = 'assistant-draft:' + card;
      try {
        var store = storage();
        var value = store ? store.getItem(key) : null;
        if (typeof value === 'string') {
          return value;
        }
      } catch (_e) {}
      return Object.prototype.hasOwnProperty.call(memoryDrafts, key) ? memoryDrafts[key] : '';
    },
    set: function (card, text) {
      var key = 'assistant-draft:' + card;
      var value = typeof text === 'string' ? text : '';
      memoryDrafts[key] = value;
      try {
        var store = storage();
        if (store) {
          store.setItem(key, value);
        }
      } catch (_e) {}
    },
    clear: function (card) {
      var key = 'assistant-draft:' + card;
      delete memoryDrafts[key];
      try {
        var store = storage();
        if (store) {
          store.removeItem(key);
        }
      } catch (_e) {}
    }
  });

  load();

  var api = Object.freeze({
    info: info,
    canSend: canSend,
    send: send,
    poll: poll,
    subscribe: subscribe,
    drafts: drafts
  });
  root.AssistantChannel = api;
  if (typeof module === 'object' && module !== null && module.exports) {
    module.exports = api;
  }
}(typeof window !== 'undefined' ? window : globalThis));
