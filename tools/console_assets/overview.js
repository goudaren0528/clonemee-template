/*
 * AssistantConsole overview (P4) — 运行监督「进行中的副本」与角色管理「我的队伍」两页。
 *
 * 依赖：core.js（P0）先加载，提供 safeText / exportLines / registerPage 与常量。
 * 注册：AssistantConsole.registerPage('runs' | 'roles', {mount})
 *   mount(container, snapshot, ui) -> {dispose()}
 *   - snapshot 只读：不写回、不排序原数组。
 *   - 运行页只看：筛选 / 展开是本页视图状态，不导出指令；「去处理 / 去验收」只经 ui.navigate('pending', id)，
 *     且只给快照里存在、仍待处理的关联条目。
 *   - 角色页选择只存 ui.selection.roles（P0 冻结形状），切页保留；导出只经 exportLines('roles') + ui.openExport(lines)。
 *   - 形象只经 ui.avatar(roleId)。
 * 口径：数字都取快照（P1 已按 4.7 计算）；没有数据显示「—」或空状态，不把未知当 0；
 *       多久没变化按生成时间算（页面不自动刷新），超过 30 分钟标橙以快照 stale 为准。
 * 安全：所有动态文字走文本节点，不拼 HTML、不设 on* 属性；展示前再过一遍 safeText；
 *       详情路径只在是安全绝对路径时展示，没有可点链接。
 */
(function (root) {
  'use strict';

  var NC = root.AssistantConsole;
  if (!NC || typeof NC.registerPage !== 'function') {
    throw new Error('overview.js 需要先加载 core.js');
  }

  var UNKNOWN = '—';
  var STALE_MS = 30 * 60 * 1000;
  var TYPES = NC.PENDING_TYPES;
  var STATUSES = NC.PENDING_STATUSES;

  // PRD 4.5：三种状态；unknown 只出现在「全部」里，保留原状态说明。
  var RUN_STATUS = {
    running: { label: '进行中', tone: 'blue' },
    waiting: { label: '等你拍板', tone: 'red' },
    done: { label: '已通关', tone: 'green' }
  };
  // v1.1 原型 07：全部 / 等你 / 出问题 / 进行中 / 已通关。「出问题」= 没通关且有出问题的关卡或 Agent。
  var FILTERS = [
    { key: 'all', label: '全部' },
    { key: 'waiting', label: '等你' },
    { key: 'problem', label: '出问题' },
    { key: 'running', label: '进行中' },
    { key: 'done', label: '已通关' }
  ];
  var STAGE_STATES = { done: '走过', current: '当前', problem: '出问题', unknown: '未到或未记录' };
  var AGENT_STATES = { active: '进行中', waiting: '等待中', done: '已结束', problem: '出问题', unknown: '状态未知' };

  // 队伍固定顺序（原型 04）；导出也按这个顺序。
  var ROLE_ORDER = ['product', 'dev', 'accept', 'pm', 'research', 'investigate'];
  var ROLE_STATUS = {
    draft: { label: '见习', tone: 'orange' },
    active: { label: '正式', tone: 'green' }
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

  function textList(list) {
    return (Array.isArray(list) ? list : []).map(function (entry) {
      return NC.safeText(entry);
    }).filter(function (entry) {
      return entry !== null;
    });
  }

  function countOf(value) {
    return typeof value === 'number' && isFinite(value) && value >= 0 ? Math.floor(value) : null;
  }

  /** Metric.value：只认非负有限数，其余（null / 缺失 / NaN）都是未知。 */
  function metricOf(metric) {
    return isObj(metric) ? countOf(metric.value) : null;
  }

  var DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;
  var DATE_TIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

  /** ISO 已带本地 offset：直接取字面时分，不做时区换算。 */
  function clockOf(iso, localDate) {
    if (typeof iso !== 'string' || !DATE_TIME_RE.test(iso)) {
      return null;
    }
    var hm = iso.slice(11, 16);
    return iso.slice(0, 10) === localDate ? hm : iso.slice(5, 10) + ' ' + hm;
  }

  function timeOf(iso) {
    if (typeof iso !== 'string' || !DATE_TIME_RE.test(iso)) {
      return NaN;
    }
    return Date.parse(iso);
  }

  function spanText(ms) {
    var minutes = Math.floor(ms / 60000);
    if (minutes < 1) {
      return '不到 1 分钟';
    }
    if (minutes < 60) {
      return minutes + ' 分钟';
    }
    var hours = Math.floor(minutes / 60);
    if (hours < 24) {
      return hours + ' 小时' + (minutes % 60 ? ' ' + (minutes % 60) + ' 分钟' : '');
    }
    return Math.floor(hours / 24) + ' 天' + (hours % 24 ? ' ' + (hours % 24) + ' 小时' : '');
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
    return h('span', { 'class': 'ovw-avatar-empty', 'aria-hidden': 'true' });
  }

  function progressBar(h, numerator, denominator) {
    var fill = h('span', { 'class': 'ovw-bar-fill' });
    var percent = denominator > 0 ? Math.max(0, Math.min(100, Math.round((numerator / denominator) * 100))) : 0;
    fill.style.width = percent + '%';
    return h('div', { 'class': 'ovw-bar', 'aria-hidden': 'true' }, fill);
  }

  function chip(h, attributes, pressed, children) {
    var attrs = Object.assign({ type: 'button', 'class': 'ovw-chip', 'aria-pressed': pressed ? 'true' : 'false' }, attributes);
    return h('button', attrs, children);
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

  // ---- v1.1 P6b 回复（reply.js 没加载时一切照 v0）

  function reply() {
    var R = root.AssistantReply;
    return R && typeof R.box === 'function' ? R : null;
  }

  /** 通道给的页面（含过期）：角色管理底部改「发送已选」。本机文件打开照 v0。 */
  function channelOn() {
    var R = reply();
    return !!R && R.mode() === 'channel';
  }

  /** 重画时保住输入框焦点（reply.js 的 keepFocus；没加载时直接重画）。 */
  function deferredRender(container, render) {
    var R = reply();
    if (R && typeof R.keepFocus === 'function') {
      return R.keepFocus(container, render);
    }
    return { request: render, blur: function () {} };
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

  // ========================================================= 运行监督

  function statusOf(run) {
    return typeof run.status === 'string' && own(RUN_STATUS, run.status) ? run.status : 'unknown';
  }

  /** 多久没变化：按生成时间算；严格超过 30 分钟标橙（以快照 stale 为准，不在页面重判）。 */
  function quietOf(run, generatedAt, localDate) {
    if (statusOf(run) === 'done') {
      var finished = clockOf(run.completedAt, localDate);
      return { text: finished ? '通关于 ' + finished : '通关时间未记录', tone: '' };
    }
    if (typeof run.lastChangedAt === 'string' && DATE_ONLY_RE.test(run.lastChangedAt)) {
      return { text: '最近变化 ' + run.lastChangedAt + '（具体时间未记录）', tone: '' };
    }
    var then = timeOf(run.lastChangedAt);
    var now = timeOf(generatedAt);
    if (!isFinite(then)) {
      return { text: '多久没变化：信息未记录', tone: '' };
    }
    if (!isFinite(now)) {
      return { text: '多久没变化：生成时间未记录', tone: '' };
    }
    if (then > now) {
      return { text: '最近变化晚于生成时间（记录异常）', tone: 'warn' };
    }
    return { text: spanText(now - then) + '没变化', tone: run.stale === true ? 'stale' : '' };
  }

  /** Agent 慢不慢：快照没有逐个 Agent 的 stale，按同一口径（严格超过 30 分钟）判断。 */
  function agentSlowOf(agent, generatedAt) {
    if (agent.state !== 'active' && agent.state !== 'waiting') {
      return null;
    }
    var then = timeOf(agent.lastChangedAt);
    var now = timeOf(generatedAt);
    if (!isFinite(then) || !isFinite(now) || now - then <= STALE_MS) {
      return null;
    }
    return spanText(now - then) + '没进展';
  }

  function mountRuns(container, snapshot, ui) {
    var doc = container.ownerDocument || root.document;
    var h = builder(doc);
    var snap = isObj(snapshot) ? snapshot : {};
    var runs = uniqueItems(snap.runs);
    var byId = Object.create(null);
    runs.forEach(function (run) {
      byId[run.id] = run;
    });
    var pendingById = Object.create(null);
    uniqueItems(snap.pending).forEach(function (item) {
      pendingById[item.id] = item;
    });
    var known = Array.isArray(snap.runs) && !hasDiagnostic(snap, 'runs_source_missing');
    var localDate = typeof snap.localDate === 'string' ? snap.localDate : null;
    var filter = 'all';
    var openId = null; // 同时最多展开一行
    var doneOpen = false; // 已通关组默认折叠
    var R = reply();
    var focusAfter = null; // 要写字的快捷评价（补充）点了之后，把光标放进这一行的输入框

    /** 快照给了回复区的运行（进行中、等你拍板；已通关和状态未知没有）。 */
    function slotOf(run) {
      return R && isObj(run) && isObj(run.reply) && typeof run.reply.card === 'string' ? run.reply : null;
    }

    function runView(run, slot, quick) {
      var label = null;
      NC.replyChoices(slot).forEach(function (choice) {
        if (choice.id === quick) {
          label = choice.label;
        }
      });
      return {
        page: 'runs',
        item: show(run.project, '项目未记录') + '：' + show(run.item, '事项未记录'),
        association: slot.association,
        choices: NC.replyChoices(slot),
        quickLabel: label
      };
    }

    function draftOf(slot) {
      var text = R.form(slot.card).text;
      return typeof text === 'string' ? text : '';
    }

    /** 展开行右侧的回复（PRD v1.1 4.6 第 6、7 项）：补充 / 问进度 / 先停下、输入框、送往、发送、回复记录。 */
    function compose(run, slot) {
      var form = R.form(slot.card);
      var other = slot.allowed === false && slot.reason === 'other_device';
      var must = form.quick !== null && Array.isArray(slot.needText) && slot.needText.indexOf(form.quick) !== -1;
      var box = h('div', { 'class': 'ovw-compose' }, [
        h('div', { 'class': 'ovw-label' }, slot.kind === 'main' ? '回复主会话' : '回复' + show(slot.receiver, '接收方'))
      ]);
      var field = null;
      if (!other) {
        box.appendChild(h('div', { 'class': 'ovw-chips cx-quick', role: 'group', 'aria-label': '快捷评价' },
          NC.replyChoices(slot).map(function (choice) {
            return chip(h, {
              'data-action': 'runs-quick',
              'data-for': run.id,
              'data-value': choice.id,
              title: choice.label
            }, form.quick === choice.id, choice.label);
          })));
        field = h('textarea', {
          'class': 'cx-field',
          rows: '3',
          placeholder: must ? '写一句（必填）' : '补一句（可不填）',
          'aria-label': must ? '补充内容' : '补一句',
          'data-action': 'runs-note',
          'data-for': run.id
        });
        field.value = draftOf(slot);
      }
      box.appendChild(R.box(h, {
        card: slot.card, slot: slot, quick: form.quick, text: draftOf(slot), field: field,
        sendAction: 'runs-reply-send', copyAction: 'runs-reply-copy', forId: run.id
      }));
      return box;
    }

    /** 能跳过去的待处理：快照里存在、仍待处理、能人工决定的关联条目（交接由主会话处理，不给入口）。 */
    function gotoTargets(run) {
      var seen = Object.create(null);
      var out = [];
      (Array.isArray(run.pendingIds) ? run.pendingIds : []).forEach(function (raw) {
        var id = idOf(raw);
        if (id === null || seen[id] || !own(pendingById, id)) {
          return;
        }
        seen[id] = true;
        var item = pendingById[id];
        if (item.status !== STATUSES.pending) {
          return;
        }
        if (item.type === TYPES.done) {
          out.push({ id: id, label: '去验收', title: show(item.title, '标题未记录') });
        } else if (item.type === TYPES.decision || item.type === TYPES.blocked) {
          out.push({ id: id, label: '去处理', title: show(item.title, '标题未记录') });
        }
      });
      return out;
    }

    function stageTrack(run) {
      var stages = (Array.isArray(run.stages) ? run.stages : []).filter(function (stage) {
        return isObj(stage) && NC.safeText(stage.label) !== null;
      });
      if (!stages.length) {
        return h('div', { 'class': 'ovw-small ovw-muted' }, '关卡信息未记录');
      }
      var nodes = [];
      stages.forEach(function (stage, index) {
        var state = own(STAGE_STATES, stage.state) ? stage.state : 'unknown';
        var label = NC.safeText(stage.label);
        if (index) {
          nodes.push(h('em', { 'aria-hidden': 'true' }, '›'));
        }
        nodes.push(h('span', {
          'class': 'ovw-stage ovw-stage-' + state,
          'data-state': state,
          title: label + '：' + STAGE_STATES[state],
          'aria-label': label + '：' + STAGE_STATES[state]
        }, label));
      });
      return h('div', { 'class': 'ovw-track' }, nodes);
    }

    function agentRow(run, agent) {
      var state = own(AGENT_STATES, agent.state) ? agent.state : 'unknown';
      var role = typeof agent.roleId === 'string' && own(NC.ROLES, agent.roleId) ? NC.ROLES[agent.roleId] : '角色未记录';
      var executor = typeof agent.executor === 'string' && own(NC.EXECUTORS, agent.executor)
        ? NC.EXECUTORS[agent.executor]
        : '执行器未记录';
      var pkg = run.kind === 'delivery' ? NC.safeText(agent.packageId) : null;
      var slow = agentSlowOf(agent, snap.generatedAt);
      var tone = state === 'problem' ? 'bad' : (slow ? 'slow' : (state === 'done' ? 'muted' : ''));
      var tag = state === 'problem' ? '出问题' : (slow || AGENT_STATES[state]);
      return h('div', { 'class': 'ovw-member', 'data-agent-state': state }, [
        avatarOf(ui, h, typeof agent.roleId === 'string' ? agent.roleId : null),
        h('span', { 'class': 'ovw-member-who' }, role + ' · ' + executor + (pkg ? ' · ' + pkg : '')),
        h('span', { 'class': 'ovw-activity' + (tone ? ' ovw-tone-' + tone : '') }, show(agent.activity, '正在干什么未记录')),
        h('span', { 'class': 'ovw-tag ovw-state' + (tone ? ' ovw-tone-' + tone : '') }, tag)
      ]);
    }

    function listBlock(label, entries, empty) {
      return [
        h('div', { 'class': 'ovw-label' }, label),
        entries.length
          ? h('ul', { 'class': 'ovw-list' }, entries.map(function (entry) {
            return h('li', null, entry);
          }))
          : h('div', { 'class': 'ovw-small ovw-muted' }, empty)
      ];
    }

    function packageLine(pkg) {
      var parts = [show(pkg.id, '包号未记录'), show(pkg.state, '状态未记录')];
      var deps = textList(pkg.dependencies);
      if (deps.length) {
        parts.push('依赖 ' + deps.join('、'));
      }
      var round = countOf(pkg.reviewRound);
      if (round !== null && round > 0) {
        parts.push('审查第 ' + round + ' 轮');
      }
      if (isObj(pkg.firstReview) && pkg.firstReview.verdict === 'pass') {
        parts.push('首轮审查通过');
      } else if (isObj(pkg.firstReview) && pkg.firstReview.verdict === 'fix') {
        parts.push('首轮审查要修');
      }
      return parts.join(' · ');
    }

    function details(run) {
      var box = h('div', { 'class': 'ovw-more' });
      listBlock('最近进展', textList(run.progress), '没有记录').forEach(function (node) {
        box.appendChild(node);
      });
      if (run.kind === 'delivery') {
        var pkgs = (Array.isArray(run.packages) ? run.packages : []).filter(isObj).map(packageLine);
        listBlock('工作包', pkgs, '工作包信息未记录').forEach(function (node) {
          box.appendChild(node);
        });
      } else {
        box.appendChild(h('div', { 'class': 'ovw-label' }, '工作包'));
        box.appendChild(h('div', { 'class': 'ovw-small ovw-muted' }, '单个任务，没有工作包'));
      }
      listBlock('替你做的决定（没打扰你）', textList(run.decisions), '没有记录').forEach(function (node) {
        box.appendChild(node);
      });
      box.appendChild(h('div', { 'class': 'ovw-label' }, '详情'));
      box.appendChild(isAbsolutePath(run.detailPath) && NC.safeText(run.detailPath) !== null
        ? h('div', { 'class': 'ovw-path' }, run.detailPath)
        : h('div', { 'class': 'ovw-small ovw-muted' }, '详情路径未记录或已屏蔽'));
      var targets = gotoTargets(run);
      if (targets.length) {
        box.appendChild(h('div', { 'class': 'ovw-goto' }, targets.map(function (target) {
          return h('div', { 'class': 'ovw-goto-row' }, [
            h('button', {
              type: 'button',
              'class': 'ovw-link',
              'data-action': 'runs-goto',
              'data-for': target.id
            }, target.label),
            h('span', { 'class': 'ovw-small ovw-muted' }, target.title)
          ]);
        })));
      }
      return box;
    }

    function hasProblem(run) {
      var stages = Array.isArray(run.stages) ? run.stages : [];
      var agents = Array.isArray(run.agents) ? run.agents : [];
      return stages.some(function (stage) {
        return isObj(stage) && stage.state === 'problem';
      }) || agents.some(function (agent) {
        return isObj(agent) && agent.state === 'problem';
      });
    }

    /** 分组（PRD v1.1 4.3）：等你 → 出问题 → 进行中 → 状态未知 → 已通关；状态未知单独成组（只在「全部」里），不算进行中。 */
    function groupOf(run) {
      var status = statusOf(run);
      if (status === 'waiting' || status === 'done') {
        return status;
      }
      if (hasProblem(run)) {
        return 'problem';
      }
      return status === 'running' ? 'running' : 'unknown';
    }

    /** 行内关卡：一格一关（绿走过 / 蓝当前 / 红出问题）+ 当前关名和 i/n；全量关卡在展开行里。 */
    function stageMini(run) {
      var stages = (Array.isArray(run.stages) ? run.stages : []).filter(function (stage) {
        return isObj(stage) && NC.safeText(stage.label) !== null;
      });
      if (!stages.length) {
        return h('div', { 'class': 'ovw-c-stage ovw-muted' }, '关卡未记录');
      }
      var at = -1;
      stages.forEach(function (stage, index) {
        if (stage.state === 'problem' || (stage.state === 'current' && at === -1)) {
          at = index;
        }
      });
      if (at === -1) {
        stages.forEach(function (stage, index) {
          if (stage.state === 'done') {
            at = index;
          }
        });
      }
      var label = at === -1 ? '未开始' : NC.safeText(stages[at].label) + ' ' + (at + 1) + '/' + stages.length;
      return h('div', { 'class': 'ovw-c-stage', title: stages.map(function (stage) {
        return NC.safeText(stage.label) + '：' + STAGE_STATES[own(STAGE_STATES, stage.state) ? stage.state : 'unknown'];
      }).join(' › ') }, [
        h('span', { 'class': 'ovw-segs', 'aria-hidden': 'true' }, stages.map(function (stage) {
          var state = own(STAGE_STATES, stage.state) ? stage.state : 'unknown';
          return h('i', { 'class': 'ovw-seg ovw-seg-' + state });
        })),
        h('span', { 'class': 'ovw-stage-now' }, label)
      ]);
    }

    /** 行内「正在干什么」：优先出问题的、再进行中的 Agent。 */
    function leadAgent(agents) {
      var order = ['problem', 'active', 'waiting', 'unknown', 'done'];
      for (var i = 0; i < order.length; i += 1) {
        for (var j = 0; j < agents.length; j += 1) {
          if (agents[j].state === order[i]) {
            return agents[j];
          }
        }
      }
      return agents[0] || null;
    }

    function runRow(run, group) {
      var status = statusOf(run);
      var meta = own(RUN_STATUS, status)
        ? RUN_STATUS[status]
        : { label: '状态未知' + (NC.safeText(run.rawStatus) ? '（原状态：' + NC.safeText(run.rawStatus) + '）' : ''), tone: 'gray' };
      // 行内标签只写短字；状态未知的原状态说明在 title 和展开行里
      var tag = group === 'problem' ? { label: '出问题', tone: 'red' }
        : (own(RUN_STATUS, status) ? meta : { label: '状态未知', tone: 'gray' });
      var quiet = quietOf(run, snap.generatedAt, localDate);
      var open = openId === run.id;
      var agents = (Array.isArray(run.agents) ? run.agents : []).filter(isObj);
      var slot = slotOf(run);
      // 另一台设备上的任务整行变淡（PRD v1.1 4.6 第 5 项、AC-33）
      var elsewhere = !!slot && slot.allowed === false && slot.reason === 'other_device';
      var wrap = h('article', {
        'class': 'ovw-card ovw-item ovw-run ovw-run-' + tag.tone + (open ? ' is-open' : '') + (elsewhere ? ' is-other' : ''),
        'data-item-id': run.id,
        'data-status': status,
        hidden: group === 'done' && !doneOpen && filter !== 'done'
      });
      var name = show(run.project, '项目未记录') + '：' + show(run.item, '事项未记录');
      var row = h('div', { 'class': 'ovw-row ovw-row-run', 'data-action': 'runs-detail', 'data-for': run.id });
      row.appendChild(h('div', { 'class': 'ovw-c-status' },
        h('span', { 'class': 'ovw-tag ovw-tag-' + tag.tone, title: meta.label }, tag.label)));
      row.appendChild(h('div', { 'class': 'ovw-c-main', title: name }, [
        h('h3', { 'class': 'ovw-name' }, name),
        run.kind === 'delivery' ? h('span', { 'class': 'ovw-tag ovw-tag-violet' }, '团队副本') : null
      ]));
      row.appendChild(stageMini(run));
      row.appendChild(h('div', { 'class': 'ovw-c-team', 'aria-hidden': 'true' }, agents.map(function (agent) {
        var state = own(AGENT_STATES, agent.state) ? agent.state : 'unknown';
        var slow = agentSlowOf(agent, snap.generatedAt);
        // 行内用角色首字做小徽章（原型 07），像素形象只在展开行的队伍明细里出现一次
        var roleName = typeof agent.roleId === 'string' && own(NC.ROLES, agent.roleId) ? NC.ROLES[agent.roleId] : null;
        return h('span', {
          'class': 'ovw-face ovw-face-role-' + (roleName ? agent.roleId : 'unknown'),
          title: (roleName || '角色未记录') + '：' + (slow || AGENT_STATES[state])
        }, [
          roleName ? roleName.charAt(0) : '?',
          h('i', { 'class': 'ovw-face-dot ovw-face-' + (state === 'problem' ? 'bad' : (slow ? 'slow' : state)) })
        ]);
      })));
      var lead = leadAgent(agents);
      var leadSlow = lead ? agentSlowOf(lead, snap.generatedAt) : null;
      var doing = lead
        ? (NC.ROLES[lead.roleId] ? NC.ROLES[lead.roleId] + '：' : '') + show(lead.activity, '正在干什么未记录')
        : '队员信息未记录';
      row.appendChild(h('div', {
        'class': 'ovw-c-doing' + (lead && lead.state === 'problem' ? ' ovw-doing-bad' : (leadSlow ? ' ovw-doing-slow' : '')),
        title: doing
      }, doing));
      row.appendChild(h('div', {
        'class': 'ovw-c-age ovw-age' + (quiet.tone ? ' ovw-age-' + quiet.tone : ''),
        'data-stale': quiet.tone === 'stale' ? 'true' : null,
        title: quiet.text
      }, quiet.text));
      row.appendChild(slot
        ? R.cell(h, R.statusView(slot.card, { slot: slot, hasDraft: draftOf(slot).trim() !== '' }), 'ovw-c-reply ovw-reply')
        : h('div', { 'class': 'ovw-c-reply ovw-reply', title: '还没有回复' }, [
          h('span', { 'class': 'ovw-reply-dot', 'aria-hidden': 'true' }), '—'
        ]));
      row.appendChild(h('button', {
        type: 'button',
        'class': 'ovw-more-btn',
        'aria-expanded': open ? 'true' : 'false',
        'aria-label': open ? '收起详情' : '展开详情',
        title: open ? '收起详情' : '展开详情',
        'data-action': 'runs-detail',
        'data-for': run.id
      }, '›'));
      wrap.appendChild(row);

      // 展开行：关卡、队伍每人在干什么、最近进展、工作包、替你做的决定、详情路径（收起时 hidden）
      var panel = h('div', { 'class': 'ovw-x cx-expand', hidden: !open });
      var side = h('div', { 'class': 'ovw-x-side' }, [h('div', { 'class': 'ovw-label' }, '关卡'), stageTrack(run)]);
      // 行内被截断的项目：事项、正在干什么在这里给全文（AC-40）
      var fullNames = h('div', { 'class': 'ovw-x-full' }, [
        h('div', { 'class': 'ovw-x-name' }, name),
        h('div', { 'class': 'ovw-small ovw-muted' }, '正在干什么：' + doing)
      ]);
      if (tag.label !== meta.label) {
        side.insertBefore(h('p', { 'class': 'ovw-small ovw-muted' }, '记录里的状态：' + meta.label), side.firstChild);
      }
      side.appendChild(h('div', { 'class': 'ovw-label' }, '队伍'));
      side.appendChild(agents.length
        ? h('div', { 'class': 'ovw-members' }, agents.map(function (agent) {
          return agentRow(run, agent);
        }))
        : h('div', { 'class': 'ovw-small ovw-muted' }, '队员信息未记录'));
      if (run.kind !== 'delivery') {
        side.appendChild(h('p', { 'class': 'ovw-note' }, '单个任务没有跟进 Agent，不会主动报卡住，这里只显示多久没变化。'));
      }
      (Array.isArray(run.diagnostics) ? run.diagnostics : []).forEach(function (diagnostic) {
        var message = isObj(diagnostic) ? show(diagnostic.message, null) : null;
        if (message) {
          side.appendChild(h('p', { 'class': 'ovw-diag' }, message));
        }
      });
      var main = details(run);
      main.insertBefore(fullNames, main.firstChild);
      if (slot) {
        side.insertBefore(compose(run, slot), side.firstChild);
      }
      panel.appendChild(main);
      panel.appendChild(side);
      wrap.appendChild(panel);
      return wrap;
    }

    /** 筛选数字和分组条数同源（groupOf）；状态未知只在「全部」里单独一组，没有筛选按钮。 */
    function counts() {
      var out = { all: runs.length, running: 0, waiting: 0, problem: 0, done: 0, unknown: 0 };
      runs.forEach(function (run) {
        out[groupOf(run)] += 1;
      });
      return out;
    }

    var GROUPS = [
      { key: 'waiting', label: '等你拍板', tone: 'red' },
      { key: 'problem', label: '出问题', tone: 'red' },
      { key: 'running', label: '进行中', tone: 'blue', note: '久未变化的在前' },
      { key: 'unknown', label: '状态未知', tone: 'gray', note: '记录里的状态认不出，原状态见展开行' },
      { key: 'done', label: '已通关', tone: 'green', note: '已通关的只看，默认折叠' }
    ];

    function render() {
      clear(container);
      var page = h('div', { 'class': 'ovw ovw-runs' });
      var n = counts();
      page.appendChild(h('div', { 'class': 'ovw-head' }, [
        h('h2', null, '进行中的副本'),
        h('div', { 'class': 'ovw-chips ovw-filters', role: 'group', 'aria-label': '按状态筛选' },
          FILTERS.map(function (def) {
            return chip(h, {
              'data-action': 'runs-filter',
              'data-value': def.key,
              'aria-label': def.label
            }, filter === def.key, [
              h('span', null, def.label),
              h('span', { 'class': 'ovw-chip-count' }, known ? String(n[def.key]) : UNKNOWN)
            ]);
          })),
        h('div', { 'class': 'ovw-legend ovw-small ovw-muted' }, [
          '关卡：',
          h('span', { 'class': 'ovw-stage ovw-stage-done' }, '绿 走过'),
          h('span', { 'class': 'ovw-stage ovw-stage-current' }, '蓝 当前'),
          h('span', { 'class': 'ovw-stage ovw-stage-problem' }, '红 出问题'),
          h('span', { 'class': 'ovw-age-stale' }, '橙 超过 30 分钟没变化')
        ])
      ]));
      page.appendChild(h('p', { 'class': 'ovw-sub' }, '所有派出去的任务走到了哪一关、谁在干什么、卡了多久。点开一行看最近进展和替你做的决定，也可以在展开行里回复；要拍板的处理在「待处理」。'));

      diagnosticsWithPrefix(snap, 'runs_').forEach(function (diagnostic) {
        page.appendChild(h('p', { 'class': 'ovw-diag' }, show(diagnostic.message, '运行记录有异常，按未知展示')));
      });

      var shown = runs.filter(function (run) {
        return filter === 'all' || groupOf(run) === filter;
      });
      if (!runs.length) {
        page.appendChild(h('p', { 'class': 'ovw-empty' }, known
          ? '还没有派出去的副本'
          : '运行记录读不到，副本未知（' + UNKNOWN + '）'));
      } else if (!shown.length) {
        page.appendChild(h('p', { 'class': 'ovw-empty' }, '这个筛选下没有副本'));
      } else {
        var table = h('div', { 'class': 'ovw-table' });
        table.appendChild(h('div', { 'class': 'ovw-th ovw-row-run', 'aria-hidden': 'true' }, [
          h('span', null, '状态'), h('span', null, '项目：事项'), h('span', null, '关卡'), h('span', null, '队伍'),
          h('span', null, '正在干什么'), h('span', { 'class': 'ovw-num' }, '没变化'), h('span', null, '回复'), h('span', null, '')
        ]));
        var body = h('div', { 'class': 'ovw-grid' });
        GROUPS.forEach(function (group) {
          var members = shown.filter(function (run) {
            return groupOf(run) === group.key;
          });
          if (!members.length) {
            return;
          }
          if (group.key === 'running' || group.key === 'unknown') {
            members = members.slice().sort(function (a, b) {
              var x = timeOf(a.lastChangedAt);
              var y = timeOf(b.lastChangedAt);
              if (!isFinite(x) || !isFinite(y)) {
                return isFinite(x) ? -1 : (isFinite(y) ? 1 : 0);
              }
              return x - y;
            });
          }
          var folded = group.key === 'done' && !doneOpen && filter !== 'done';
          var section = h('section', { 'class': 'ovw-group ovw-tone-' + group.tone, 'data-group': group.key });
          section.appendChild(h('div', { 'class': 'ovw-grp' }, [
            h('span', { 'class': 'ovw-dot', 'aria-hidden': 'true' }),
            h('h3', { 'class': 'ovw-grp-name' }, group.label),
            h('span', { 'class': 'ovw-grp-count' }, String(members.length)),
            group.note ? h('span', { 'class': 'ovw-grp-note' }, '· ' + group.note) : null,
            group.key === 'done' && filter !== 'done'
              ? h('button', {
                type: 'button',
                'class': 'ovw-grp-more',
                'data-action': 'runs-done-toggle',
                'aria-expanded': folded ? 'false' : 'true'
              }, folded ? '展开' : '收起')
              : null
          ]));
          members.forEach(function (run) {
            section.appendChild(runRow(run, group.key));
          });
          body.appendChild(section);
        });
        table.appendChild(body);
        page.appendChild(table);
      }
      container.appendChild(page);
      focusNote(container, focusAfter);
      focusAfter = null;
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
        var value = target.getAttribute('data-value');
        var id = target.getAttribute('data-for');
        if (action === 'runs-quick' || action === 'runs-reply-send' || action === 'runs-reply-copy') {
          var run = id !== null && own(byId, id) ? byId[id] : null;
          var slot = run ? slotOf(run) : null;
          if (!slot || slot.allowed !== true) {
            return;
          }
          var form = R.form(slot.card);
          if (action === 'runs-quick') {
            if (!NC.replyChoices(slot).some(function (choice) { return choice.id === value; })) {
              return;
            }
            var quick = form.quick === value ? null : value;
            R.setForm(slot.card, { quick: quick });
            // 要写字的评价（补充）点了就把光标放进输入框
            if (quick !== null && Array.isArray(slot.needText) && slot.needText.indexOf(quick) !== -1) {
              openId = id;
              focusAfter = id;
            }
            render();
          } else if (action === 'runs-reply-copy') {
            R.copy(slot.card, runView(run, slot, form.quick), form.quick, form.text, function (lines) {
              if (ui && typeof ui.openExport === 'function') {
                ui.openExport(lines);
              }
            });
          } else {
            target.setAttribute('disabled', '');
            R.send(slot.card, { card: slot.card, quick: form.quick, text: form.text },
              runView(run, slot, form.quick)).then(function () {
              view.request();
            });
          }
          return;
        }
        if (action === 'runs-filter') {
          if (!FILTERS.some(function (def) { return def.key === value; }) || filter === value) {
            return;
          }
          filter = value;
          render();
        } else if (action === 'runs-detail') {
          if (id === null || !own(byId, id)) {
            return;
          }
          openId = openId === id ? null : id;
          render();
        } else if (action === 'runs-done-toggle') {
          doneOpen = !doneOpen;
          render();
        } else if (action === 'runs-goto') {
          // 只跳快照里真实存在的关联；跳转会切页（本页随即 dispose），之后不再碰 DOM。
          var linked = Object.keys(byId).some(function (runId) {
            return gotoTargets(byId[runId]).some(function (entry) {
              return entry.id === id;
            });
          });
          if (linked && ui && typeof ui.navigate === 'function') {
            ui.navigate('pending', id);
          }
        }
      },
      input: function (event) {
        var target = event.target;
        if (!target || target.nodeType !== 1 || target.getAttribute('data-action') !== 'runs-note') {
          return;
        }
        var id = target.getAttribute('data-for');
        var slot = id !== null && own(byId, id) ? slotOf(byId[id]) : null;
        if (!slot || slot.allowed !== true) {
          return;
        }
        var value = typeof target.value === 'string' ? target.value : '';
        var hadDraft = draftOf(slot).trim() !== '';
        var form = R.setForm(slot.card, { text: value });
        R.refresh(container, slot.card, slot, form.quick, value);
        if (hadDraft !== (value.trim() !== '')) {
          view.request(); // 回复列「草稿 / —」跟着变；正在打字时等失焦再重绘
        }
      }
    });
  }

  NC.registerPage('runs', { mount: mountRuns });

  // ========================================================= 角色管理

  function executorsOf(role) {
    var seen = Object.create(null);
    return (Array.isArray(role.executors) ? role.executors : []).filter(function (executor) {
      if (typeof executor !== 'string' || !own(NC.EXECUTORS, executor) || seen[executor]) {
        return false;
      }
      seen[executor] = true;
      return true;
    });
  }

  /** 这名队员能选的操作：转正只给见习；换主装备只给装备里除默认外的；修改意见都可以写。 */
  function actionsOf(role) {
    var out = [];
    if (role.status === 'draft') {
      out.push({ action: 'activate', label: '转正（改为 active）' });
    }
    if (role.status === 'draft' || role.status === 'active') {
      executorsOf(role).slice(1).forEach(function (executor) {
        out.push({ action: 'executor', executor: executor, label: '换主装备 → ' + NC.EXECUTORS[executor] });
      });
    }
    out.push({ action: 'feedback', label: '写修改意见' });
    return out;
  }

  function validRolePick(role, pick) {
    if (!isObj(pick) || typeof pick.action !== 'string') {
      return false;
    }
    return actionsOf(role).some(function (option) {
      return option.action === pick.action && (option.action !== 'executor' || option.executor === pick.executor);
    });
  }

  /** 等级文案与经验条（4.7：Lv.1 0–2 · Lv.2 3–9 · Lv.3 10–29 · Lv.4 30+）；数据取快照，未知不画进度。 */
  function levelOf(role) {
    var level = countOf(role.level);
    var progress = isObj(role.levelProgress) ? role.levelProgress : null;
    if (level === null || level < 1 || level > 4 || !progress) {
      return null;
    }
    var current = countOf(progress.current);
    var span = countOf(progress.span);
    var remaining = countOf(progress.nextRemaining);
    if (current === null || span === null || span === 0) {
      return null;
    }
    if (level === 4) {
      return { text: 'Lv.4 · 已是最高等级', current: span, span: span };
    }
    if (remaining === null || remaining === 0) {
      return null;
    }
    return { text: 'Lv.' + level + ' · 再通关 ' + remaining + ' 次升级', current: current, span: span };
  }

  /** 一次通过：只算开发；有已知样本才显示百分比，否则「—」，不把缺证据当 0%。 */
  function firstPassOf(role) {
    var metric = isObj(role.firstPass) ? role.firstPass : null;
    if (role.id !== 'dev' || !metric) {
      return { value: UNKNOWN, sub: null };
    }
    var value = countOf(metric.value);
    var numerator = countOf(metric.numerator);
    var denominator = countOf(metric.denominator);
    if (value === null || denominator === null || denominator === 0 || numerator === null) {
      return { value: UNKNOWN, sub: show(metric.reason, null) };
    }
    return { value: value + '%', sub: numerator + ' / ' + denominator };
  }

  function mountRoles(container, snapshot, ui) {
    var doc = container.ownerDocument || root.document;
    var h = builder(doc);
    var snap = isObj(snapshot) ? snapshot : {};
    var picks = ensureBucket(ui, 'roles');
    var listed = uniqueItems(snap.roles);
    var roles = ROLE_ORDER.map(function (id) {
      return listed.filter(function (role) {
        return role.id === id;
      })[0] || null;
    }).filter(Boolean);
    var byId = Object.create(null);
    roles.forEach(function (role) {
      byId[role.id] = role;
    });
    var noticeEl = null;
    var R = reply();

    var openId = null; // 窄屏点开一行才显示操作；选「写修改意见」也会展开这一行

    function stat(value, label, sub) {
      return h('div', { 'class': 'ovw-stat ovw-num' }, [
        h('b', null, value),
        h('span', { 'class': 'ovw-sr' }, label),
        sub ? h('span', { 'class': 'ovw-stat-sub' }, sub) : null
      ]);
    }

    function chipsOf(list, empty, firstTag, limit) {
      if (!list.length) {
        return h('span', { 'class': 'ovw-small ovw-muted' }, empty);
      }
      var shown = typeof limit === 'number' ? list.slice(0, limit) : list;
      var nodes = shown.map(function (text, index) {
        return h('span', { 'class': 'ovw-tag' + (index === 0 && firstTag ? ' ovw-tag-blue' : '') }, [
          text,
          index === 0 && firstTag ? h('span', { 'class': 'ovw-default' }, firstTag) : null
        ]);
      });
      if (shown.length < list.length) {
        // 收起的技能也留在页面上（读屏和搜索能找到），只是不占行宽
        list.slice(shown.length).forEach(function (text) {
          nodes.push(h('span', { 'class': 'ovw-sr' }, text));
        });
        nodes.push(h('span', { 'class': 'ovw-more-count', 'aria-hidden': 'true' }, '+' + (list.length - shown.length)));
      }
      return h('div', { 'class': 'ovw-chips ovw-chips-tight', title: list.join('、') }, nodes);
    }

    /** 操作按钮：行内只写短字，aria-label / title 写完整说法。 */
    function shortLabel(option, alternatives) {
      if (option.action === 'activate') {
        return '转正';
      }
      if (option.action === 'feedback') {
        return '写意见';
      }
      return alternatives > 1 ? '换 ' + NC.EXECUTORS[option.executor] : '换主装备';
    }

    function roleRow(role) {
      var pick = picks[role.id];
      var chosen = validRolePick(role, pick);
      var known = role.status === 'draft' || role.status === 'active';
      var status = own(ROLE_STATUS, role.status) ? ROLE_STATUS[role.status] : { label: '状态未记录', tone: 'gray' };
      var active = metricOf(role.activeSessions);
      var completed = metricOf(role.completed);
      var level = levelOf(role);
      var name = show(role.name, NC.ROLES[role.id] + ' Agent');
      var feedbackOpen = chosen && pick.action === 'feedback';
      var open = openId === role.id; // 同时最多展开一行；意见草稿存在 picks 里，收起不丢
      var wrap = h('article', {
        'class': 'ovw-card ovw-item ovw-hero ovw-hero-' + status.tone + (chosen ? ' is-chosen' : '') + (open ? ' is-open' : ''),
        'data-item-id': role.id,
        'data-selected': chosen ? 'true' : null
      });
      var row = h('div', { 'class': 'ovw-row ovw-row-role', 'data-action': 'role-expand', 'data-for': role.id });

      row.appendChild(h('div', { 'class': 'ovw-c-who' }, [
        h('div', { 'class': 'ovw-frame' + (active !== null && active > 0 ? ' is-busy' : '') }, [
          avatarOf(ui, h, role.id),
          active !== null && active > 0
            ? h('span', { 'class': 'ovw-busy-dot', title: '正在出勤', 'aria-label': '正在出勤', role: 'img' })
            : null
        ]),
        h('h3', { 'class': 'ovw-name', title: name }, name),
        h('span', { 'class': 'ovw-tag ovw-tag-' + status.tone }, status.label),
        role.isNew === true ? h('span', { 'class': 'ovw-tag ovw-tag-violet' }, '新队员') : null
      ]));

      var levelBox = h('div', { 'class': 'ovw-c-level ovw-level' });
      if (level) {
        levelBox.appendChild(h('div', { 'class': 'ovw-lv' }, level.text));
        levelBox.appendChild(progressBar(h, level.current, level.span));
      } else {
        levelBox.appendChild(h('div', { 'class': 'ovw-lv' }, 'Lv.' + UNKNOWN));
        levelBox.appendChild(h('div', { 'class': 'ovw-small ovw-muted' },
          show(isObj(role.completed) ? role.completed.reason : null, '通关次数未知')));
      }
      row.appendChild(levelBox);

      var pass = firstPassOf(role);
      row.appendChild(stat(completed === null ? UNKNOWN : String(completed), '通关副本', null));
      row.appendChild(stat(pass.value, '一次通过', pass.sub));
      row.appendChild(stat(active === null ? UNKNOWN : String(active), '正在出勤', null));

      var executors = executorsOf(role);
      var skills = textList(role.skills);
      row.appendChild(h('div', { 'class': 'ovw-c-equip' }, [
        h('span', { 'class': 'ovw-sr' }, '装备'),
        chipsOf(executors.map(function (executor) {
          return NC.EXECUTORS[executor];
        }), '未记录', '默认')
      ]));
      row.appendChild(h('div', { 'class': 'ovw-c-skills' }, [
        h('span', { 'class': 'ovw-sr' }, '技能'),
        chipsOf(skills, known ? '第一次用到时再学' : '未记录', null, 2)
      ]));
      var useWhen = show(role.useWhen, '未记录');
      row.appendChild(h('div', { 'class': 'ovw-c-use', title: useWhen }, [
        h('span', { 'class': 'ovw-sr' }, '擅长'),
        useWhen
      ]));

      var options = actionsOf(role);
      var alternatives = options.filter(function (option) {
        return option.action === 'executor';
      }).length;
      row.appendChild(h('div', { 'class': 'ovw-c-ops ovw-chips', role: 'group', 'aria-label': '操作（选一个）' },
        options.map(function (option) {
          var on = chosen && pick.action === option.action &&
            (option.action !== 'executor' || pick.executor === option.executor);
          return chip(h, {
            'class': 'ovw-chip' + (on ? ' is-selected' : ''),
            'data-action': 'role-action',
            'data-for': role.id,
            'data-value': option.action,
            'data-executor': option.executor || null,
            'aria-label': option.label,
            title: option.label
          }, on, shortLabel(option, alternatives));
        })));
      wrap.appendChild(row);

      var panel = h('div', { 'class': 'ovw-x ovw-x-role cx-expand', hidden: !open });
      panel.appendChild(h('dl', { 'class': 'ovw-kv' }, [
        h('dt', null, '装备'),
        h('dd', null, executors.length ? executors.map(function (executor, index) {
          return NC.EXECUTORS[executor] + (index === 0 ? '（默认）' : '');
        }).join('、') : '未记录'),
        h('dt', null, '技能'),
        h('dd', null, skills.length ? skills.join('、') : (known ? '第一次用到时再学' : '未记录')),
        h('dt', null, '擅长'),
        h('dd', null, useWhen)
      ]));
      (Array.isArray(role.diagnostics) ? role.diagnostics : []).forEach(function (diagnostic) {
        var message = isObj(diagnostic) ? show(diagnostic.message, null) : null;
        if (message) {
          panel.appendChild(h('p', { 'class': 'ovw-diag' }, message));
        }
      });
      if (!known) {
        panel.appendChild(h('p', { 'class': 'ovw-small ovw-muted' }, '角色状态未记录：不能转正或换主装备，可以写修改意见'));
      }
      if (feedbackOpen) {
        var note = h('textarea', {
          'class': 'ovw-note',
          rows: '2',
          maxlength: String(NC.TEXT_LIMIT),
          placeholder: '修改意见，写一句',
          'aria-label': '修改意见',
          'data-action': 'role-note',
          'data-for': role.id
        });
        if (typeof pick.note === 'string') {
          note.value = pick.note;
        }
        panel.appendChild(note);
      }
      wrap.appendChild(panel);
      return wrap;
    }

    function render() {
      clear(container);
      var page = h('div', { 'class': 'ovw ovw-roles' });
      var formal = 0;
      var unknownStatus = 0;
      var busy = 0;
      var unknownBusy = 0;
      roles.forEach(function (role) {
        if (role.status === 'active') {
          formal += 1;
        } else if (role.status !== 'draft') {
          unknownStatus += 1;
        }
        var active = metricOf(role.activeSessions);
        if (active === null) {
          unknownBusy += 1;
        } else if (active > 0) {
          busy += 1;
        }
      });
      var total = NC.ROLE_IDS.length;
      var statusKnown = roles.length > 0 && unknownStatus < roles.length;
      var busyText = unknownBusy === roles.length ? UNKNOWN : String(busy) + (unknownBusy ? '（' + unknownBusy + ' 名未知）' : '');

      var goal = h('div', { 'class': 'ovw-goal' }, [
        h('span', { 'class': 'ovw-goal-label' }, '转正进度'),
        h('span', { 'class': 'ovw-goal-num' },
          '正式 ' + (statusKnown ? formal : UNKNOWN) + ' / ' + total + ' · 出勤中 ' + busyText),
        statusKnown ? progressBar(h, formal, total) : null
      ]);

      page.appendChild(h('div', { 'class': 'ovw-head' }, [
        h('h2', null, '我的队伍'),
        h('p', { 'class': 'ovw-sub' }, '等级按通关次数算，数字都来自运行记录；见习队员试用后由你确认转正'),
        goal
      ]));
      if (unknownStatus && statusKnown) {
        page.appendChild(h('p', { 'class': 'ovw-small ovw-muted' }, unknownStatus + ' 名状态未记录'));
      }

      diagnosticsWithPrefix(snap, 'roles_').forEach(function (diagnostic) {
        page.appendChild(h('p', { 'class': 'ovw-diag' }, show(diagnostic.message, '角色设定有异常，按未知展示')));
      });

      if (roles.length) {
        var table = h('div', { 'class': 'ovw-table' });
        table.appendChild(h('div', { 'class': 'ovw-th ovw-row-role', 'aria-hidden': 'true' }, [
          h('span', null, '队员'), h('span', null, '等级'), h('span', { 'class': 'ovw-num' }, '通关'),
          h('span', { 'class': 'ovw-num' }, '一次通过'), h('span', { 'class': 'ovw-num' }, '出勤'),
          h('span', null, '装备（第一个是默认）'), h('span', null, '技能'), h('span', null, '擅长'),
          h('span', null, '操作（选一个）')
        ]));
        table.appendChild(h('div', { 'class': 'ovw-team' }, roles.map(roleRow)));
        page.appendChild(table);
      } else {
        page.appendChild(h('p', { 'class': 'ovw-empty' }, '还没有读到任何角色设定'));
      }

      page.appendChild(h('p', { 'class': 'ovw-credit' },
        '形象：DiceBear「Pixel Art」（设计 CC0 1.0，代码 MIT），每个角色固定一个形象，离线内嵌。'));

      var picked = roles.filter(function (role) {
        return validRolePick(role, picks[role.id]);
      }).length;
      noticeEl = h('p', { 'class': 'ovw-notice', role: 'status' });
      if (channelOn()) {
        page.appendChild(sendDock(picked));
      } else {
        page.appendChild(h('div', { 'class': 'ovw-dock' }, [
          h('span', { 'class': 'ovw-dock-sum' }, picked ? '已选 ' + picked + ' 名队员' : '还没选操作'),
          noticeEl,
          h('span', { 'class': 'ovw-small ovw-muted ovw-dock-hint' }, '只包含选了操作的队员'),
          h('button', { type: 'button', 'class': 'ovw-primary', 'data-action': 'roles-export' }, '生成指令')
        ]));
      }
      container.appendChild(page);
    }

    /** 只把当前合法的选择交给 P0：旧选择 / 手工改值（比如给正式队员转正）不进入导出。 */
    function validPicks() {
      var valid = {};
      roles.forEach(function (role) {
        if (validRolePick(role, picks[role.id])) {
          valid[role.id] = picks[role.id];
        }
      });
      return valid;
    }

    /**
     * 通道页面的底部（PRD v1.1 4.7 第 6 项）：「发送已选（N）」把 v0 导出的角色指令作为一条消息发给主会话；
     * 发出后显示编号和回复状态，选择没变就不能再发（防重复执行）；页面过期时退回「生成指令」。
     */
    function sendDock(picked) {
      var lines = NC.exportLines('roles', roles, { roles: validPicks() });
      var form = R.form('roles');
      var state = R.local('roles');
      var sending = !!state && state.phase === 'sending';
      var sentNow = typeof form.sentSig === 'string' && form.sentSig === JSON.stringify(lines) && lines.length > 0;
      var tooMany = lines.length > NC.REPLY_LIMITS.maxLines;
      var sum = sentNow
        ? '已发给主会话 · 编号 ' + form.sentId
        : (picked ? '已选 ' + picked + ' 名队员的操作' : '还没选操作');
      var hint = '一条消息发给主会话，主会话照角色指令执行';
      if (picked > lines.length) {
        hint = (picked - lines.length) + ' 名不会发：修改意见没填，或含疑似敏感内容';
      } else if (tooMany) {
        hint = '一次最多发 ' + NC.REPLY_LIMITS.maxLines + ' 条，少选几名再发';
      }
      var button;
      if (R.canSend()) {
        button = h('button', {
          type: 'button', 'class': 'ovw-primary', 'data-action': 'roles-send',
          disabled: sending || sentNow || tooMany || !lines.length
        }, sending ? '发送中…' : '发送已选（' + lines.length + '）');
      } else {
        hint = R.stale()
          ? '通道重启过：生成指令复制回主会话，或刷新页面后重发'
          : '本地通道连不上：生成指令复制回主会话，重新连上后可以再发';
        button = h('button', { type: 'button', 'class': 'ovw-primary', 'data-action': 'roles-export' }, '生成指令');
      }
      var failed = !!state && state.phase === 'failed';
      if (failed && state.message) {
        noticeEl.textContent = state.message;
      }
      var records = R.records('roles');
      return h('div', { 'class': 'ovw-dock' }, [
        h('span', { 'class': 'ovw-dock-sum' }, sum),
        records.length || state ? R.cell(h, R.statusView('roles', {}), 'ovw-dock-reply') : null,
        noticeEl,
        h('span', { 'class': 'ovw-small ovw-muted ovw-dock-hint' }, hint),
        // 没发出去时可以退回复制指令（PRD v1.1 4.8：失败 → 重试 / 复制指令）
        failed && R.canSend() ? h('button', { type: 'button', 'class': 'cx-copy', 'data-action': 'roles-export' }, '复制指令') : null,
        button
      ]);
    }

    function sendNow() {
      var lines = NC.exportLines('roles', roles, { roles: validPicks() });
      var state = R.local('roles');
      if (!lines.length || lines.length > NC.REPLY_LIMITS.maxLines || (state && state.phase === 'sending')) {
        return;
      }
      var sig = JSON.stringify(lines);
      R.send('roles', { card: 'roles', quick: null, text: '', lines: lines },
        { page: 'roles', item: '指挥台：角色管理', association: '—' }).then(function (result) {
        if (result && result.ok) {
          R.setForm('roles', { sentSig: sig, sentId: result.id });
        }
        view.request();
      });
    }

    function exportNow() {
      var valid = validPicks();
      var wanted = Object.keys(valid).length;
      var lines = NC.exportLines('roles', roles, { roles: valid });
      if (ui && typeof ui.openExport === 'function') {
        ui.openExport(lines);
      }
      if (!noticeEl) {
        return;
      }
      if (!wanted) {
        noticeEl.textContent = '还没有给任何队员选操作，没有生成指令';
      } else if (lines.length < wanted) {
        noticeEl.textContent = '有 ' + (wanted - lines.length) + ' 名已选操作的队员没有导出：修改意见没填，或含疑似敏感内容';
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
        if (action === 'roles-export') {
          exportNow();
          return;
        }
        if (action === 'roles-send') {
          if (R && R.canSend()) {
            sendNow();
          }
          return;
        }
        if (action === 'role-expand') {
          var openFor = target.getAttribute('data-for');
          if (openFor === null || !own(byId, openFor)) {
            return;
          }
          openId = openId === openFor ? null : openFor;
          render();
          return;
        }
        if (action !== 'role-action') {
          return;
        }
        var id = target.getAttribute('data-for');
        var role = id !== null && own(byId, id) ? byId[id] : null;
        var next = { action: target.getAttribute('data-value') };
        if (next.action === 'executor') {
          next.executor = target.getAttribute('data-executor');
        }
        if (!role || !validRolePick(role, next)) {
          return;
        }
        var pick = isObj(picks[id]) ? picks[id] : {};
        if (validRolePick(role, pick) && pick.action === next.action &&
            (next.action !== 'executor' || pick.executor === next.executor)) {
          delete pick.action;
          delete pick.executor;
        } else {
          pick.action = next.action;
          if (next.action === 'executor') {
            pick.executor = next.executor;
          } else {
            delete pick.executor;
          }
          if (next.action === 'feedback') {
            openId = id; // 写意见要输入框：展开这一行（别的行随之收起）
          }
        }
        if (own(pick, 'action') || own(pick, 'note')) {
          picks[id] = pick;
        } else {
          delete picks[id];
        }
        render();
      },
      input: function (event) {
        var target = event.target;
        if (!target || target.nodeType !== 1 || target.getAttribute('data-action') !== 'role-note') {
          return;
        }
        var id = target.getAttribute('data-for');
        if (id === null || !own(byId, id)) {
          return;
        }
        var value = typeof target.value === 'string' ? target.value : '';
        var pick = isObj(picks[id]) ? picks[id] : {};
        if (value.trim() === '') {
          delete pick.note;
        } else {
          pick.note = value;
        }
        if (own(pick, 'action') || own(pick, 'note')) {
          picks[id] = pick;
        } else {
          delete picks[id];
        }
      }
    });
  }

  NC.registerPage('roles', { mount: mountRoles });
}(typeof window !== 'undefined' ? window : globalThis));
