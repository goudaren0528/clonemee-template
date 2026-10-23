/*
 * AssistantConsole actions (P3，v1.1 P6a 改成表格) — 待处理「需要你出手」与派发确认「副本大厅」两页。
 * v1.1 P6a：一条一行的表格 + 展开行（同时最多展开一行），关键字段前置；导出逻辑与 v0 相同。
 *
 * 依赖：core.js（P0）先加载，提供 safeText / exportLines / defaultsFor / registerPage。
 * 注册：AssistantConsole.registerPage('pending' | 'dispatch', {mount})
 *   mount(container, snapshot, ui) -> {dispose()}
 *   - snapshot 只读：不写回、不排序原数组。
 *   - 选择只存 ui.selection.pending / ui.selection.dispatch（P0 冻结形状），切页保留。
 *   - 导出只经 ui.openExport(lines)；空数组由壳层给提示。
 *   - 形象只经 ui.avatar(roleId)。
 * 安全：所有动态文字走 textContent（createTextNode），不拼 HTML、不设 on* 属性；
 *       展示前再过一遍 safeText；体验链接只接受 loopback http(s) 与本地 file:///，点击才导航。
 */
(function (root) {
  'use strict';

  var NC = root.AssistantConsole;
  if (!NC || typeof NC.registerPage !== 'function') {
    throw new Error('actions.js 需要先加载 core.js');
  }

  var TYPES = NC.PENDING_TYPES;
  var STATUSES = NC.PENDING_STATUSES;
  var LANES = [
    { key: 'decision', label: NC.PENDING_COLUMNS.decision, tone: 'red', priority: true },
    { key: 'blocked', label: NC.PENDING_COLUMNS.blocked, tone: 'orange', priority: false },
    { key: 'accept', label: NC.PENDING_COLUMNS.accept, tone: 'blue', priority: false }
  ];
  // PRD 4.4：「派谁去」可选的五个角色（跟进只在团队副本里出现）。
  var PICKABLE_ROLES = ['product', 'dev', 'accept', 'research', 'investigate'];
  // PRD 4.4：团队副本固定三人小队，不能改。
  var TEAM = [
    { roleId: 'dev', executor: 'opencode' },
    { roleId: 'accept', executor: 'codex' },
    { roleId: 'pm', executor: 'claude' }
  ];
  var UNKNOWN = '—';
  // PRD 4.7：单任务按角色固定关卡；其余角色（产品、验收等）只用记录里的明确步骤。
  var ROLE_STAGES = {
    investigate: ['复现', '定位根因', '出结论'],
    research: ['收集', '整理', '出报告'],
    dev: ['实现', '自测', '提交']
  };

  // ------------------------------------------------------------ 小工具

  function isObj(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function own(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function idOf(value) {
    if (typeof value === 'string' && value.trim() !== '') {
      return value.trim();
    }
    return null;
  }

  /** 展示用文本：再过一遍 safeText，命中或为空时用占位。 */
  function show(value, fallback) {
    var text = NC.safeText(value);
    return text === null ? fallback : text;
  }

  function dayOf(iso) {
    return typeof iso === 'string' && /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(0, 10) : null;
  }

  /** ISO 已带本地 offset：直接取字面时分，不做时区换算。 */
  function clockOf(iso, localDate) {
    if (typeof iso !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(iso)) {
      return null;
    }
    var hm = iso.slice(11, 16);
    return iso.slice(0, 10) === localDate ? hm : iso.slice(5, 10) + ' ' + hm;
  }

  /** 相对生成时间（页面不自动刷新，所以不用当前时钟）。 */
  function agoOf(iso, generatedAt) {
    var then = typeof iso === 'string' ? Date.parse(iso) : NaN;
    var now = typeof generatedAt === 'string' ? Date.parse(generatedAt) : NaN;
    if (!isFinite(then) || !isFinite(now)) {
      return '时间未记录';
    }
    var minutes = Math.floor((now - then) / 60000);
    if (minutes < 0) {
      return '时间晚于生成时间';
    }
    if (minutes < 1) {
      return '刚刚';
    }
    if (minutes < 60) {
      return minutes + ' 分钟前';
    }
    var hours = Math.floor(minutes / 60);
    if (hours < 24) {
      return hours + ' 小时前';
    }
    return Math.floor(hours / 24) + ' 天前';
  }

  var LOOPBACK_URL_RE = /^https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?(?:[\/?#]|$)/i;
  var LOCAL_FILE_URL_RE = /^file:\/\/\/(?:[A-Za-z]:\/|(?!\/))/i;

  /** P1 已校验过体验链接；这里只做一次纵深检查，拿不准就不给可点击链接。 */
  function safeUrl(raw) {
    if (typeof raw !== 'string' || raw === '' || /[\s\u0000-\u001F\u007F]/.test(raw)) {
      return null;
    }
    if (!LOOPBACK_URL_RE.test(raw) && !LOCAL_FILE_URL_RE.test(raw)) {
      return null;
    }
    return NC.safeText(raw, { limit: 2000 }) === raw ? raw : null;
  }

  function isAbsolutePath(value) {
    if (typeof value !== 'string' || value === '' || /[\u0000-\u001F\u007F]/.test(value)) {
      return false;
    }
    if (value.indexOf('\\\\') === 0 || value.indexOf('//') === 0) {
      return false;
    }
    if (value.split(/[\\/]+/).indexOf('..') !== -1) {
      return false;
    }
    return /^[A-Za-z]:[\\/]/.test(value) || value.charAt(0) === '/';
  }

  function ensureBucket(ui, key) {
    if (!isObj(ui.selection)) {
      ui.selection = { pending: {}, dispatch: {}, roles: {} };
    }
    if (!isObj(ui.selection[key])) {
      ui.selection[key] = {};
    }
    return ui.selection[key];
  }

  function hasDiagnostic(snapshot, code) {
    var list = Array.isArray(snapshot.diagnostics) ? snapshot.diagnostics : [];
    return list.some(function (diagnostic) {
      return isObj(diagnostic) && diagnostic.code === code;
    });
  }

  function diagnosticsWithPrefix(snapshot, prefix) {
    var list = Array.isArray(snapshot.diagnostics) ? snapshot.diagnostics : [];
    return list.filter(function (diagnostic) {
      return isObj(diagnostic) && typeof diagnostic.code === 'string' && diagnostic.code.indexOf(prefix) === 0;
    });
  }

  /** DOM 工厂：只 setAttribute + 文本节点，拒绝 on* 属性。 */
  function builder(doc) {
    function append(element, children) {
      if (children === null || children === undefined || children === false) {
        return;
      }
      var list = Array.isArray(children) ? children : [children];
      list.forEach(function (child) {
        if (child === null || child === undefined || child === false) {
          return;
        }
        element.appendChild(typeof child === 'object' ? child : doc.createTextNode(String(child)));
      });
    }
    function h(tag, attributes, children) {
      var element = doc.createElement(tag);
      if (attributes) {
        Object.keys(attributes).forEach(function (name) {
          var value = attributes[name];
          if (value === null || value === undefined || value === false || /^on/i.test(name)) {
            return;
          }
          element.setAttribute(name, value === true ? '' : String(value));
        });
      }
      append(element, children);
      return element;
    }
    return h;
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function actionTarget(target, container) {
    var node = target;
    while (node && node !== container) {
      if (node.nodeType === 1 && node.getAttribute('data-action')) {
        return node;
      }
      node = node.parentNode;
    }
    return null;
  }

  function avatarOf(ui, h, roleId) {
    if (ui && typeof ui.avatar === 'function') {
      var node = ui.avatar(roleId === undefined ? null : roleId);
      if (node && typeof node === 'object') {
        return node;
      }
    }
    return h('span', { 'class': 'act-avatar-empty', 'aria-hidden': 'true' });
  }

  function progressBar(h, numerator, denominator) {
    var fill = h('span', { 'class': 'act-bar-fill' });
    var percent = denominator > 0 ? Math.min(100, Math.round((numerator / denominator) * 100)) : 0;
    fill.style.width = percent + '%';
    return h('div', { 'class': 'act-bar', 'aria-hidden': 'true' }, fill);
  }

  function chip(h, attributes, pressed, children) {
    var attrs = Object.assign({ type: 'button', 'class': 'act-chip', 'aria-pressed': pressed ? 'true' : 'false' }, attributes);
    return h('button', attrs, children);
  }

  /** 生成时间到某个时刻过了多少分钟；算不出（缺时间、晚于生成时间）返回 null。 */
  function minutesSince(iso, generatedAt) {
    var then = typeof iso === 'string' ? Date.parse(iso) : NaN;
    var now = typeof generatedAt === 'string' ? Date.parse(generatedAt) : NaN;
    if (!isFinite(then) || !isFinite(now) || now < then) {
      return null;
    }
    return Math.floor((now - then) / 60000);
  }

  /** 回复状态列：P6a 先显示「—」，回复界面（P6b）接通后显示最新一条回复的状态。 */
  function replyCell(h) {
    return h('div', { 'class': 'act-c-reply act-reply', title: '还没有回复' }, [
      h('span', { 'class': 'act-reply-dot', 'aria-hidden': 'true' }),
      '—'
    ]);
  }

  function expandButton(h, action, id, open) {
    return h('button', {
      type: 'button',
      'class': 'act-more',
      'data-action': action,
      'data-for': id,
      'aria-expanded': open ? 'true' : 'false',
      'aria-label': open ? '收起详情' : '展开详情',
      title: open ? '收起详情' : '展开详情'
    }, '›');
  }

  /** 把光标放进某一行的输入框（按 data-for 比较，不拼选择器）。 */
  function focusNote(container, id) {
    if (id === null || typeof container.querySelectorAll !== 'function') {
      return;
    }
    try {
      var list = container.querySelectorAll('textarea');
      for (var i = 0; i < list.length; i += 1) {
        if (list[i].getAttribute('data-for') === id && typeof list[i].focus === 'function') {
          list[i].focus();
          return;
        }
      }
    } catch (_e) {}
  }

  function mountWith(container, render, handlers) {
    var disposed = false;
    function guard(fn) {
      return function (event) {
        if (!disposed) {
          fn(event);
        }
      };
    }
    var onClick = guard(handlers.click);
    var onInput = guard(handlers.input || function () {});
    var onBlur = guard(handlers.blur || function () {});
    container.addEventListener('click', onClick);
    container.addEventListener('input', onInput);
    container.addEventListener('change', onInput);
    container.addEventListener('focusout', onBlur);
    var unsubscribe = typeof handlers.subscribe === 'function' ? handlers.subscribe(guard(function () {
      (handlers.update || render)();
    })) : null;
    render();
    return {
      dispose: function () {
        if (disposed) {
          return;
        }
        disposed = true;
        if (typeof unsubscribe === 'function') {
          unsubscribe();
        }
        container.removeEventListener('click', onClick);
        container.removeEventListener('input', onInput);
        container.removeEventListener('change', onInput);
        container.removeEventListener('focusout', onBlur);
        clear(container);
      }
    };
  }

  function openExport(ui, lines) {
    if (ui && typeof ui.openExport === 'function') {
      ui.openExport(lines);
    }
  }

  // ---- v1.1 P6b 回复（reply.js 没加载时一切照 v0）

  function reply() {
    var R = root.AssistantReply;
    return R && typeof R.box === 'function' ? R : null;
  }

  /** 通道给的页面（含过期）：卡住改用回复的快捷评价、底部改「发送已选」。本机文件打开照 v0。 */
  function channelOn() {
    var R = reply();
    return !!R && R.mode() === 'channel';
  }

  function replySlot(item) {
    return reply() && isObj(item) && isObj(item.reply) && typeof item.reply.card === 'string' ? item.reply : null;
  }

  /** 这张卡片发过回复（回复列有状态）：今日关卡算已处理（PRD 4.4「发出后更新」）。 */
  function replied(item) {
    var slot = replySlot(item);
    return !!slot && reply().records(slot.card).length > 0;
  }

  /** 重画时保住输入框焦点（reply.js 的 keepFocus；没加载时直接重画）。 */
  function deferredRender(container, render) {
    var R = reply();
    if (R && typeof R.keepFocus === 'function') {
      return R.keepFocus(container, render);
    }
    return { request: render, blur: function () {} };
  }

  // ========================================================= 待处理

  function isDecidable(item) {
    return item.status === STATUSES.pending &&
      (item.type === TYPES.done || item.type === TYPES.decision || item.type === TYPES.blocked);
  }

  /** 这张卡可点的决定：待验收固定三项（PRD 4.3），其余只取记录里的选项；通道页面上卡住用回复的三项（PRD v1.1 4.2）。 */
  function choicesOf(item) {
    if (item.type === TYPES.blocked && channelOn() && replySlot(item)) {
      return NC.replyChoices(item.reply);
    }
    if (item.type === TYPES.done) {
      return Object.keys(NC.ACCEPT_CHOICES).map(function (id) {
        return { id: id, label: NC.ACCEPT_CHOICES[id] };
      });
    }
    var list = Array.isArray(item.choices) ? item.choices : [];
    return list.filter(function (choice) {
      return isObj(choice) && idOf(choice.id) !== null && NC.safeText(choice.label) !== null;
    }).map(function (choice) {
      return { id: choice.id, label: NC.safeText(choice.label) };
    });
  }

  function validPick(item, pick) {
    if (!isObj(pick) || typeof pick.choiceId !== 'string' || !isDecidable(item)) {
      return false;
    }
    return choicesOf(item).some(function (choice) {
      return choice.id === pick.choiceId;
    });
  }

  function uniqueItems(list) {
    var seen = Object.create(null);
    var out = [];
    (Array.isArray(list) ? list : []).forEach(function (item) {
      if (!isObj(item)) {
        return;
      }
      var id = idOf(item.id);
      if (id === null || seen[id]) {
        return;
      }
      seen[id] = true;
      out.push(item);
    });
    return out;
  }

  /**
   * 今日关卡（impl-plan 5）：分母 = 仍未处理（当日或跨日）+ 今天明确处理过的；
   * 分子 = 今天已处理 + 当前选了有效决定的未处理卡片。交接计入总数但不能人工决定。
   */
  function gateOf(items, picks, localDate) {
    var total = 0;
    var done = 0;
    var undated = 0;
    items.forEach(function (item) {
      if (item.status === STATUSES.pending) {
        total += 1;
        if (validPick(item, picks[item.id]) || replied(item)) {
          done += 1;
        }
      } else if (item.status === STATUSES.processed) {
        var day = dayOf(item.processedAt);
        if (day === null || typeof localDate !== 'string') {
          undated += 1;
        } else if (day === localDate) {
          total += 1;
          done += 1;
        }
      }
    });
    return { total: total, done: done, undated: undated };
  }

  function mountPending(container, snapshot, ui) {
    var doc = container.ownerDocument || root.document;
    var h = builder(doc);
    var snap = isObj(snapshot) ? snapshot : {};
    var picks = ensureBucket(ui, 'pending');
    var items = uniqueItems(snap.pending);
    var byId = Object.create(null);
    items.forEach(function (item) {
      byId[item.id] = item;
    });
    var countMetric = isObj(snap.stats) && isObj(snap.stats.pendingCount) ? snap.stats.pendingCount : null;
    var known = Array.isArray(snap.pending) && !(countMetric && countMetric.value === null) &&
      !hasDiagnostic(snap, 'pending_source_missing');
    var localDate = typeof snap.localDate === 'string' ? snap.localDate : null;
    var noticeEl = null;
    var R = reply();
    var batch = null; // 「发送已选」：{phase: 'sending'|'done', total, ok, failed}
    // 草稿刷新后还在（AC-25）：没写过补一句的卡片从草稿恢复
    if (R) {
      items.forEach(function (item) {
        var slot = replySlot(item);
        var saved = slot && isDecidable(item) ? R.draft(slot.card) : '';
        if (saved && !(isObj(picks[item.id]) && typeof picks[item.id].note === 'string')) {
          picks[item.id] = Object.assign(isObj(picks[item.id]) ? picks[item.id] : {}, { note: saved });
        }
      });
    }

    function subjectOf(item) {
      return show(item.project, '项目未记录') + '：' + show(item.item, '事项未记录');
    }

    /** 回复用的快捷评价：选中的决定是这张卡片的回复选项才算（本机文件里的卡住建议项不算）。 */
    function quickOf(item, pick) {
      var slot = replySlot(item);
      if (!slot || !isObj(pick) || typeof pick.choiceId !== 'string') {
        return null;
      }
      return NC.replyChoices(slot).some(function (choice) { return choice.id === pick.choiceId; }) ? pick.choiceId : null;
    }

    function noteOf(pick) {
      return isObj(pick) && typeof pick.note === 'string' ? pick.note : '';
    }

    function replyView(item, quick) {
      var slot = item.reply;
      var label = null;
      NC.replyChoices(slot).forEach(function (choice) {
        if (choice.id === quick) {
          label = choice.label;
        }
      });
      return { page: 'pending', item: subjectOf(item), association: slot.association, choices: NC.replyChoices(slot), quickLabel: label };
    }

    /** 要写字的决定：打回（v0 起就是）；通道页面上再加回复规定必填的（换个办法）。 */
    function needsText(item, choiceId) {
      if (item.type === TYPES.done && choiceId === 'reject') {
        return true;
      }
      var slot = replySlot(item);
      return channelOn() && !!slot && Array.isArray(slot.needText) && slot.needText.indexOf(choiceId) !== -1;
    }

    function sendOne(item) {
      var slot = item.reply;
      var pick = picks[item.id];
      var quick = quickOf(item, pick);
      var text = noteOf(pick);
      var choiceId = isObj(pick) ? pick.choiceId : undefined;
      return R.send(slot.card, { card: slot.card, quick: quick, text: text }, replyView(item, quick)).then(function (result) {
        // 只清这次发出去的决定和字：等结果的时候改了决定或又写了字，留着（和 reply.js 的草稿规则一致）
        var now = picks[item.id];
        if (result && result.ok && (!isObj(now) || (now.choiceId === choiceId && noteOf(now) === text))) {
          delete picks[item.id];
        }
        return result;
      });
    }

    /** 通道开着时底部能发的：点了决定、校验过（「稍后」只在页面上标记，不发）。 */
    function sendable(item) {
      var slot = replySlot(item);
      var pick = picks[item.id];
      return !!slot && validPick(item, pick) &&
        NC.validateReply(slot, quickOf(item, pick), noteOf(pick)).ok;
    }

    function experienceBlock(item) {
      var exp = item.experience;
      if (!isObj(exp) || exp.ui === 'unknown' || (exp.ui !== 'yes' && exp.ui !== 'no')) {
        return h('div', { 'class': 'act-try act-try-muted' }, '体验信息未记录：是否需要体验环境未知');
      }
      if (exp.ui === 'no') {
        return h('div', { 'class': 'act-try act-try-muted' }, '没有界面，不需要体验环境');
      }
      var block = h('div', { 'class': 'act-try' });
      var recorded = clockOf(exp.recordedAt, localDate);
      var expires = clockOf(exp.expiresAt, localDate);
      var url = safeUrl(exp.url);
      var canOpen = false;
      if (exp.state === 'recorded-started') {
        block.appendChild(h('div', { 'class': 'act-try-row' }, [
          h('span', { 'class': 'act-live' }, '体验环境已启动'),
          h('span', { 'class': 'act-muted' },
            '（记录于 ' + (recorded || '时间未记录') + (expires ? '，' + expires + ' 到期' : '') + '，生成后状态可能变化）')
        ]));
        canOpen = true;
      } else if (exp.state === 'static') {
        block.appendChild(h('div', { 'class': 'act-try-row' },
          h('span', { 'class': 'act-muted' }, '静态页面，直接打开，不需要启动体验环境')));
        canOpen = true;
      } else if (exp.state === 'expired') {
        block.appendChild(h('div', { 'class': 'act-try-row' },
          h('span', { 'class': 'act-warn' },
            '体验环境记录已过期（记录于 ' + (recorded || '时间未记录') + (expires ? '，' + expires + ' 到期' : '') + '），链接可能打不开')));
      } else {
        block.appendChild(h('div', { 'class': 'act-try-row' },
          h('span', { 'class': 'act-muted' }, '体验环境状态未记录')));
      }

      var tests = exp.tests;
      if (isObj(tests) && typeof tests.passed === 'number' && typeof tests.total === 'number') {
        var summary = show(tests.summary, null);
        block.appendChild(h('div', { 'class': 'act-small' },
          '用例通过 ' + tests.passed + ' / ' + tests.total + (summary ? ' · ' + summary : '')));
      } else {
        block.appendChild(h('div', { 'class': 'act-small act-muted' }, '用例通过情况未记录'));
      }

      if (canOpen && url) {
        block.appendChild(h('div', { 'class': 'act-try-row' }, [
          h('a', { 'class': 'act-open', href: url, target: '_blank', rel: 'noopener noreferrer' }, '打开体验'),
          h('span', { 'class': 'act-path' }, url)
        ]));
      } else if (canOpen) {
        block.appendChild(h('div', { 'class': 'act-small act-muted' }, '体验链接未记录或未通过安全校验，不提供打开按钮'));
      }

      var focus = (Array.isArray(exp.focus) ? exp.focus : []).map(function (entry) {
        return show(entry, null);
      }).filter(Boolean);
      if (focus.length) {
        block.appendChild(h('div', { 'class': 'act-label' }, '建议你重点体验'));
        block.appendChild(h('ol', { 'class': 'act-focus' }, focus.map(function (entry) {
          return h('li', null, entry);
        })));
      } else {
        block.appendChild(h('div', { 'class': 'act-small act-muted' }, '重点体验未记录'));
      }

      if (exp.state === 'recorded-started') {
        block.appendChild(h('div', { 'class': 'act-small act-muted' },
          '你点验收通过或打回后，体验环境会自动关掉（由主会话收到指令后执行，这里点选不会关）'));
      }
      return block;
    }

    var openId = null; // 同时最多展开一行（PRD v1.1 4.3）
    var focusAfter = null; // 要写字的决定点了之后，把光标放进这一行的输入框（AC-41）

    /** 待验收行的类型标签：有体验环境的写「可体验」。 */
    function typeLabel(item) {
      if (item.type === TYPES.done) {
        var exp = item.experience;
        var tryable = isObj(exp) && exp.ui === 'yes' && (exp.state === 'recorded-started' || exp.state === 'static');
        return tryable ? '可体验' : '待验收';
      }
      return show(item.type, '类型未记录');
    }

    function pendingRow(item, lane) {
      var pick = picks[item.id];
      var chosen = validPick(item, pick);
      var open = openId === item.id;
      var wrap = h('article', {
        'class': 'act-item act-tone-' + lane.tone + (chosen ? ' is-chosen' : '') + (open ? ' is-open' : ''),
        'data-item-id': item.id,
        'data-selected': chosen ? 'true' : null
      });

      var project = show(item.project, '项目未记录');
      var subject = show(item.item, '事项未记录');
      var summary = show(item.summary, '说明未记录');
      var choices = choicesOf(item);
      var row = h('div', {
        'class': 'act-row act-row-pending',
        'data-action': 'pending-expand',
        'data-for': item.id
      });
      row.appendChild(h('div', { 'class': 'act-c-type' },
        h('span', { 'class': 'act-tag act-tag-' + lane.tone }, typeLabel(item))));
      row.appendChild(h('div', { 'class': 'act-c-main', title: project + '：' + subject + ' · ' + summary }, [
        h('span', { 'class': 'act-source' }, project + '：'),
        h('strong', { 'class': 'act-subject' }, subject),
        h('span', { 'class': 'act-summary' }, summary)
      ]));
      row.appendChild(h('div', { 'class': 'act-c-who act-who' }, [
        avatarOf(ui, h, item.roleId),
        h('span', { 'class': 'act-who-name' }, show(item.senderName, '来源角色未记录'))
      ]));
      var waited = minutesSince(item.createdAt, snap.generatedAt);
      row.appendChild(h('div', {
        'class': 'act-c-age act-age' + (waited !== null && waited > 30 ? ' act-age-stale' : '')
      }, agoOf(item.createdAt, snap.generatedAt)));
      if (choices.length) {
        row.appendChild(h('div', { 'class': 'act-c-choice act-chips', role: 'group', 'aria-label': '你的决定' },
          choices.map(function (choice) {
            var on = chosen && pick.choiceId === choice.id;
            return chip(h, {
              'class': 'act-chip' + (on ? ' is-selected' : ''),
              'data-action': 'pending-choice',
              'data-for': item.id,
              'data-choice-id': choice.id,
              title: choice.label
            }, on, choice.label);
          })));
      } else {
        row.appendChild(h('div', { 'class': 'act-c-choice act-warn' }, '没有可选的决定'));
      }
      var slot = replySlot(item);
      row.appendChild(slot
        ? R.cell(h, R.statusView(slot.card, { slot: slot, hasDraft: noteOf(pick).trim() !== '' }), 'act-c-reply act-reply')
        : replyCell(h));
      row.appendChild(expandButton(h, 'pending-expand', item.id, open));
      wrap.appendChild(row);

      // 展开行：完整说明、体验区、详情路径、补一句（收起时 hidden，内容和选择状态都在）
      var panel = h('div', { 'class': 'act-x cx-expand', hidden: !open });
      // 行内被截断的字段在这里给全文（AC-40）：项目：事项、标题、一句话
      var left = h('div', { 'class': 'act-x-main' }, [
        h('div', { 'class': 'act-x-item' }, project + '：' + subject),
        h('div', { 'class': 'act-name' }, show(item.title, '标题未记录')),
        h('p', { 'class': 'act-summary-full' }, summary)
      ]);
      if (item.type === TYPES.done) {
        left.appendChild(experienceBlock(item));
      }
      (Array.isArray(item.diagnostics) ? item.diagnostics : []).forEach(function (diagnostic) {
        var message = isObj(diagnostic) ? show(diagnostic.message, null) : null;
        if (message) {
          left.appendChild(h('p', { 'class': 'act-diag' }, message));
        }
      });
      if (!choices.length) {
        left.appendChild(h('p', { 'class': 'act-diag' }, '记录里没有可选的决定（记录不完整），请看详情后在主会话处理'));
      }
      left.appendChild(h('div', { 'class': 'act-label' }, '详情'));
      left.appendChild(isAbsolutePath(item.detailPath) && NC.safeText(item.detailPath) !== null
        ? h('div', { 'class': 'act-path' }, item.detailPath)
        : h('div', { 'class': 'act-muted act-small' }, '详情路径未记录或已屏蔽'));
      panel.appendChild(left);

      if (slot && isDecidable(item)) {
        // 回复框（PRD v1.1 4.4 第 10 项）：输入框不设上限，超 500 字数变红、发不出去
        var quick = quickOf(item, pick);
        var must = quick !== null && needsText(item, quick);
        var field = h('textarea', {
          'class': 'act-note cx-field',
          rows: '3',
          placeholder: must ? '写一句原因（必填）' : '补一句（可不填）',
          'aria-label': must ? '原因' : '补一句',
          'data-action': 'pending-note',
          'data-for': item.id
        });
        field.value = noteOf(pick);
        panel.appendChild(h('div', { 'class': 'act-x-side' }, [
          h('div', { 'class': 'act-label' }, must ? '原因（必填）' : '补一句'),
          R.box(h, {
            card: slot.card, slot: slot, quick: quick, text: noteOf(pick), field: field,
            sendAction: 'pending-reply-send', copyAction: 'pending-reply-copy', forId: item.id
          })
        ]));
      } else if (choices.length) {
        var note = h('textarea', {
          'class': 'act-note',
          rows: '3',
          maxlength: String(NC.TEXT_LIMIT),
          placeholder: '补一句（可不填）',
          'aria-label': '补一句',
          'data-action': 'pending-note',
          'data-for': item.id
        });
        if (isObj(pick) && typeof pick.note === 'string') {
          note.value = pick.note;
        }
        panel.appendChild(h('div', { 'class': 'act-x-side' }, [
          h('div', { 'class': 'act-label' }, '补一句'),
          note,
          h('div', { 'class': 'act-small act-muted' }, '会跟着这一条的决定一起生成指令')
        ]));
      }
      wrap.appendChild(panel);
      return wrap;
    }

    function lane(laneDef, rows) {
      var count = known ? String(rows.length) : UNKNOWN;
      var section = h('section', { 'class': 'act-group act-tone-' + laneDef.tone, 'data-lane': laneDef.key });
      section.appendChild(h('div', { 'class': 'act-grp' }, [
        h('span', { 'class': 'act-dot', 'aria-hidden': 'true' }),
        h('h3', { 'class': 'act-grp-name' }, laneDef.label),
        h('span', { 'class': 'act-count' }, count),
        laneDef.priority ? h('span', { 'class': 'act-flag' }, '优先') : null
      ]));
      if (rows.length) {
        // 组内等得久的在前（只改展示顺序，导出仍按记录顺序）
        rows.slice().sort(function (a, b) {
          var x = typeof a.createdAt === 'string' ? Date.parse(a.createdAt) : NaN;
          var y = typeof b.createdAt === 'string' ? Date.parse(b.createdAt) : NaN;
          if (!isFinite(x) || !isFinite(y)) {
            return isFinite(x) ? -1 : (isFinite(y) ? 1 : 0);
          }
          return x - y;
        }).forEach(function (item) {
          section.appendChild(pendingRow(item, laneDef));
        });
      } else {
        section.appendChild(h('p', { 'class': 'act-empty' }, known ? '这一组没有待处理' : '待处理清单读不到，条数未知'));
      }
      return section;
    }

    function render() {
      clear(container);
      var page = h('div', { 'class': 'act act-pending' });
      var gate = gateOf(items, picks, localDate);

      var goal = h('div', { 'class': 'act-goal' });
      goal.appendChild(h('span', { 'class': 'act-goal-label' }, '今日关卡'));
      if (known) {
        goal.appendChild(h('span', { 'class': 'act-goal-num' }, '已处理 ' + gate.done + ' / ' + gate.total));
        goal.appendChild(progressBar(h, gate.done, gate.total));
      } else {
        goal.appendChild(h('span', { 'class': 'act-goal-num' }, UNKNOWN));
      }

      page.appendChild(h('div', { 'class': 'act-head' }, [
        h('h2', null, '需要你出手'),
        h('p', { 'class': 'act-sub' }, channelOn()
          ? '点一下决定，可以展开补一句，最后「发送已选」直接送到主会话'
          : '点一下决定，可以展开补一句，最后生成指令复制回主会话'),
        goal
      ]));
      if (known && gate.undated) {
        page.appendChild(h('p', { 'class': 'act-diag' }, gate.undated + ' 条已处理记录缺少处理时间，今日关卡可能不完整'));
      }
      if (!known) {
        page.appendChild(h('p', { 'class': 'act-diag' }, show(countMetric && countMetric.reason, '待处理清单缺失，进度未知')));
      }

      diagnosticsWithPrefix(snap, 'pending_').forEach(function (diagnostic) {
        page.appendChild(h('p', { 'class': 'act-diag' }, show(diagnostic.message, '待处理清单有异常，按未知展示')));
      });

      var open = items.filter(function (item) {
        return item.status === STATUSES.pending;
      });
      if (known && !open.length) {
        page.appendChild(h('p', { 'class': 'act-empty' }, '目前没有需要你出手的事'));
      }

      var table = h('div', { 'class': 'act-table act-table-pending' });
      table.appendChild(h('div', { 'class': 'act-th act-row-pending', 'aria-hidden': 'true' }, [
        h('span', null, '类型'), h('span', null, '事项 · 一句话'), h('span', null, '谁报的'),
        h('span', { 'class': 'act-num' }, '等了'), h('span', null, '你的决定'), h('span', null, '回复'), h('span', null, '')
      ]));
      LANES.forEach(function (laneDef) {
        table.appendChild(lane(laneDef, open.filter(function (item) {
          return item.type !== TYPES.handoff && item.column === laneDef.label;
        })));
      });

      var autoDone = items.filter(function (item) {
        return item.status === STATUSES.processed && item.type === TYPES.handoff;
      });
      var folded = h('details', { 'class': 'act-auto' });
      folded.appendChild(h('summary', null, [
        h('span', { 'class': 'act-dot', 'aria-hidden': 'true' }),
        '已自动处理 · ' + autoDone.length,
        autoDone.length ? h('span', { 'class': 'act-muted' }, ' · ' + show(autoDone[0].title, '标题未记录') +
          ' → ' + show(autoDone[0].processedNote, '处理说明未记录')) : null
      ]));
      if (autoDone.length) {
        autoDone.forEach(function (item) {
          folded.appendChild(h('div', { 'class': 'act-auto-item' }, [
            h('div', { 'class': 'act-name' }, show(item.title, '标题未记录')),
            h('div', { 'class': 'act-small act-muted' },
              show(item.project, '项目未记录') + '：' + show(item.item, '事项未记录') +
              ' · ' + (clockOf(item.processedAt, localDate) || '处理时间未记录')),
            h('div', { 'class': 'act-small' }, '主会话：' + show(item.processedNote, '处理说明未记录'))
          ]));
        });
      } else {
        folded.appendChild(h('p', { 'class': 'act-small act-muted' }, '没有已自动处理的记录'));
      }
      table.appendChild(folded);
      page.appendChild(table);

      var handoffs = open.filter(function (item) {
        return item.type === TYPES.handoff;
      });
      if (handoffs.length) {
        page.appendChild(h('div', { 'class': 'act-handoff-note' }, [
          '另有 ' + handoffs.length + ' 条交接还未处理：由主会话处理，这里不需要点决定（已计入今日关卡总数）。',
          h('span', { 'class': 'act-muted' }, ' ' + handoffs.map(function (item) {
            return show(item.title, '标题未记录');
          }).join('、'))
        ]));
      }
      var stray = open.filter(function (item) {
        return item.type !== TYPES.handoff && !LANES.some(function (laneDef) {
          return item.column === laneDef.label;
        });
      }).length + items.filter(function (item) {
        return item.status !== STATUSES.pending && item.status !== STATUSES.processed;
      }).length;
      if (stray) {
        page.appendChild(h('p', { 'class': 'act-diag' }, stray + ' 条记录的类型或状态无法识别，未放进三组'));
      }

      var picked = items.filter(function (item) {
        return validPick(item, picks[item.id]);
      });
      var withNote = picked.filter(function (item) {
        return isObj(picks[item.id]) && typeof picks[item.id].note === 'string' && picks[item.id].note.trim() !== '';
      }).length;
      noticeEl = h('p', { 'class': 'act-notice', role: 'status' });
      if (channelOn()) {
        page.appendChild(sendDock(picked, withNote));
      } else {
        page.appendChild(h('div', { 'class': 'act-dock' }, [
          h('span', { 'class': 'act-dock-sum' }, picked.length
            ? '已选 ' + picked.length + ' 条（' + (picked.length - withNote) + ' 条只点决定 + ' + withNote + ' 条带补充）'
            : '还没选决定'),
          noticeEl,
          h('span', { 'class': 'act-small act-muted act-dock-hint' }, '只包含点了决定的条目'),
          h('button', { type: 'button', 'class': 'act-primary', 'data-action': 'pending-export' }, '生成指令')
        ]));
      }
      container.appendChild(page);
      focusNote(container, focusAfter);
      focusAfter = null;
    }

    /** 通道页面的底部（PRD v1.1 4.4 第 9 项）：发送已选（N），每行一条；页面过期时生成回复指令。 */
    function sendDock(picked, withNote) {
      var ready = picked.filter(sendable);
      var skipped = picked.length - ready.length;
      var sum;
      if (batch && batch.phase === 'sending') {
        sum = '正在发出 ' + batch.total + ' 条…';
      } else if (batch && batch.phase === 'done' && !picked.length) {
        sum = batch.failed ? batch.ok + ' 条已送达，' + batch.failed + ' 条没发出去' : '已发出 ' + batch.ok + ' 条 · 每条的状态在各自那一行';
      } else if (batch && batch.phase === 'done' && batch.failed) {
        sum = batch.ok + ' 条已送达，' + batch.failed + ' 条没发出去 · 可以再点重发';
      } else {
        sum = picked.length
          ? '已选 ' + picked.length + ' 条（' + (picked.length - withNote) + ' 条快捷决定 + ' + withNote + ' 条带字）'
          : '还没选决定';
      }
      var hint = skipped
        ? skipped + ' 条不会发：「稍后」只在页面上标记，要写原因的还没写，或在另一台设备上'
        : '每行发一条，各自有编号和状态';
      var button;
      if (R.canSend()) {
        var busy = !!(batch && batch.phase === 'sending');
        button = h('button', {
          type: 'button', 'class': 'act-primary', 'data-action': 'pending-send-all', disabled: busy || !ready.length
        }, busy ? '发送中…' : '发送已选（' + ready.length + '）');
      } else {
        button = h('button', { type: 'button', 'class': 'act-primary', 'data-action': 'pending-reply-lines', disabled: !ready.length },
          '生成指令（' + ready.length + '）');
        hint = R.stale()
          ? '通道重启过：生成回复指令复制回主会话，或刷新页面后重发'
          : '本地通道连不上：生成回复指令复制回主会话，重新连上后可以再发';
      }
      return h('div', { 'class': 'act-dock' }, [
        h('span', { 'class': 'act-dock-sum' }, sum),
        noticeEl,
        h('span', { 'class': 'act-small act-muted act-dock-hint' }, hint),
        // 有没发出去的：可以退回复制回复指令（还留在已选里的那几条）
        batch && batch.phase === 'done' && batch.failed && R.canSend() && ready.length
          ? h('button', { type: 'button', 'class': 'cx-copy', 'data-action': 'pending-reply-lines' }, '复制指令')
          : null,
        button
      ]);
    }

    function sendAll() {
      var ready = items.filter(sendable);
      if (!ready.length || (batch && batch.phase === 'sending')) {
        return;
      }
      batch = { phase: 'sending', total: ready.length, ok: 0, failed: 0 };
      render();
      // 一条一条发（每行一条消息，各自编号）；上一条回来再发下一条，避免撞上每分钟上限时乱序
      ready.reduce(function (chain, item) {
        return chain.then(function () {
          return sendOne(item).then(function (result) {
            if (result && result.ok) {
              batch.ok += 1;
            } else {
              batch.failed += 1;
            }
          });
        });
      }, Promise.resolve()).then(function () {
        batch.phase = 'done';
        view.request();
      });
    }

    function replyLines() {
      var lines = items.filter(sendable).map(function (item) {
        var pick = picks[item.id];
        var quick = quickOf(item, pick);
        return NC.replyCommandLine(replyView(item, quick), quick, noteOf(pick));
      }).filter(Boolean);
      openExport(ui, lines);
      if (noticeEl) {
        noticeEl.textContent = lines.length ? '已生成 ' + lines.length + ' 条回复指令' : '没有能生成指令的回复';
      }
    }

    function exportNow() {
      var lines = NC.exportLines('pending', items, { pending: picks });
      var wanted = items.filter(function (item) {
        return validPick(item, picks[item.id]);
      }).length;
      openExport(ui, lines);
      if (!noticeEl) {
        return;
      }
      if (!wanted) {
        noticeEl.textContent = '还没有选任何决定，没有生成指令';
      } else if (lines.length < wanted) {
        noticeEl.textContent = '有 ' + (wanted - lines.length) + ' 张已选卡片没有导出：补充里有疑似敏感内容，或记录字段不完整';
      } else {
        noticeEl.textContent = '已生成 ' + lines.length + ' 条指令';
      }
    }

    var view = deferredRender(container, render);

    return mountWith(container, render, {
      subscribe: R ? R.subscribe : null,
      update: view.request,
      blur: view.blur,
      click: function (event) {
        var target = actionTarget(event.target, container);
        if (!target) {
          return;
        }
        var action = target.getAttribute('data-action');
        if (action === 'pending-export') {
          exportNow();
          return;
        }
        if (action === 'pending-send-all') {
          sendAll();
          return;
        }
        if (action === 'pending-reply-lines') {
          replyLines();
          return;
        }
        if (action === 'pending-reply-send' || action === 'pending-reply-copy') {
          var sendFor = target.getAttribute('data-for');
          var sendItem = sendFor !== null && own(byId, sendFor) ? byId[sendFor] : null;
          var sendSlot = sendItem ? replySlot(sendItem) : null;
          if (!sendSlot || !isDecidable(sendItem)) {
            return;
          }
          var sendQuick = quickOf(sendItem, picks[sendFor]);
          if (action === 'pending-reply-copy') {
            R.copy(sendSlot.card, replyView(sendItem, sendQuick), sendQuick, noteOf(picks[sendFor]), function (lines) {
              openExport(ui, lines);
            });
            return;
          }
          target.setAttribute('disabled', '');
          sendOne(sendItem).then(function () {
            view.request();
          });
          return;
        }
        if (action === 'pending-expand') {
          var openFor = target.getAttribute('data-for');
          if (openFor === null || !own(byId, openFor)) {
            return;
          }
          openId = openId === openFor ? null : openFor;
          render();
          return;
        }
        if (action !== 'pending-choice') {
          return;
        }
        var id = target.getAttribute('data-for');
        var choiceId = target.getAttribute('data-choice-id');
        var item = id !== null && own(byId, id) ? byId[id] : null;
        if (!item || !validPick(item, { choiceId: choiceId })) {
          return;
        }
        var pick = isObj(picks[id]) ? picks[id] : {};
        if (pick.choiceId === choiceId) {
          delete pick.choiceId;
        } else {
          pick.choiceId = choiceId;
        }
        if (own(pick, 'choiceId') || own(pick, 'note')) {
          picks[id] = pick;
        } else {
          delete picks[id];
        }
        // 要写字的决定（打回；通道页面上还有换个办法）点了就展开这一行、光标放进输入框（AC-41）
        if (typeof pick.choiceId === 'string' && needsText(item, pick.choiceId)) {
          openId = id;
          focusAfter = id;
        }
        render();
      },
      input: function (event) {
        var target = event.target;
        if (!target || target.nodeType !== 1 || target.getAttribute('data-action') !== 'pending-note') {
          return;
        }
        var id = target.getAttribute('data-for');
        if (id === null || !own(byId, id) || !isDecidable(byId[id])) {
          return;
        }
        var value = typeof target.value === 'string' ? target.value : '';
        var pick = isObj(picks[id]) ? picks[id] : {};
        var hadDraft = noteOf(pick).trim() !== '';
        var wasReady = channelOn() && sendable(byId[id]);
        if (value.trim() === '') {
          delete pick.note;
        } else {
          pick.note = value;
        }
        if (own(pick, 'choiceId') || own(pick, 'note')) {
          picks[id] = pick;
        } else {
          delete picks[id];
        }
        var slot = replySlot(byId[id]);
        if (slot) {
          R.saveDraft(slot.card, value);
          R.refresh(container, slot.card, slot, quickOf(byId[id], pick), value);
          // 回复列「草稿 / —」或底栏能发的条数变了（空 ↔ 有字、有效 ↔ 超长）才重画；重画保住焦点和光标
          if (hadDraft !== (value.trim() !== '') || wasReady !== (channelOn() && sendable(byId[id]))) {
            view.request();
          }
        }
      }
    });
  }

  NC.registerPage('pending', { mount: mountPending });

  // ========================================================= 派发确认

  function environmentOf(snap) {
    var env = snap.environment;
    return isObj(env) && typeof env.inOrca === 'boolean' ? env : null;
  }

  /** 用户在卡片上的显式选择（只认合法枚举，旧选择/手改值不进入计算）。 */
  function choiceOf(pick) {
    var choice = {};
    if (!isObj(pick)) {
      return choice;
    }
    if (typeof pick.roleId === 'string' && own(NC.ROLES, pick.roleId)) {
      choice.roleId = pick.roleId;
    }
    if (typeof pick.executor === 'string' && own(NC.EXECUTORS, pick.executor)) {
      choice.executor = pick.executor;
    }
    if (typeof pick.mode === 'string' && own(NC.MODES, pick.mode)) {
      choice.mode = pick.mode;
    }
    return choice;
  }

  /** 卡片当前方案：值一律按 P0 defaultsFor；没有用户选择时理由沿用快照里的默认理由。 */
  function planOf(task, pick, roles, env) {
    var choice = choiceOf(pick);
    var picked = Object.keys(choice).length > 0;
    var result = NC.defaultsFor(task, roles, env, choice);
    var snapshotReason = NC.safeText(task.defaultReason);
    return {
      roleId: choice.roleId || (typeof task.roleId === 'string' && own(NC.ROLES, task.roleId) ? task.roleId : null),
      executor: result.executor,
      mode: result.mode,
      reason: !picked && snapshotReason !== null ? snapshotReason : NC.safeText(result.reason) || '理由未记录',
      fallback: picked ? result.fallback === true : task.fallback === true
    };
  }

  /** 能否进入副本；不能时给出原因（展示用固定文案）。 */
  function readinessOf(task, plan, env) {
    if (!env) {
      return '缺少环境信息，不能派发';
    }
    if (task.kind === 'delivery') {
      if (env.inOrca !== true) {
        return '不在 Orca 里：团队副本不能进入';
      }
      if (task.selectable !== true) {
        return show(task.disabledReason, '这个副本暂时不能进入');
      }
      if (!isAbsolutePath(task.prdPath) || NC.safeText(task.prdPath) === null) {
        return '信息不完整：缺少 PRD 路径，不能组队';
      }
      return null;
    }
    if (task.kind !== 'single') {
      return '任务类型未识别，不能派发';
    }
    if (task.selectable !== true) {
      return show(task.disabledReason, '这个副本暂时不能进入');
    }
    if (!plan.executor || !plan.mode) {
      return '还差执行器或在哪里做，先选好再进入副本';
    }
    if (env.inOrca === true && plan.mode === 'terminal') {
      return 'Orca 里不能选新终端窗口';
    }
    return null;
  }

  function starsOf(h, difficulty) {
    if (typeof difficulty === 'number' && difficulty >= 1 && difficulty <= 3) {
      return h('span', { 'class': 'act-stars', 'aria-label': '难度 ' + difficulty + ' 星' },
        new Array(Math.floor(difficulty) + 1).join('★'));
    }
    return h('span', { 'class': 'act-stars act-muted', 'aria-label': '难度未知' }, '难度 ' + UNKNOWN);
  }

  function textList(list) {
    return (Array.isArray(list) ? list : []).map(function (entry) {
      return NC.safeText(entry);
    }).filter(function (entry) {
      return entry !== null;
    });
  }

  /**
   * 卡片关卡跟随当前有效角色：PRD 规定了序列的角色用固定序列；
   * 改选成没有固定序列的角色时，记录里的关卡属于原角色，不能沿用，按未记录展示。
   * 团队副本保持记录里的流水线关卡。
   */
  function stagesFor(task, roleId) {
    if (task.kind !== 'single') {
      return textList(task.stages);
    }
    if (roleId !== null && own(ROLE_STAGES, roleId)) {
      return ROLE_STAGES[roleId].slice();
    }
    var recordRole = typeof task.roleId === 'string' && own(NC.ROLES, task.roleId) ? task.roleId : null;
    return roleId === recordRole ? textList(task.stages) : [];
  }

  function mountDispatch(container, snapshot, ui) {
    var doc = container.ownerDocument || root.document;
    var h = builder(doc);
    var snap = isObj(snapshot) ? snapshot : {};
    var picks = ensureBucket(ui, 'dispatch');
    var tasks = uniqueItems(snap.dispatchTasks);
    var byId = Object.create(null);
    tasks.forEach(function (task) {
      byId[task.id] = task;
    });
    var roles = Array.isArray(snap.roles) ? snap.roles : [];
    var env = environmentOf(snap);
    var known = Array.isArray(snap.dispatchTasks) && !hasDiagnostic(snap, 'daily_source_missing');
    var noticeEl = null;
    var R = reply();

    function stateOf(task) {
      var pick = picks[task.id];
      var plan = planOf(task, pick, roles, env);
      var blocked = readinessOf(task, plan, env);
      return {
        plan: plan,
        blocked: blocked,
        selected: isObj(pick) && pick.selected === true && blocked === null
      };
    }

    var openId = null; // 同时最多展开一行

    function trackOf(stages) {
      return stages.length
        ? h('div', { 'class': 'act-track' }, stages.map(function (stage, index) {
          return [index ? h('em', { 'aria-hidden': 'true' }, '›') : null, h('span', null, stage)];
        }).reduce(function (all, pair) {
          return all.concat(pair);
        }, []))
        : h('div', { 'class': 'act-small act-muted' }, '信息未记录');
    }

    function toggleButton(task, state) {
      return h('button', {
        type: 'button',
        'class': 'act-toggle' + (state.selected ? ' is-selected' : ''),
        'aria-pressed': state.selected ? 'true' : 'false',
        'data-action': 'dispatch-toggle',
        'data-for': task.id,
        title: state.blocked || (state.selected ? '已组队，再点取消' : '按当前方案组队'),
        disabled: state.blocked !== null && !state.selected
      }, state.selected ? '已组队' : '组队');
    }

    /** 依据：团队 / 兜底 / 默认（或你改过）+ 理由。 */
    function basisCell(task, state) {
      var plan = state.plan;
      var picked = Object.keys(choiceOf(picks[task.id])).length > 0;
      var tag = task.kind === 'delivery'
        ? h('span', { 'class': 'act-tag act-tag-team' }, '团队')
        : (plan.fallback
          ? h('span', { 'class': 'act-tag act-tag-fallback' }, '兜底')
          : h('span', { 'class': 'act-tag act-tag-plain' }, picked ? '已改' : '默认'));
      return h('div', { 'class': 'act-c-basis', title: '理由：' + plan.reason }, [
        tag,
        h('span', { 'class': 'act-basis-text' }, plan.reason)
      ]);
    }

    function questRow(task, state) {
      var plan = state.plan;
      var team = task.kind === 'delivery';
      var open = openId === task.id;
      var wrap = h('article', {
        'class': 'act-item act-quest act-tone-' + (team ? 'violet' : (state.selected ? 'green' : 'gray')) +
          (state.selected ? ' is-chosen' : '') + (open ? ' is-open' : ''),
        'data-item-id': task.id,
        'data-selected': state.selected ? 'true' : null
      });
      var row = h('div', { 'class': 'act-row act-row-dispatch', 'data-action': 'dispatch-expand', 'data-for': task.id });
      row.appendChild(h('div', { 'class': 'act-c-toggle' }, toggleButton(task, state)));
      var title = show(task.title, '标题未记录');
      var project = show(task.project, '项目未记录');
      var taskNo = show(task.taskNo, '任务号未记录');
      row.appendChild(h('div', { 'class': 'act-c-main', title: project + ' · ' + taskNo + ' ' + title }, [
        team ? h('span', { 'class': 'act-tag act-tag-team' }, '团队副本') : null,
        h('span', { 'class': 'act-source' }, project + ' · ' + taskNo),
        h('strong', { 'class': 'act-subject' }, title)
      ]));
      row.appendChild(h('div', { 'class': 'act-c-stars' }, starsOf(h, task.difficulty)));
      if (team) {
        row.appendChild(h('div', {
          'class': 'act-c-squad',
          title: TEAM.map(function (member) {
            return NC.ROLES[member.roleId] + ' · ' + NC.EXECUTORS[member.executor];
          }).join('、')
        }, [
          h('span', { 'class': 'act-squad-faces' }, TEAM.map(function (member) {
            return avatarOf(ui, h, member.roleId);
          })),
          h('span', null, '三人小队 · 固定')
        ]));
      } else {
        row.appendChild(h('div', { 'class': 'act-c-role' }, plan.roleId === null
          ? h('span', { 'class': 'act-cell-pick act-muted' }, '（没写角色）')
          : h('span', { 'class': 'act-cell-pick' }, [avatarOf(ui, h, plan.roleId), NC.ROLES[plan.roleId]])));
        row.appendChild(h('div', { 'class': 'act-c-exec' },
          h('span', { 'class': 'act-cell-pick' }, plan.executor ? NC.EXECUTORS[plan.executor] : UNKNOWN)));
      }
      row.appendChild(h('div', { 'class': 'act-c-mode' },
        h('span', { 'class': 'act-cell-pick' }, plan.mode ? NC.MODES[plan.mode] : UNKNOWN)));
      row.appendChild(basisCell(task, state));
      var outputs = textList(task.outputs);
      row.appendChild(h('div', { 'class': 'act-c-out', title: outputs.join('、') },
        outputs.length ? outputs.join('、') : '信息未记录'));
      row.appendChild(expandButton(h, 'dispatch-expand', task.id, open));
      wrap.appendChild(row);

      // 展开行：改队伍的三组选项、关卡、产出、说明、完整理由（收起时 hidden）
      var panel = h('div', { 'class': 'act-x cx-expand', hidden: !open });
      var left = h('div', { 'class': 'act-x-main' }, [
        h('div', { 'class': 'act-x-item' }, project + ' · ' + taskNo),
        h('div', { 'class': 'act-name' }, title),
        h('p', { 'class': 'act-summary-full' }, show(task.summary, '说明未记录')),
        h('div', { 'class': 'act-label' }, '关卡'),
        trackOf(stagesFor(task, team ? null : plan.roleId)),
        h('div', { 'class': 'act-label' }, '通关产出'),
        h('div', { 'class': outputs.length ? 'act-small' : 'act-small act-muted' },
          outputs.length ? outputs.join('、') : '信息未记录')
      ]);
      if (team) {
        left.appendChild(h('div', { 'class': 'act-label' }, 'PRD'));
        left.appendChild(isAbsolutePath(task.prdPath) && NC.safeText(task.prdPath) !== null
          ? h('div', { 'class': 'act-path' }, task.prdPath)
          : h('div', { 'class': 'act-warn' }, '信息不完整：缺少 PRD 路径'));
      }
      left.appendChild(h('div', { 'class': 'act-reason' }, [
        '理由：' + plan.reason,
        !team && plan.fallback ? h('span', { 'class': 'act-tag act-tag-fallback' }, '兜底') : null
      ]));
      if (state.blocked) {
        left.appendChild(h('p', { 'class': 'act-warn' }, state.blocked));
      }
      panel.appendChild(left);

      var side = h('div', { 'class': 'act-x-side' });
      if (team) {
        side.appendChild(h('div', { 'class': 'act-label' }, '三人小队（固定，不能改）'));
        side.appendChild(h('div', { 'class': 'act-squad' }, TEAM.map(function (member) {
          return h('div', { 'class': 'act-member' }, [
            avatarOf(ui, h, member.roleId),
            h('span', null, NC.ROLES[member.roleId] + ' · ' + NC.EXECUTORS[member.executor])
          ]);
        })));
      } else {
        side.appendChild(h('div', { 'class': 'act-label' }, '派谁去'));
        side.appendChild(h('div', { 'class': 'act-chips' }, PICKABLE_ROLES.map(function (roleId) {
          return chip(h, { 'data-action': 'dispatch-role', 'data-for': task.id, 'data-value': roleId },
            plan.roleId === roleId, [avatarOf(ui, h, roleId), NC.ROLES[roleId]]);
        })));
        if (plan.roleId === null) {
          side.appendChild(h('div', { 'class': 'act-small act-muted' }, '角色未指定'));
        }
        side.appendChild(h('div', { 'class': 'act-label' }, '用什么执行器'));
        side.appendChild(h('div', { 'class': 'act-chips' }, NC.EXECUTOR_IDS.map(function (executor) {
          return chip(h, { 'data-action': 'dispatch-executor', 'data-for': task.id, 'data-value': executor },
            plan.executor === executor, NC.EXECUTORS[executor]);
        })));
        side.appendChild(h('div', { 'class': 'act-label' }, '在哪里做'));
        if (env) {
          var modes = env.inOrca ? ['worktree', 'directory'] : ['terminal'];
          side.appendChild(h('div', { 'class': 'act-chips' }, modes.map(function (mode) {
            return chip(h, { 'data-action': 'dispatch-mode', 'data-for': task.id, 'data-value': mode },
              plan.mode === mode, NC.MODES[mode]);
          })));
        } else {
          side.appendChild(h('div', { 'class': 'act-warn' }, '缺少环境信息'));
        }
      }
      panel.appendChild(side);
      wrap.appendChild(panel);
      return wrap;
    }

    function render() {
      clear(container);
      var page = h('div', { 'class': 'act act-dispatch' });
      var states = tasks.map(stateOf);
      var ready = states.filter(function (state) {
        return state.selected;
      }).length;
      var canAll = states.some(function (state) {
        return !state.selected && state.blocked === null;
      });

      var goal = h('div', { 'class': 'act-goal' });
      goal.appendChild(h('span', { 'class': 'act-goal-label' }, '今日出发'));
      if (known) {
        goal.appendChild(h('span', { 'class': 'act-goal-num' }, '已组队 ' + ready + ' / ' + tasks.length));
        goal.appendChild(progressBar(h, ready, tasks.length));
      } else {
        goal.appendChild(h('span', { 'class': 'act-goal-num' }, UNKNOWN));
      }

      page.appendChild(h('div', { 'class': 'act-head' }, [
        h('h2', null, '副本大厅'),
        h('p', { 'class': 'act-sub' }, '默认方案已填好：确认就点「组队」，要改就点开这一行'),
        h('button', {
          type: 'button',
          'class': 'act-secondary',
          'data-action': 'dispatch-all-default',
          disabled: !canAll
        }, '按默认全部组队'),
        goal
      ]));
      page.appendChild(h('p', { 'class': 'act-banner' + (env && env.inOrca ? '' : ' act-banner-warn') }, !env
        ? '缺少环境信息：不能确定在不在 Orca 里，暂不能派发'
        : (env.inOrca
          ? '当前在 Orca 里：在哪里做可选新建工作区或现有目录'
          : '不在 Orca 里：在哪里做只有新终端窗口，团队副本不能进入')));
      if (!known) {
        page.appendChild(h('p', { 'class': 'act-diag' }, '当日计划缺失，候选未知'));
      }

      diagnosticsWithPrefix(snap, 'daily_').forEach(function (diagnostic) {
        page.appendChild(h('p', { 'class': 'act-diag' }, show(diagnostic.message, '当日计划有异常，按未知展示')));
      });

      if (!tasks.length) {
        page.appendChild(h('p', { 'class': 'act-empty' }, known
          ? '今天没有待派的副本'
          : '当日计划读不到，待派副本未知（' + UNKNOWN + '）'));
      } else {
        var table = h('div', { 'class': 'act-table act-table-dispatch act-hall' });
        table.appendChild(h('div', { 'class': 'act-th act-row-dispatch', 'aria-hidden': 'true' }, [
          h('span', null, '组队'), h('span', null, '任务'), h('span', null, '难度'), h('span', null, '派谁去'),
          h('span', null, '执行器'), h('span', null, '在哪里做'), h('span', null, '依据'), h('span', null, '产出'),
          h('span', null, '')
        ]));
        tasks.forEach(function (task, index) {
          table.appendChild(questRow(task, states[index]));
        });
        page.appendChild(table);
      }

      noticeEl = h('p', { 'class': 'act-notice', role: 'status' });
      if (channelOn()) {
        page.appendChild(sendDock(ready));
      } else {
        page.appendChild(h('div', { 'class': 'act-dock' }, [
          h('span', { 'class': 'act-dock-sum' }, '已组队 ' + ready + ' 个副本'),
          noticeEl,
          h('span', { 'class': 'act-small act-muted act-dock-hint' }, '只为已组队的副本生成确认派发指令'),
          h('button', { type: 'button', 'class': 'act-primary', 'data-action': 'dispatch-export' }, '全部出发 · 生成指令')
        ]));
      }
      container.appendChild(page);
    }

    function linesNow() {
      return NC.exportLines('dispatch', tasks, {
        dispatch: picks,
        environment: env,
        roleDefinitions: roles
      });
    }

    /**
     * 通道页面的底部（PRD v1.1 4.5 第 9 项）：「全部出发（N）」把 v0 导出的确认派发指令作为一条消息发给主会话；
     * 发出后显示编号和回复状态，组队没变就不能再发（防重复派发）；页面过期时退回「全部出发 · 生成指令」。
     */
    function sendDock(ready) {
      var lines = linesNow();
      var form = R.form('dispatch');
      var state = R.local('dispatch');
      var sending = !!state && state.phase === 'sending';
      var sentNow = typeof form.sentSig === 'string' && form.sentSig === JSON.stringify(lines) && lines.length > 0;
      var tooMany = lines.length > NC.REPLY_LIMITS.maxLines;
      var sum = sentNow
        ? '已发给主会话 · 编号 ' + form.sentId + ' · 主会话按「确认派发」执行，派出去后在运行监督里看'
        : '已组队 ' + ready + ' 个副本 · 一条消息发给主会话';
      var hint = '只发已组队的副本';
      if (ready > lines.length) {
        hint = (ready - lines.length) + ' 个已组队副本不会发：方案或记录字段不完整';
      } else if (tooMany) {
        hint = '一次最多发 ' + NC.REPLY_LIMITS.maxLines + ' 条，少组几个再发';
      }
      var button;
      if (R.canSend()) {
        button = h('button', {
          type: 'button', 'class': 'act-primary', 'data-action': 'dispatch-send',
          disabled: sending || sentNow || tooMany || !lines.length
        }, sending ? '发送中…' : '全部出发（' + lines.length + '）');
      } else {
        hint = R.stale()
          ? '通道重启过：生成指令复制回主会话，或刷新页面后重发'
          : '本地通道连不上：生成指令复制回主会话，重新连上后可以再发';
        button = h('button', { type: 'button', 'class': 'act-primary', 'data-action': 'dispatch-export' }, '全部出发 · 生成指令');
      }
      var failed = !!state && state.phase === 'failed';
      if (failed && state.message) {
        noticeEl.textContent = state.message;
      }
      var records = R.records('dispatch');
      return h('div', { 'class': 'act-dock' }, [
        h('span', { 'class': 'act-dock-sum' }, sum),
        records.length || state ? R.cell(h, R.statusView('dispatch', {}), 'act-dock-reply') : null,
        noticeEl,
        h('span', { 'class': 'act-small act-muted act-dock-hint' }, hint),
        // 没发出去时可以退回复制指令（PRD v1.1 4.8：失败 → 重试 / 复制指令）
        failed && R.canSend() ? h('button', { type: 'button', 'class': 'cx-copy', 'data-action': 'dispatch-export' }, '复制指令') : null,
        button
      ]);
    }

    function sendNow() {
      var lines = linesNow();
      var state = R.local('dispatch');
      if (!lines.length || lines.length > NC.REPLY_LIMITS.maxLines || (state && state.phase === 'sending')) {
        return;
      }
      var sig = JSON.stringify(lines);
      R.send('dispatch', { card: 'dispatch', quick: null, text: '', lines: lines },
        { page: 'dispatch', item: '指挥台：派发确认', association: '—' }).then(function (result) {
        if (result && result.ok) {
          R.setForm('dispatch', { sentSig: sig, sentId: result.id });
        }
        view.request();
      });
    }

    function exportNow() {
      var lines = linesNow();
      var wanted = tasks.filter(function (task) {
        return stateOf(task).selected;
      }).length;
      openExport(ui, lines);
      if (!noticeEl) {
        return;
      }
      if (!wanted) {
        noticeEl.textContent = '还没有组队的副本，没有生成指令';
      } else if (lines.length < wanted) {
        noticeEl.textContent = '有 ' + (wanted - lines.length) + ' 个已组队副本没有导出：方案或记录字段不完整';
      } else {
        noticeEl.textContent = '已生成 ' + lines.length + ' 条确认派发指令';
      }
    }

    function pickFor(id) {
      var pick = isObj(picks[id]) ? picks[id] : {};
      picks[id] = pick;
      return pick;
    }

    var view = deferredRender(container, render);

    return mountWith(container, render, {
      subscribe: R ? R.subscribe : null,
      update: view.request,
      blur: view.blur,
      click: function (event) {
        var target = actionTarget(event.target, container);
        if (!target) {
          return;
        }
        var action = target.getAttribute('data-action');
        if (action === 'dispatch-export') {
          exportNow();
          return;
        }
        if (action === 'dispatch-send') {
          if (R && R.canSend()) {
            sendNow();
          }
          return;
        }
        if (action === 'dispatch-all-default') {
          // 按默认全部组队（AC-41、PRD v1.1 4.5）：还没组队的任务一律按默认方案组队——
          // 清掉没组队时手改过的角色 / 执行器 / 地点；已组队的保持原样；默认方案不能组队的不动。
          // 想按改过的方案出发，仍可以在那一行单独点「组队」。
          tasks.forEach(function (one) {
            if (stateOf(one).selected) {
              return;
            }
            if (readinessOf(one, planOf(one, {}, roles, env), env) === null) {
              picks[one.id] = { selected: true };
            }
          });
          render();
          return;
        }
        var id = target.getAttribute('data-for');
        var task = id !== null && own(byId, id) ? byId[id] : null;
        if (!task) {
          return;
        }
        if (action === 'dispatch-expand') {
          openId = openId === id ? null : id;
          render();
          return;
        }
        var value = target.getAttribute('data-value');
        var pick;
        if (action === 'dispatch-toggle') {
          var state = stateOf(task);
          if (state.selected) {
            pickFor(id).selected = false;
          } else if (state.blocked === null) {
            pickFor(id).selected = true;
          } else {
            return;
          }
        } else if (task.kind !== 'single') {
          // 团队副本固定队伍：不接受角色、执行器、地点改动。
          return;
        } else if (action === 'dispatch-role' && PICKABLE_ROLES.indexOf(value) !== -1) {
          pick = pickFor(id);
          if (pick.roleId === value) {
            delete pick.roleId;
          } else {
            pick.roleId = value;
          }
        } else if (action === 'dispatch-executor' && NC.EXECUTOR_IDS.indexOf(value) !== -1) {
          pick = pickFor(id);
          if (pick.executor === value) {
            delete pick.executor;
          } else {
            pick.executor = value;
          }
        } else if (action === 'dispatch-mode' && env &&
            (env.inOrca ? ['worktree', 'directory'] : ['terminal']).indexOf(value) !== -1) {
          pick = pickFor(id);
          if (pick.mode === value) {
            delete pick.mode;
          } else {
            pick.mode = value;
          }
        } else {
          return;
        }
        render();
      }
    });
  }

  NC.registerPage('dispatch', { mount: mountDispatch });
}(typeof window !== 'undefined' ? window : globalThis));
