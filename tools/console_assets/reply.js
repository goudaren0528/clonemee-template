/*
 * AssistantReply（v1.1 P6b）— 指挥台四页共用的回复状态和构件。
 *
 * 全局入口：window.AssistantReply（需要先加载 core.js；channel.js 可选，没有就是本机文件模式）
 *   mode() / canSend() / stale()              通道模式、能不能发、是否过期（通道重启过）
 *   seed(snapshot) / startPolling()           快照里的回复打底；通道模式下每 20 秒拉一次回复状态
 *   records(card) / statusView(card, opts)    这张卡片的回复记录（新的在前）、回复列显示什么
 *   form(card) / setForm(card, patch)         没有 v0 选择的卡片（运行监督）的快捷评价和正文
 *   draft(card) / saveDraft(card, text)       草稿：刷新页面后还在（AC-25）
 *   send(card, payload, view) / copy(card, view, quick, text, openExport)
 *   subscribe(fn) -> unsubscribe              回复状态、本地状态、记录有更新、通道状态（过期 / 断线）变了就通知
 *   keepFocus(container, render)              重画时保住输入框的焦点和光标（打字时回复列、底栏计数照样更新）
 *   recordsChanged()                          页面之外的记录变了（页头「记录有更新 · 刷新」）
 *   cell(h, view, cls) / box(h, opts) / validationText(code)   用页面自己的 h() 造节点
 * 安全：只用 textContent / setAttribute（页面的 h）造节点；正文按 NC.validateReply / replyCommandLine 校验和中和。
 */
(function (root) {
  'use strict';

  var NC = root.AssistantConsole;
  if (!NC) {
    return;
  }

  var LOCAL_LABELS = { draft: '草稿', sending: '发送中', failed: '没发出去', copied: '已复制指令' };
  var TONES = {
    waiting: 'gray', delivered: 'blue', forwarded: 'blue', acked: 'green', handled: 'green',
    lost: 'orange', unknown: 'gray', draft: 'gray', sending: 'blue', failed: 'red', copied: 'gray', other: 'gray'
  };
  var VALIDATION = {
    empty: '选一个快捷评价或写一句',
    need_text: '这个决定要写一句原因',
    too_long: '超过 ' + NC.REPLY_LIMITS.maxChars + ' 字，删短一点再发',
    page_only: '「稍后」只在页面上标记，不发送',
    sensitive: '疑似敏感内容，没法发',
    bad_choice: '快捷评价不是这张卡片的选项',
    other_device: '这件事在另一台设备上跑，请到那台设备的面板上发',
    not_replyable: '这张卡片不接收回复'
  };

  var store = {
    seeded: false,
    byCard: Object.create(null),
    local: Object.create(null),
    forms: Object.create(null),
    listeners: [],
    inflight: Object.create(null),
    polling: false,
    pageVersion: null,
    changed: false
  };

  function isObj(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function channel() {
    var ch = root.AssistantChannel;
    return ch && typeof ch.info === 'function' ? ch : null;
  }

  function mode() {
    var ch = channel();
    return ch ? ch.info().mode : 'file';
  }

  function canSend() {
    var ch = channel();
    return !!ch && ch.canSend();
  }

  function stale() {
    var ch = channel();
    return !!ch && ch.info().mode === 'channel' && !!ch.info().stale;
  }

  /** 中途连不上（不是令牌过期）：页头「本地通道没开」，回复改复制指令（AC-20）。 */
  function offline() {
    var ch = channel();
    return !!ch && ch.info().mode === 'channel' && !!ch.info().offline && !ch.info().stale;
  }

  function notify() {
    store.listeners.slice().forEach(function (fn) {
      try {
        fn();
      } catch (_e) {}
    });
  }

  function subscribe(fn) {
    store.listeners.push(fn);
    return function () {
      store.listeners = store.listeners.filter(function (other) { return other !== fn; });
    };
  }

  function sortNewest(list) {
    return list.slice().sort(function (a, b) {
      var x = Date.parse(a.createdAt || '') || 0;
      var y = Date.parse(b.createdAt || '') || 0;
      return y - x;
    });
  }

  function index(replies) {
    var by = Object.create(null);
    (Array.isArray(replies) ? replies : []).forEach(function (reply) {
      if (isObj(reply) && typeof reply.card === 'string' && reply.card) {
        (by[reply.card] = by[reply.card] || []).push(reply);
      }
    });
    Object.keys(by).forEach(function (card) {
      by[card] = sortNewest(by[card]);
    });
    return by;
  }

  function seed(snapshot) {
    if (store.seeded) {
      return;
    }
    store.seeded = true;
    store.byCard = index(isObj(snapshot) ? snapshot.replies : []);
    var ch = channel();
    store.pageVersion = ch && ch.info().recordsVersion ? ch.info().recordsVersion : null;
  }

  function merge(replies, meta) {
    var next = index(replies);
    var before = JSON.stringify(store.byCard);
    // 发出去还没被分表收录的本地记录（极短窗口）保留到服务端出现同编号为止
    Object.keys(store.byCard).forEach(function (card) {
      store.byCard[card].forEach(function (reply) {
        if (reply._local && !(next[card] || []).some(function (r) { return r.id === reply.id; })) {
          (next[card] = next[card] || []).unshift(reply);
        }
      });
    });
    store.byCard = next;
    var changed = JSON.stringify(store.byCard) !== before;
    if (isObj(meta) && meta.recordsVersion && store.pageVersion && meta.recordsVersion !== store.pageVersion && !store.changed) {
      store.changed = true;
      changed = true;
    }
    if (changed) {
      notify();
    }
  }

  function startPolling() {
    var ch = channel();
    if (store.polling || !ch || ch.info().mode !== 'channel') {
      return;
    }
    store.polling = true;
    ch.poll([], merge);
  }

  function records(card) {
    return (store.byCard[card] || []).slice();
  }

  function form(card) {
    return Object.assign({ quick: null, text: draft(card) }, store.forms[card] || {});
  }

  function setForm(card, patch) {
    store.forms[card] = Object.assign(form(card), patch || {});
    if (Object.prototype.hasOwnProperty.call(patch || {}, 'text')) {
      saveDraft(card, store.forms[card].text);
    }
    return store.forms[card];
  }

  function draft(card) {
    var ch = channel();
    return ch && ch.drafts ? ch.drafts.get(card) : '';
  }

  function saveDraft(card, text) {
    var ch = channel();
    if (ch && ch.drafts) {
      if (typeof text === 'string' && text.trim() !== '') {
        ch.drafts.set(card, text);
      } else {
        ch.drafts.clear(card);
      }
    }
  }

  /** 回复列：本地状态（发送中 / 没发出去 / 已复制）优先，其次最新一条回复的状态，再次草稿，否则「—」。 */
  function statusView(card, opts) {
    var options = opts || {};
    var slot = options.slot;
    if (isObj(slot) && slot.allowed === false && slot.reason === 'other_device') {
      return { text: '另一台设备', tone: 'other', key: 'other' };
    }
    var local = store.local[card];
    if (local && (local.phase === 'sending' || local.phase === 'failed' || local.phase === 'copied')) {
      return { text: LOCAL_LABELS[local.phase], tone: TONES[local.phase], key: local.phase };
    }
    var latest = records(card)[0];
    if (latest) {
      var status = Object.prototype.hasOwnProperty.call(NC.REPLY_STATUSES, latest.status) ? latest.status : 'unknown';
      return { text: NC.REPLY_STATUSES[status], tone: TONES[status], key: status };
    }
    if (options.hasDraft) {
      return { text: LOCAL_LABELS.draft, tone: TONES.draft, key: 'draft' };
    }
    return { text: '—', tone: 'gray', key: 'none' };
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function setLocal(card, value) {
    store.local[card] = value;
    notify();
  }

  /** 经通道发一条；返回 Promise<结果>。成功后先在本地记一条，等轮询拿到服务端的同编号记录替换。 */
  function send(card, payload, view) {
    var ch = channel();
    if (store.inflight[card]) {
      // 同一张卡片正在发：不重复发（AC-17）。页面按钮此时也是「发送中…」不可点
      return Promise.resolve({ ok: false, code: 'sending', message: '这条正在发送，等它回来' });
    }
    if (!ch || !ch.canSend()) {
      setLocal(card, { phase: 'failed', code: stale() ? 'stale' : 'offline',
        message: stale() ? '通道重启过，刷新页面后重发' : '本地通道连不上，改用复制指令' });
      return Promise.resolve({ ok: false, code: store.local[card].code, message: store.local[card].message });
    }
    var submitted = typeof payload.text === 'string' ? payload.text : '';
    store.inflight[card] = true;
    setLocal(card, { phase: 'sending' });
    function done() {
      delete store.inflight[card];
    }
    return Promise.resolve(ch.send(payload)).then(function (result) {
      done();
      if (result && result.ok) {
        var info = view || {};
        var list = store.byCard[card] || [];
        if (!list.some(function (r) { return r.id === result.id; })) {
          list.unshift({
            id: result.id, createdAt: nowIso(), card: card, page: info.page || '', item: info.item || '',
            association: info.association || null, quick: info.quickLabel || null,
            text: typeof payload.text === 'string' ? payload.text.trim() : '',
            status: result.status || 'waiting', statusNote: null, forwardedTo: null, ack: null,
            device: { id: null, name: null, local: true }, _local: true
          });
          store.byCard[card] = list;
        }
        // 只清这次发出去的内容：等结果的时候又写了新的，留着（不能清掉没发出去的字）
        if (draft(card) === submitted) {
          saveDraft(card, '');
        }
        if (store.forms[card] && (store.forms[card].text || '') === submitted) {
          store.forms[card] = { quick: null, text: '' };
        }
        setLocal(card, { phase: 'sent', id: result.id, status: result.status, message: result.message });
      } else {
        setLocal(card, { phase: 'failed', code: result && result.code, message: (result && result.message) || '没发出去' });
      }
      return result;
    }, function (error) {
      done();
      setLocal(card, { phase: 'failed', code: 'internal', message: (error && error.message) || '没发出去' });
      return { ok: false, code: 'internal', message: '没发出去' };
    });
  }

  /** 通道没开：生成【控制台指令】回复：… 一行交给导出框（AC-20）。 */
  function copy(card, view, quick, text, openExport) {
    var line = NC.replyCommandLine(view, quick, text);
    if (!line) {
      setLocal(card, { phase: 'failed', code: 'empty', message: '这条回复生成不了指令（没选快捷评价也没写字，或内容不合格）' });
      return null;
    }
    if (typeof openExport === 'function') {
      openExport([line]);
    }
    setLocal(card, { phase: 'copied', line: line, message: '已生成指令，在下方导出框里复制，粘贴回主会话' });
    return line;
  }

  function local(card) {
    return store.local[card] || null;
  }

  function recordsChanged() {
    return store.changed;
  }

  function validationText(code) {
    return VALIDATION[code] || '';
  }

  // ------------------------------------------------------------- 构件（用页面的 h）

  function cell(h, view, cls) {
    return h('div', { 'class': (cls ? cls + ' ' : '') + 'cx-reply cx-tone-' + view.tone, title: view.text, 'data-reply-state': view.key }, [
      h('span', { 'class': 'cx-reply-dot', 'aria-hidden': 'true' }),
      view.text
    ]);
  }

  function clock(iso) {
    var d = new Date(iso || '');
    if (!isFinite(d.getTime())) {
      return '时间未记录';
    }
    var hh = String(d.getHours()).padStart(2, '0');
    var mm = String(d.getMinutes()).padStart(2, '0');
    return hh + ':' + mm;
  }

  function progress(h, reply, kind) {
    var steps = kind === 'main'
      ? [['delivered', '已送达主会话'], ['handled', '主会话已处理']]
      : [['delivered', '已送达主会话'], ['forwarded', '已转给跟进'], ['acked', '跟进已收到']];
    // 走到第几格（0 起）：等主会话 / 未知还没到第一格；已转给跟进是第 2 格；转不到停在第 2 格、第 3 格标橙
    var at = { delivered: 0, forwarded: 1, acked: 2, handled: 1, lost: 1 }[reply.status];
    at = typeof at === 'number' ? at : -1;
    var settled = reply.status === 'acked' || reply.status === 'handled' || reply.status === 'lost';
    return h('div', { 'class': 'cx-steps', 'aria-label': '进度：' + (NC.REPLY_STATUSES[reply.status] || '状态未知') },
      steps.map(function (step, i) {
        var state = i < at || (i === at && settled) ? 'done' : (i === at ? 'current' : 'todo');
        if (reply.status === 'lost' && i === at + 1) {
          state = 'lost';
        }
        return h('span', { 'class': 'cx-step cx-step-' + state }, step[1]);
      }));
  }

  function recordList(h, card, kind) {
    var list = records(card);
    if (!list.length) {
      return h('p', { 'class': 'cx-muted' }, '这张卡片还没有回复');
    }
    return h('ol', { 'class': 'cx-records' }, list.slice(0, 5).map(function (reply) {
      var said = (reply.quick ? reply.quick + (reply.text ? '：' : '') : '') + (reply.text || '');
      var nodes = [
        h('div', { 'class': 'cx-record-head' }, [
          h('span', { 'class': 'cx-record-time' }, clock(reply.createdAt)),
          h('span', { 'class': 'cx-record-said', title: said }, said || '（空）'),
          h('span', { 'class': 'cx-record-id' }, reply.id)
        ]),
        progress(h, reply, kind)
      ];
      if (reply.ack && reply.ack.text) {
        nodes.push(h('div', { 'class': 'cx-record-ack' }, '回执：' + reply.ack.text));
      }
      if (reply.status === 'lost') {
        nodes.push(h('div', { 'class': 'cx-record-lost' },
          '转不到' + (reply.statusNote ? '：' + reply.statusNote : '') + '。主会话已经告诉你，在主会话里选「重开跟进接着做 / 我自己处理」'));
      }
      return h('li', { 'class': 'cx-record cx-tone-' + (TONES[reply.status] || 'gray') }, nodes);
    }));
  }

  /**
   * 展开行里的回复框。opts：
   *   card, slot（快照里的 reply）, quick（快捷评价 id 或 null）, text, field（页面自己的输入框节点）,
   *   sendAction / copyAction（按钮 data-action）, forId（按钮 data-for）
   */
  function box(h, opts) {
    var slot = opts.slot;
    var text = typeof opts.text === 'string' ? opts.text : '';
    var wrap = h('div', { 'class': 'cx-replybox', 'data-reply-for': opts.card });
    var other = slot.allowed === false && slot.reason === 'other_device';
    wrap.appendChild(h('div', { 'class': 'cx-route' }, slot.kind === 'main'
      ? '送到主会话 → 主会话自己处理'
      : '送到主会话 → 转给 ' + slot.receiver));
    wrap.appendChild(h('div', { 'class': 'cx-assoc', title: slot.association }, '关联 ' + slot.association));
    if (other) {
      wrap.appendChild(h('p', { 'class': 'cx-warn' },
        '这件事在' + (slot.device && slot.device.name ? ' ' + slot.device.name + ' ' : '另一台设备') + '上跑，回复请到那台设备的面板上发'));
      wrap.appendChild(recordList(h, opts.card, slot.kind));
      return wrap;
    }
    if (opts.field) {
      wrap.appendChild(opts.field);
    }
    var count = Array.from(text.trim()).length;
    wrap.appendChild(h('div', { 'class': 'cx-count' + (count > NC.REPLY_LIMITS.maxChars ? ' cx-over' : ''), 'data-count-for': opts.card },
      count + ' / ' + NC.REPLY_LIMITS.maxChars));
    var check = NC.validateReply(slot, opts.quick, text);
    var state = local(opts.card);
    var sending = sendingOf(opts.card);
    var row = h('div', { 'class': 'cx-actions' });
    if (canSend()) {
      row.appendChild(h('button', {
        type: 'button', 'class': 'cx-send', 'data-action': opts.sendAction, 'data-for': opts.forId,
        disabled: !check.ok || sending
      }, sending ? '发送中…' : (state && state.phase === 'failed' ? '重试' : '发送这一条')));
    }
    if (!canSend() || (state && state.phase === 'failed')) {
      row.appendChild(h('button', {
        type: 'button', 'class': 'cx-copy', 'data-action': opts.copyAction, 'data-for': opts.forId,
        disabled: !check.ok
      }, '复制指令'));
    }
    wrap.appendChild(row);
    var hint = !check.ok ? validationText(check.code) : '';
    var message = state && state.message ? state.message : hint;
    if (stale()) {
      message = '通道重启过，刷新页面后重发（写的字刷新后还在）';
    }
    wrap.appendChild(h('p', {
      'class': 'cx-feedback' + (state && state.phase === 'failed' ? ' cx-bad' : (state && state.phase === 'sent' ? ' cx-good' : '')),
      role: 'status'
    }, message));
    if (state && state.phase === 'copied' && state.line) {
      wrap.appendChild(h('div', { 'class': 'cx-line' }, state.line));
    }
    wrap.appendChild(h('div', { 'class': 'cx-label' }, '回复记录'));
    wrap.appendChild(recordList(h, opts.card, slot.kind));
    return wrap;
  }

  function sendingOf(card) {
    return !!store.inflight[card] || !!(store.local[card] && store.local[card].phase === 'sending');
  }

  /** 输入时不重绘整页：只更新这个回复框的字数和按钮可用状态（发送中保持不可点）。 */
  function refresh(container, card, slot, quick, text) {
    if (!container || typeof container.querySelectorAll !== 'function') {
      return;
    }
    var check = NC.validateReply(slot, quick, text);
    if (sendingOf(card)) {
      check = { ok: false, code: 'sending' };
    }
    var count = Array.from((text || '').trim()).length;
    var boxes = container.querySelectorAll('[data-reply-for]');
    for (var i = 0; i < boxes.length; i += 1) {
      if (boxes[i].getAttribute('data-reply-for') !== card) {
        continue;
      }
      var counters = boxes[i].querySelectorAll('[data-count-for]');
      for (var j = 0; j < counters.length; j += 1) {
        counters[j].textContent = count + ' / ' + NC.REPLY_LIMITS.maxChars;
        counters[j].setAttribute('class', 'cx-count' + (count > NC.REPLY_LIMITS.maxChars ? ' cx-over' : ''));
      }
      var buttons = boxes[i].querySelectorAll('.cx-send, .cx-copy');
      for (var k = 0; k < buttons.length; k += 1) {
        var disable = !check.ok || (sendingOf(card) && (buttons[k].getAttribute('class') || '').indexOf('cx-send') !== -1);
        if (disable) {
          buttons[k].setAttribute('disabled', '');
        } else {
          buttons[k].removeAttribute('disabled');
        }
      }
      var feedback = boxes[i].querySelectorAll('.cx-feedback');
      if (feedback.length && !(store.local[card] && store.local[card].message)) {
        feedback[0].textContent = check.ok ? '' : validationText(check.code);
      }
    }
  }

  /** 重画时保住输入框的焦点和光标：打字时回复列、底栏计数、通道状态照样更新（AC-18、AC-14）。 */
  function keepFocus(container, render) {
    var doc = container && (container.ownerDocument || root.document);
    function inside(node) {
      var current = node && node.parentNode;
      while (current) {
        if (current === container) {
          return true;
        }
        current = current.parentNode;
      }
      return false;
    }
    function activeField() {
      var active = doc && doc.activeElement;
      return active && active.tagName === 'TEXTAREA' && inside(active) ? active : null;
    }
    function refocus(field, start, end) {
      var key = field.getAttribute('data-for');
      if (key === null || typeof container.querySelectorAll !== 'function') {
        return;
      }
      var list = container.querySelectorAll('textarea');
      for (var i = 0; i < list.length; i += 1) {
        if (list[i].getAttribute('data-for') !== key) {
          continue;
        }
        try {
          if (typeof list[i].focus === 'function') {
            list[i].focus();
          }
          if (typeof list[i].setSelectionRange === 'function') {
            list[i].setSelectionRange(start, end);
          }
        } catch (_e) {}
        return;
      }
    }
    return {
      request: function () {
        var field = activeField();
        if (!field) {
          render();
          return;
        }
        // 重画前先记下光标：节点摘下来之后 selectionStart 不一定还在
        var start = field.selectionStart;
        var end = field.selectionEnd;
        render();
        refocus(field, start, end);
      },
      blur: function () {}
    };
  }

  var api = Object.freeze({
    mode: mode,
    canSend: canSend,
    stale: stale,
    offline: offline,
    keepFocus: keepFocus,
    seed: seed,
    startPolling: startPolling,
    records: records,
    statusView: statusView,
    form: form,
    setForm: setForm,
    draft: draft,
    saveDraft: saveDraft,
    send: send,
    copy: copy,
    local: local,
    subscribe: subscribe,
    recordsChanged: recordsChanged,
    validationText: validationText,
    cell: cell,
    box: box,
    refresh: refresh
  });
  root.AssistantReply = api;
  // 通道状态（过期 / 断线 / 恢复）变了也通知页面重画
  var ch = channel();
  if (ch && typeof ch.subscribe === 'function') {
    ch.subscribe(notify);
  }
  if (typeof module === 'object' && module !== null && module.exports) {
    module.exports = api;
  }
}(typeof window !== 'undefined' ? window : globalThis));
