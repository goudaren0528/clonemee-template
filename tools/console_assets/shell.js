/*
 * assistant-console P2 shell.js
 * 负责：
 * 1. 从数据节点读取快照与形象数据
 * 2. 渲染顶部生成时间与共用五指标（含未知态/无伪进度处理）
 * 3. 维护内存选择对象 selection（跨 tab 保留，刷新重置）
 * 4. 提供页面注册挂载机制与安全 ui 工具对象（ui.el, ui.avatar, ui.openExport, ui.navigate, ui.selection）
 * 5. 安全只读文本框与 Clipboard 导出交互
 * 6. 严禁改动或扩展已冻结的 AssistantConsole 对象
 */

(function () {
  'use strict';

  var NC = (typeof window !== 'undefined' ? window.AssistantConsole : (typeof globalThis !== 'undefined' ? globalThis.AssistantConsole : null));
  if (!NC) {
    return;
  }

  // 严格只允许的四个固定页面
  var ALLOWED_PAGES = ['pending', 'dispatch', 'runs', 'roles'];

  // DOM 属性白名单（供 ui.el 使用，严格拒绝 on*、style 及资源属性）
  var ALLOWED_ATTRS = {
    'class': true,
    'id': true,
    'title': true,
    'role': true,
    'tabindex': true,
    'type': true,
    'value': true,
    'placeholder': true,
    'readonly': true,
    'disabled': true,
    'selected': true,
    'checked': true,
    'href': true,
    'target': true,
    'rel': true,
    'aria-label': true,
    'aria-hidden': true,
    'aria-selected': true,
    'aria-expanded': true,
    'aria-controls': true,
    'aria-haspopup': true,
    'data-page': true,
    'data-item-id': true,
    'data-action': true,
    'data-metric': true,
    'data-kind': true,
    'data-status': true,
    'data-role': true,
    'data-group': true,
    'data-v': true
  };

  // 页面需要的常见安全非资源 DOM 标签白名单（严禁 script/style/iframe/img/link/object 等）
  var ALLOWED_TAGS = {
    'div': true,
    'span': true,
    'p': true,
    'a': true,
    'button': true,
    'input': true,
    'select': true,
    'option': true,
    'textarea': true,
    'label': true,
    'header': true,
    'footer': true,
    'main': true,
    'nav': true,
    'section': true,
    'article': true,
    'aside': true,
    'h1': true,
    'h2': true,
    'h3': true,
    'h4': true,
    'h5': true,
    'h6': true,
    'ul': true,
    'ol': true,
    'li': true,
    'dl': true,
    'dt': true,
    'dd': true,
    'table': true,
    'thead': true,
    'tbody': true,
    'tr': true,
    'th': true,
    'td': true,
    'details': true,
    'summary': true,
    'b': true,
    'strong': true,
    'i': true,
    'em': true,
    'small': true,
    'code': true,
    'pre': true,
    'kbd': true
  };

  // 布尔属性集合：false 必须省略，true 设置为空字符串
  var BOOLEAN_ATTRS = {
    'disabled': true,
    'readonly': true,
    'checked': true,
    'selected': true
  };

  // 严格安全 URL 校验：只允许 a 标签，且只接受安全 file: URI 或 loopback HTTP/HTTPS。
  // 使用 WHATWG URL 解析协议/主机/凭据，避免手写 authority 正则在 IPv6、空端口等形态上漂移。
  function isSafeHref(urlStr) {
    if (typeof urlStr !== 'string') return false;
    // 拒绝包含控制字符、换行、回车、制表符
    if (/[\u0000-\u001F\u007F\s]/.test(urlStr)) {
      return false;
    }
    // WHATWG 会把反斜线规范化为路径分隔符，先按原字符拒绝，避免 authority 被改写后绕过
    if (urlStr.indexOf('\\') !== -1) {
      return false;
    }
    // 拒绝百分号编码主机（如 %6cocalhost）；只看 authority，不影响路径/查询中的 %
    if (hasPercentInAuthority(urlStr)) {
      return false;
    }
    var parsed;
    try {
      parsed = new URL(urlStr);
    } catch (_e) {
      return false;
    }
    // file: 保持原语义：仅本地绝对路径前缀 file:///，不扩展 file://host
    if (parsed.protocol === 'file:') {
      return urlStr.toLowerCase().indexOf('file:///') === 0;
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return false;
    }
    if (parsed.username !== '' || parsed.password !== '') {
      return false;
    }
    var host = parsed.hostname;
    return host === 'localhost' || host === '127.0.0.1' || host === '[::1]';
  }

  function hasPercentInAuthority(urlStr) {
    var start = urlStr.indexOf('://');
    if (start === -1) return false;
    start += 3;
    for (var i = start; i < urlStr.length; i += 1) {
      var ch = urlStr.charAt(i);
      if (ch === '/' || ch === '?' || ch === '#') {
        return urlStr.slice(start, i).indexOf('%') !== -1;
      }
    }
    return urlStr.slice(start).indexOf('%') !== -1;
  }

  // 提取或初始化快照数据
  var snapshot = null;
  var avatars = Object.create(null);

  function readJsonNode(id) {
    var doc = (typeof document !== 'undefined' ? document : null);
    if (!doc) return null;
    var node = doc.getElementById(id);
    if (!node || typeof node.textContent !== 'string') return null;
    try {
      return JSON.parse(node.textContent);
    } catch (_e) {
      return null;
    }
  }

  // 全局内存选择字典（跨页保留，重新打开页面重置）
  var selection = {
    pending: Object.create(null),
    dispatch: Object.create(null),
    roles: Object.create(null),
    environment: { inOrca: false, executorsAvailable: [] },
    roleDefinitions: []
  };

  // 当前活跃页面与已卸载钩子
  var currentPage = null;
  var currentDispose = null;

  // 安全 DOM 创建函数：ui.el(tag, attributes, text)
  function el(tag, attributes, text) {
    if (typeof tag !== 'string' || !/^[a-z][a-z0-9-]*$/i.test(tag)) {
      throw new TypeError('ui.el: 非法标签名');
    }
    var tagLower = tag.toLowerCase();
    if (!ALLOWED_TAGS[tagLower]) {
      throw new TypeError('ui.el: 拒绝非白名单标签');
    }
    var doc = (typeof document !== 'undefined' ? document : null);
    if (!doc) {
      throw new Error('ui.el: document 不存在');
    }
    var node = doc.createElement(tagLower);
    if (attributes && typeof attributes === 'object') {
      var keys = Object.keys(attributes);
      for (var i = 0; i < keys.length; i += 1) {
        var key = keys[i];
        var val = attributes[key];
        if (key.indexOf('on') === 0) {
          throw new TypeError('ui.el: 拒绝 on* 事件属性');
        }
        var lower = key.toLowerCase();
        if (lower === 'style') {
          throw new TypeError('ui.el: 拒绝 style 属性');
        }
        if (!ALLOWED_ATTRS[lower] && lower.indexOf('data-') !== 0 && lower.indexOf('aria-') !== 0) {
          throw new TypeError('ui.el: 属性不在白名单');
        }

        // 处理布尔属性
        if (BOOLEAN_ATTRS[lower]) {
          if (val === true || val === '' || val === lower) {
            node.setAttribute(lower, '');
          }
          // false、null、undefined 时省略，不设置该属性
          continue;
        }

        if (val !== null && val !== undefined) {
          if (lower === 'href') {
            if (tagLower !== 'a') {
              throw new TypeError('ui.el: 仅 a 标签允许 href');
            }
            if (!isSafeHref(String(val))) {
              throw new TypeError('ui.el: 拒绝不安全 URL');
            }
          }
          node.setAttribute(key, String(val));
        }
      }
    }
    if (text !== null && text !== undefined) {
      if (typeof text !== 'string' && typeof text !== 'number') {
        throw new TypeError('ui.el: 第三个参数只接受安全文本或数字，不接受 HTML 或对象');
      }
      node.textContent = String(text);
    }
    return node;
  }

  var avatarInstanceCounter = 0;

  // 头像渲染函数：ui.avatar(roleId)
  function avatar(roleId) {
    var container = el('span', { 'class': 'console-avatar', 'role': 'img', 'aria-label': roleId ? String(roleId) : '角色形象' });
    var svgStr = (roleId && avatars && typeof avatars[roleId] === 'string') ? avatars[roleId] : null;
    if (svgStr) {
      // 避免同页多个头像实例的 mask id 冲突（每个实例重命名 mask id 和引用）
      avatarInstanceCounter += 1;
      var instanceMaskId = 'viewboxMask-' + String(avatarInstanceCounter);
      var scopedSvg = svgStr
        .replace(/\bid="viewboxMask"/g, 'id="' + instanceMaskId + '"')
        .replace(/mask="url\(#viewboxMask\)"/g, 'mask="url(#' + instanceMaskId + ')"');

      // 通过安全的 DOMParser 解析受控审计过的纯 SVG 节点，并导入
      try {
        var parser = new DOMParser();
        var svgDoc = parser.parseFromString(scopedSvg, 'image/svg+xml');
        var svgEl = svgDoc.documentElement;
        if (svgEl && svgEl.nodeName.toLowerCase() === 'svg') {
          var imported = (typeof document !== 'undefined' ? document.importNode(svgEl, true) : svgEl);
          container.appendChild(imported);
          return container;
        }
      } catch (_e) {}
    }
    // 未知角色中性占位
    var placeholder = el('span', { 'class': 'console-avatar-placeholder' }, '?');
    container.appendChild(placeholder);
    return container;
  }

  // 微动效（V1，只做展示）：页面模块每次点击都整页重绘，CSS 过渡跟不上新节点。
  // 这里在重绘后给新节点加 base.css 的 mx-* 起始态类，两帧后去掉，元素从起始态过渡回常态。
  // 只加 / 去类名和临时 transform，不碰 selection、导出和数据；reduced-motion 或没有
  // requestAnimationFrame（Node 测试薄 DOM）时什么都不做，任何异常都吞掉，不影响原交互。
  var MOTION_COUNTERS = '.act-goal-num, .ovw-goal-num, .ovw-chip-count, .act-count';
  var MOTION_NOTICES = '.act-notice, .ovw-notice';
  var MOTION_BARS = '.act-bar-fill, .ovw-bar-fill';
  var MOTION_PULSE_ACTIONS = {
    'pending-choice': true,
    'dispatch-toggle': true,
    'dispatch-role': true,
    'dispatch-executor': true,
    'dispatch-mode': true,
    'role-action': true,
    'dispatch-all-default': true
  };
  var pendingMotion = null;

  function motionOn() {
    if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') return false;
    try {
      if (typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        return false;
      }
    } catch (_e) {}
    return true;
  }

  function nextFrames(fn) {
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(fn);
    });
  }

  function play(node, cls) {
    if (!node || !node.classList || !motionOn()) return;
    try {
      node.classList.add(cls);
      void node.offsetWidth;
      nextFrames(function () {
        node.classList.remove(cls);
      });
    } catch (_e) {}
  }

  // 进度条从旧长度伸到新长度：先 scaleX(旧/新) 停住，再去掉让它过渡回 1
  function growBar(node, fromPct) {
    var toPct = parseFloat(node.style.width);
    if (!(toPct > 0) || !(fromPct >= 0) || fromPct === toPct) return;
    node.classList.add('mx-hold');
    node.style.transform = 'scaleX(' + (fromPct / toPct) + ')';
    void node.offsetWidth;
    nextFrames(function () {
      node.classList.remove('mx-hold');
      node.style.transform = '';
    });
  }

  function motionKey(node) {
    return JSON.stringify(['data-action', 'data-for', 'data-value', 'data-choice-id'].map(function (name) {
      return node.getAttribute(name) || '';
    }));
  }

  function findBy(root, selector, test) {
    var list = root.querySelectorAll(selector);
    for (var i = 0; i < list.length; i += 1) {
      if (test(list[i])) return list[i];
    }
    return null;
  }

  function collect(root, selector, read) {
    var out = [];
    var list = root.querySelectorAll(selector);
    for (var i = 0; i < list.length; i += 1) {
      out.push(read(list[i]));
    }
    return out;
  }

  function readText(node) { return node.textContent; }

  function expandOpen(card) {
    var panel = card.querySelector('.cx-expand');
    return !!panel && !panel.hasAttribute('hidden');
  }
  function readWidth(node) { return parseFloat(node.style.width); }

  // 捕获阶段：页面模块重绘之前记下点了什么、计数和进度条是多少
  function motionBefore(event) {
    pendingMotion = null;
    if (!motionOn()) return;
    try {
      var main = document.getElementById('console-main');
      var target = event.target && event.target.closest ? event.target.closest('[data-action]') : null;
      if (!main || !target || !main.contains(target) || target.tagName === 'TEXTAREA') return;
      var card = target.closest('[data-item-id]');
      pendingMotion = {
        main: main,
        page: currentPage,
        action: target.getAttribute('data-action'),
        key: motionKey(target),
        cardId: card ? card.getAttribute('data-item-id') : null,
        wasOpen: card ? expandOpen(card) : false,
        counters: collect(main, MOTION_COUNTERS, readText),
        notices: collect(main, MOTION_NOTICES, readText),
        bars: collect(main, MOTION_BARS, readWidth)
      };
    } catch (_e) {
      pendingMotion = null;
    }
  }

  // 冒泡到 document 时页面已经重绘完：在新节点上播起始态
  function motionAfter() {
    var m = pendingMotion;
    pendingMotion = null;
    if (!m || m.page !== currentPage) return;
    try {
      var main = m.main;
      var pressed = findBy(main, '[data-action]', function (node) { return motionKey(node) === m.key; });
      if (pressed && pressed.tagName === 'BUTTON') play(pressed, 'mx-press');
      var card = m.cardId === null ? null : findBy(main, '[data-item-id]', function (node) {
        return node.getAttribute('data-item-id') === m.cardId;
      });
      if (card && MOTION_PULSE_ACTIONS[m.action]) play(card, 'mx-pulse');
      // 展开行（四页通用 .cx-expand）：这次点击让它从收起变成展开时淡入
      if (card && !m.wasOpen && expandOpen(card)) play(card.querySelector('.cx-expand'), 'mx-reveal');
      if (m.action === 'runs-filter') play(main.querySelector('.ovw-grid'), 'mx-enter');
      var counters = main.querySelectorAll(MOTION_COUNTERS);
      for (var i = 0; i < counters.length && i < m.counters.length; i += 1) {
        if (counters[i].textContent !== m.counters[i]) play(counters[i], 'mx-bump');
      }
      var notices = main.querySelectorAll(MOTION_NOTICES);
      for (var j = 0; j < notices.length && j < m.notices.length; j += 1) {
        if (notices[j].textContent && notices[j].textContent !== m.notices[j]) play(notices[j], 'mx-bump');
      }
      var bars = main.querySelectorAll(MOTION_BARS);
      for (var k = 0; k < bars.length && k < m.bars.length; k += 1) {
        growBar(bars[k], m.bars[k]);
      }
    } catch (_e) {}
  }

  // 导出浮层：ui.openExport(lines)
  function openExport(lines) {
    var doc = (typeof document !== 'undefined' ? document : null);
    if (!doc) return;
    var dock = doc.getElementById('console-dock');
    var txt = doc.getElementById('console-dock-text');
    var status = doc.getElementById('console-dock-status');
    if (!dock || !txt) return;

    dock.style.display = 'block';
    play(dock, 'mx-rise');
    play(status, 'mx-bump');

    if (!Array.isArray(lines) || lines.length === 0) {
      txt.value = '';
      if (status) status.textContent = '未选择任何有效操作，不生成空指令';
      return;
    }

    var content = lines.join('\n');
    txt.value = content;
    if (status) {
      status.textContent = '已就绪 ' + lines.length + ' 条指令';
    }
    // 聚焦并自动全选，方便用户直接 Ctrl+C 手动复制
    try {
      txt.focus();
      txt.select();
    } catch (_e) {}
  }

  // 页面导航跳转：ui.navigate(pageId, itemId)
  function navigate(pageId, itemId) {
    if (ALLOWED_PAGES.indexOf(pageId) === -1) {
      return false;
    }
    // 如果指定了 itemId，必须在快照中能够查找到真实关联
    if (itemId !== undefined && itemId !== null) {
      var found = false;
      if (snapshot) {
        if (pageId === 'pending' && Array.isArray(snapshot.pending)) {
          for (var i = 0; i < snapshot.pending.length; i += 1) {
            if (snapshot.pending[i] && snapshot.pending[i].id === itemId) {
              found = true;
              break;
            }
          }
        } else if (pageId === 'runs' && Array.isArray(snapshot.runs)) {
          for (var j = 0; j < snapshot.runs.length; j += 1) {
            if (snapshot.runs[j] && snapshot.runs[j].id === itemId) {
              found = true;
              break;
            }
          }
        }
      }
      if (!found) {
        return false;
      }
    }
    renderTab(pageId);
    if (itemId && typeof document !== 'undefined') {
      var allItems = document.querySelectorAll('[data-item-id]');
      var targetNode = null;
      for (var k = 0; k < allItems.length; k += 1) {
        if (allItems[k].getAttribute('data-item-id') === String(itemId)) {
          targetNode = allItems[k];
          break;
        }
      }
      if (targetNode && typeof targetNode.scrollIntoView === 'function') {
        targetNode.scrollIntoView();
      }
    }
    return true;
  }

  // 组装 ui 对象传递给 mount(container, snapshot, ui)
  var uiApi = {
    selection: selection,
    el: el,
    avatar: avatar,
    openExport: openExport,
    navigate: navigate
  };

  // 渲染顶部生成时间与指标
  function renderHud(snap) {
    var doc = (typeof document !== 'undefined' ? document : null);
    if (!doc || !snap) return;

    // 1. 生成时间 HH:MM
    var genEl = doc.getElementById('console-gen-time');
    if (genEl) {
      var timeStr = '--:--';
      if (typeof snap.generatedAt === 'string') {
        var match = snap.generatedAt.match(/T(\d{2}:\d{2})/);
        if (match) {
          timeStr = match[1];
        }
      }
      var viaChannel = typeof window !== 'undefined' && window.AssistantChannel &&
        typeof window.AssistantChannel.info === 'function' && window.AssistantChannel.info().mode === 'channel';
      // v1.1：通道给的页面刷新就拿最新（PRD 增量 5.1 对原 AC-1 的解释）；本机文件页面仍不会自动刷新
      genEl.textContent = '生成于 ' + timeStr + (viaChannel ? ' · 刷新页面拿最新' : ' · 页面不会自动刷新');
    }

    var stats = snap.stats || {};

    // 2. 指标 1：等你出手 (pendingCount)
    var pValEl = doc.querySelector('.stat[data-metric="pending"] .stat-val');
    var badgePending = doc.getElementById('tab-badge-pending');
    var pCount = (stats.pendingCount && typeof stats.pendingCount.value === 'number') ? stats.pendingCount.value : null;
    var pText = (pCount !== null) ? String(pCount) : '—';
    if (pValEl) pValEl.textContent = pText;
    if (badgePending) badgePending.textContent = pText;

    // 3. 指标 2：进行中的副本 (runningCount)
    var rValEl = doc.querySelector('.stat[data-metric="running"] .stat-val');
    var rCount = (stats.runningCount && typeof stats.runningCount.value === 'number') ? stats.runningCount.value : null;
    if (rValEl) rValEl.textContent = (rCount !== null) ? String(rCount) : '—';

    // 4. 指标 3：今日通关 / 今日出发 与进度条
    var tpValEl = doc.querySelector('.stat[data-metric="today-progress"] .stat-val');
    var tpBarEl = doc.querySelector('.stat[data-metric="today-progress"] .bar i');
    var compToday = (stats.completedToday && typeof stats.completedToday.value === 'number') ? stats.completedToday.value : null;
    var dispToday = (stats.dispatchedToday && typeof stats.dispatchedToday.value === 'number') ? stats.dispatchedToday.value : null;

    if (compToday !== null || dispToday !== null) {
      var compStr = (compToday !== null) ? String(compToday) : '—';
      var dispStr = (dispToday !== null) ? String(dispToday) : '—';
      if (tpValEl) tpValEl.textContent = compStr + ' / ' + dispStr;

      // 只有两者均已知且分母大于 0 时才计算进度，否则 0 分母/未知不画伪进度
      if (compToday !== null && dispToday !== null && dispToday > 0) {
        var ratio = Math.min(100, Math.round((compToday / dispToday) * 100));
        if (tpBarEl) tpBarEl.style.width = ratio + '%';
      } else {
        if (tpBarEl) tpBarEl.style.width = '0%';
      }
    } else {
      if (tpValEl) tpValEl.textContent = '—';
      if (tpBarEl) tpBarEl.style.width = '0%';
    }

    // 5. 指标 4：本周通关
    var wValEl = doc.querySelector('.stat[data-metric="week-completed"] .stat-val');
    var wCount = (stats.completedWeek && typeof stats.completedWeek.value === 'number') ? stats.completedWeek.value : null;
    if (wValEl) wValEl.textContent = (wCount !== null) ? String(wCount) : '—';

    // 6. 指标 5：连续清空待处理 (按自然日，未知显示 —)
    var csValEl = doc.querySelector('.stat[data-metric="clear-streak"] .stat-val');
    var csCount = (stats.clearStreak && typeof stats.clearStreak.value === 'number') ? stats.clearStreak.value : null;
    if (csValEl) {
      csValEl.textContent = (csCount !== null) ? (csCount + ' 天') : '—';
    }
  }

  // 切换并渲染页面 Tab
  function renderTab(pageId) {
    if (ALLOWED_PAGES.indexOf(pageId) === -1) {
      return;
    }
    var doc = (typeof document !== 'undefined' ? document : null);
    if (!doc) return;

    // 清理前一个页面的 dispose 钩子
    if (typeof currentDispose === 'function') {
      try {
        currentDispose();
      } catch (_e) {}
      currentDispose = null;
    }

    // 更新 tabs 导航高亮与 aria
    var tabBtns = doc.querySelectorAll('#console-tabs .tab-btn');
    for (var i = 0; i < tabBtns.length; i += 1) {
      var btn = tabBtns[i];
      var isTarget = btn.getAttribute('data-page') === pageId;
      if (isTarget) {
        btn.classList.add('on');
        btn.setAttribute('aria-selected', 'true');
      } else {
        btn.classList.remove('on');
        btn.setAttribute('aria-selected', 'false');
      }
    }

    // 切页时重置隐藏并清空导出浮层，避免跨页残留旧导出指令（selection 选择数据在内存中保留）
    var dock = doc.getElementById('console-dock');
    if (dock) {
      dock.style.display = 'none';
      var dockTxt = doc.getElementById('console-dock-text');
      if (dockTxt) dockTxt.value = '';
      var dockStatus = doc.getElementById('console-dock-status');
      if (dockStatus) dockStatus.textContent = '只包含当前页已选项目';
    }

    currentPage = pageId;
    var container = doc.getElementById('console-main');
    if (!container) return;
    container.innerHTML = '';

    // 获取已注册页面
    var registered = (typeof NC.getPage === 'function') ? NC.getPage(pageId) : null;
    if (registered && typeof registered.mount === 'function') {
      try {
        var ret = registered.mount(container, snapshot, uiApi);
        if (ret && typeof ret.dispose === 'function') {
          currentDispose = ret.dispose;
        }
      } catch (mountErr) {
        var errCard = el('div', { 'class': 'placeholder-card' });
        errCard.appendChild(el('h2', {}, '页面加载出错'));
        errCard.appendChild(el('p', {}, '页面模块执行异常'));
        container.appendChild(errCard);
      }
    } else {
      // 模块尚未合入，明确显示占位提示，不伪造假数据
      var pCard = el('div', { 'class': 'placeholder-card', 'data-page': pageId });
      var h2 = el('h2', {}, '页面模块尚未合入');
      var desc = el('p', {}, '该功能由并行包独立交付，合包后自动就绪。');
      var note = el('div', { 'class': 'placeholder-note' }, '当前页签标识：' + pageId);
      pCard.appendChild(h2);
      pCard.appendChild(desc);
      pCard.appendChild(note);
      container.appendChild(pCard);
    }
    play(container, 'mx-enter');
  }

  // 绑定全局事件（单次挂载）
  // v1.1 页头：本地通道状态、「记录有更新 · 刷新」、页面过期横条（数据来自 AssistantChannel / AssistantReply）
  function renderChannel() {
    var doc = (typeof document !== 'undefined' ? document : null);
    if (!doc || typeof window === 'undefined') return;
    var CH = window.AssistantChannel;
    var R = window.AssistantReply;
    var info = CH && typeof CH.info === 'function' ? CH.info() : { mode: 'file', url: null };
    var isStale = !!(R && typeof R.stale === 'function' && R.stale());
    var isOffline = !!(R && typeof R.offline === 'function' && R.offline());
    var state = doc.getElementById('console-channel-state');
    if (state) {
      while (state.firstChild) state.removeChild(state.firstChild);
      var hostPort = info.url ? String(info.url).replace(/^http:\/\//, '').replace(/\/$/, '') : '';
      if (info.mode === 'channel' && !isStale && !isOffline) {
        state.setAttribute('class', 'chan chan-ok');
        state.appendChild(el('i', { 'aria-hidden': 'true' }));
        state.appendChild(el('span', {}, '本地通道 ' + hostPort));
      } else if (info.mode === 'channel' && isStale) {
        state.setAttribute('class', 'chan chan-off');
        state.appendChild(el('i', { 'aria-hidden': 'true' }));
        state.appendChild(el('span', {}, '通道重启过 · 刷新页面后重发'));
      } else if (info.mode === 'channel') {
        state.setAttribute('class', 'chan chan-off');
        state.appendChild(el('i', { 'aria-hidden': 'true' }));
        state.appendChild(el('span', {}, '本地通道连不上 · 回复改为复制指令'));
      } else if (info.url) {
        state.setAttribute('class', 'chan chan-go');
        state.appendChild(el('a', { href: info.url, title: '通道其实在跑：点这里切到能直接发送的页面' }, '去本地通道 ' + hostPort));
      } else {
        state.setAttribute('class', 'chan chan-off');
        state.appendChild(el('i', { 'aria-hidden': 'true' }));
        state.appendChild(el('span', {}, '本地通道没开 · 回复改为复制指令'));
      }
      state.removeAttribute('hidden');
    }
    var changed = doc.getElementById('console-records-changed');
    if (changed) {
      if (R && typeof R.recordsChanged === 'function' && R.recordsChanged()) {
        changed.removeAttribute('hidden');
      } else {
        changed.setAttribute('hidden', '');
      }
    }
    var banner = doc.getElementById('console-banner');
    if (banner) {
      if (isStale) {
        banner.textContent = '通道重启过：这个页面的发送凭证失效了，刷新页面后重发（没发出去的字刷新后还在）';
        banner.removeAttribute('hidden');
      } else {
        banner.setAttribute('hidden', '');
      }
    }
  }

  function setupShellEvents() {
    var doc = (typeof document !== 'undefined' ? document : null);
    if (!doc) return;

    // 微动效：捕获阶段记重绘前的状态，冒泡到 document 时在重绘后的新节点上播放
    if (!doc.__motionBound && typeof doc.addEventListener === 'function') {
      doc.__motionBound = true;
      doc.addEventListener('click', motionBefore, true);
      doc.addEventListener('click', motionAfter, false);
    }

    // 页签点击切换
    var tabNav = doc.getElementById('console-tabs');
    if (tabNav && !tabNav.__shellBound) {
      tabNav.__shellBound = true;
      tabNav.addEventListener('click', function (e) {
        var btn = e.target.closest('.tab-btn');
        if (!btn || !tabNav.contains(btn)) return;
        var page = btn.getAttribute('data-page');
        if (page) {
          renderTab(page);
        }
      });
    }

    // 导出 Dock 的关闭与复制
    var dock = doc.getElementById('console-dock');
    if (dock && !dock.__shellBound) {
      dock.__shellBound = true;
      var closeBtn = doc.getElementById('console-dock-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', function () {
          dock.style.display = 'none';
        });
      }

      var copyBtn = doc.getElementById('console-dock-copy');
      var copyText = doc.getElementById('console-dock-text');
      var copyStatus = doc.getElementById('console-dock-status');

      function setCopyStatus(text) {
        if (!copyStatus) return;
        copyStatus.textContent = text;
        play(copyStatus, 'mx-bump');
      }

      if (copyBtn && copyText) {
        copyBtn.addEventListener('click', function () {
          var val = copyText.value;
          if (!val) {
            setCopyStatus('无内容可复制');
            return;
          }
          function onCopyFail() {
            setCopyStatus('剪贴板访问被拒绝，已选中文字，请按 Ctrl+C 手动复制');
            try {
              copyText.focus();
              copyText.select();
            } catch (_e) {}
          }

          // 渐进增强：优先 navigator.clipboard，同步 throw 或 Promise 拒绝均正确降级提示并全选输入框
          if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            try {
              var p = navigator.clipboard.writeText(val);
              if (p && typeof p.then === 'function') {
                p.then(
                  function () {
                    setCopyStatus('已复制到剪贴板');
                  },
                  onCopyFail
                );
              } else {
                setCopyStatus('已复制到剪贴板');
              }
            } catch (_syncErr) {
              onCopyFail();
            }
          } else {
            setCopyStatus('剪贴板 API 不可用，已选中文字，请按 Ctrl+C 手动复制');
            try {
              copyText.focus();
              copyText.select();
            } catch (_e) {}
          }
        });
      }
    }
  }

  // 初始化入口
  function init() {
    snapshot = readJsonNode('console-snapshot');
    var rawAvatars = readJsonNode('console-avatars');
    if (rawAvatars && typeof rawAvatars === 'object') {
      avatars = rawAvatars;
    }

    if (snapshot) {
      if (snapshot.environment) {
        selection.environment = snapshot.environment;
      }
      if (Array.isArray(snapshot.roles)) {
        selection.roleDefinitions = snapshot.roles;
      }
    }

    renderHud(snapshot);
    setupShellEvents();
    if (typeof window !== 'undefined' && window.AssistantReply) {
      try {
        window.AssistantReply.seed(snapshot);
        window.AssistantReply.startPolling();
        window.AssistantReply.subscribe(renderChannel);
      } catch (_e) {}
    }
    renderChannel();
    var recordsButton = typeof document !== 'undefined' ? document.getElementById('console-records-changed') : null;
    if (recordsButton && !recordsButton.__shellBound && typeof recordsButton.addEventListener === 'function') {
      recordsButton.__shellBound = true;
      recordsButton.addEventListener('click', function () {
        if (typeof location !== 'undefined' && typeof location.reload === 'function') {
          location.reload();
        }
      });
    }
    renderTab('pending');
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
  }

  // 暴露只读 shell 状态供测试环境检查
  if (typeof window !== 'undefined') {
    window.__assistantShell = {
      init: init,
      ui: uiApi,
      renderTab: renderTab,
      getSnapshot: function () { return snapshot; },
      getCurrentPage: function () { return currentPage; }
    };
  }
}());
