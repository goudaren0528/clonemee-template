'use strict';

/*
 * P3：待处理 / 派发确认 两页的行为测试（node:test + assert + vm，无 npm 依赖）。
 *
 * 被测对象：tools/console_assets/actions.js（真实）加载在 tools/console_assets/core.js（P0，只读）之上。
 * 数据：只调用 P0 的 tests/console_support.py::snapshot_fixture 生成虚构快照（python -B），
 *       不读、不改任何真实记录，也不复制 runs/、plans/daily/。
 *
 * DOM 契约（来自 impl-plan「P2 提供 data-page / data-item-id / data-action 稳定定位」与
 * PRD 4.3–4.4 的可见文案；薄 DOM 不能证明真实浏览器布局、剪贴板或链接可用性）：
 *   1. 页面 mount(container, snapshot, ui) 返回 { dispose() }，并注册到 AssistantConsole.getPage('pending'|'dispatch')。
 *   2. 每张卡片根节点带 data-item-id，值等于快照里的 Pending.id / DispatchTask.id。
 *   3. 决定、角色、执行器、地点、组队按钮用可见文案即可点选：
 *      「A 继续原范围」「验收通过」「打回」「稍后」「产品」「Claude Code」「新建工作区」
 *      「组队」「已组队」（v1.1 P6a 前叫「进入副本」）「生成指令」「全部出发 · 生成指令」「今日关卡」「今日出发」。
 *      本测试按“文案 + 冒泡点击”定位，因此实现用 data-action 或原生控件都可以，不要求新增测试 API。
 *   4. 补充输入是卡片内的 textarea / input[type=text]；「已自动处理」用 details 折叠。
 *   5. 选择状态只经 ui.selection 传递，导出只经 ui.openExport(lines) 传出。
 *
 * 断言只依赖上述可见契约与 P0 导出的精确文本；一旦 actions.js 的实际 DOM 命名不同，
 * 失败信息会列出可见交互元素，便于对接（见交付报告「待对接差异」）。
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');

const ROOT = path.join(__dirname, '..');
const CORE_PATH = path.join(ROOT, 'tools', 'console_assets', 'core.js');
const ACTIONS_PATH = path.join(ROOT, 'tools', 'console_assets', 'actions.js');
const CSS_PATH = path.join(ROOT, 'tools', 'console_assets', 'actions.css');

const CORE_SOURCE = fs.readFileSync(CORE_PATH, 'utf8');
// v1.1 P6b：和正式页面一样在页面脚本之前加载 channel.js、reply.js（没有通道节点时是本机文件模式）
const CHANNEL_PATH = path.join(ROOT, 'tools', 'console_assets', 'channel.js');
const REPLY_PATH = path.join(ROOT, 'tools', 'console_assets', 'reply.js');
const CHANNEL_SOURCE = fs.readFileSync(CHANNEL_PATH, 'utf8');
const REPLY_SOURCE = fs.readFileSync(REPLY_PATH, 'utf8');
const ACTIONS_EXISTS = fs.existsSync(ACTIONS_PATH);
const CSS_EXISTS = fs.existsSync(CSS_PATH);
const ACTIONS_SOURCE = ACTIONS_EXISTS ? fs.readFileSync(ACTIONS_PATH, 'utf8') : null;
const ACTIONS_SKIP = ACTIONS_EXISTS
  ? false
  : 'tools/console_assets/actions.js 尚未实现：接口对接待 P3 owner 落地后验证';
const CSS_SKIP = CSS_EXISTS
  ? false
  : 'tools/console_assets/actions.css 尚未实现：静态样式约束待 P3 owner 落地后验证';

/** actions.js 未落地时登记为 skip（不是 pass），避免把未执行写成通过。 */
function pageTest(name, fn) {
  if (ACTIONS_EXISTS) {
    test(name, fn);
  } else {
    test(name, { skip: ACTIONS_SKIP }, () => {});
  }
}

// ---------------------------------------------------------------- P0 夹具

function runPython(script, stdin) {
  const result = spawnSync(process.env.PYTHON || 'python', ['-B', '-c', script], {
    cwd: ROOT,
    encoding: 'utf8',
    input: stdin,
    env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }),
    windowsHide: true,
    timeout: 60000
  });
  if (result.error || result.status !== 0) {
    assert.fail(
      'python -B 夹具导出失败：' +
      (result.error ? result.error.message : '') +
      (result.stderr ? '\n' + result.stderr : '')
    );
  }
  return result.stdout;
}

let SNAPSHOT_CACHE = null;

/** 一次性从 P0 取回全部场景；测试内的期望值仍然手写，不用快照反推。 */
function allSnapshots() {
  if (SNAPSHOT_CACHE) {
    return SNAPSHOT_CACHE;
  }
  const scenarios = ['full-orca', 'full-local', 'empty', 'missing', 'conflict', 'hostile'];
  const script = [
    'import json,sys',
    "sys.path.insert(0,'tests')",
    'from console_support import snapshot_fixture',
    'names=json.loads(sys.stdin.read())',
    'print(json.dumps({n: snapshot_fixture(scenario=n) for n in names}, ensure_ascii=False))'
  ].join('\n');
  SNAPSHOT_CACHE = JSON.parse(runPython(script, JSON.stringify(scenarios)));
  return SNAPSHOT_CACHE;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function snap(scenario) {
  const all = allSnapshots();
  assert.ok(all[scenario], '缺少 P0 场景：' + scenario);
  return clone(all[scenario]);
}

// ------------------------------------------------------- 轻薄 DOM 实现

const BOOLEAN_PROPS = ['hidden', 'open', 'disabled', 'checked', 'selected', 'required', 'readOnly', 'multiple'];

function createStyle() {
  const style = {};
  Object.defineProperty(style, 'setProperty', {
    value(name, value) {
      style[name] = value === null || value === undefined ? '' : String(value);
    },
    enumerable: false
  });
  Object.defineProperty(style, 'getPropertyValue', {
    value(name) {
      return style[name] === undefined ? '' : style[name];
    },
    enumerable: false
  });
  Object.defineProperty(style, 'removeProperty', {
    value(name) {
      const old = style[name];
      delete style[name];
      return old === undefined ? '' : old;
    },
    enumerable: false
  });
  return style;
}

function createClassList(element) {
  function read() {
    const raw = element.getAttribute('class');
    return raw ? raw.split(/\s+/).filter(Boolean) : [];
  }
  function write(list) {
    element.setAttribute('class', list.join(' '));
  }
  return {
    add() {
      const list = read();
      for (let i = 0; i < arguments.length; i += 1) {
        const name = String(arguments[i]);
        if (list.indexOf(name) === -1) {
          list.push(name);
        }
      }
      write(list);
    },
    remove() {
      const drop = Array.prototype.slice.call(arguments).map(String);
      write(read().filter(function (name) {
        return drop.indexOf(name) === -1;
      }));
    },
    toggle(name, force) {
      const key = String(name);
      const has = this.contains(key);
      if (force === true || (force === undefined && !has)) {
        this.add(key);
        return true;
      }
      this.remove(key);
      return false;
    },
    contains(name) {
      return read().indexOf(String(name)) !== -1;
    },
    item(index) {
      return read()[index] || null;
    },
    get length() {
      return read().length;
    },
    get value() {
      return read().join(' ');
    },
    toString() {
      return read().join(' ');
    }
  };
}

function createDataset(element) {
  return new Proxy({}, {
    get(target, key) {
      if (typeof key !== 'string') {
        return undefined;
      }
      const name = 'data-' + key.replace(/[A-Z]/g, function (letter) {
        return '-' + letter.toLowerCase();
      });
      const value = element.getAttribute(name);
      return value === null ? undefined : value;
    },
    set(target, key, value) {
      const name = 'data-' + String(key).replace(/[A-Z]/g, function (letter) {
        return '-' + letter.toLowerCase();
      });
      element.setAttribute(name, value === null || value === undefined ? '' : String(value));
      return true;
    },
    has(target, key) {
      return typeof key === 'string' && element.hasAttribute(
        'data-' + key.replace(/[A-Z]/g, function (letter) {
          return '-' + letter.toLowerCase();
        })
      );
    },
    deleteProperty(target, key) {
      element.removeAttribute('data-' + String(key).replace(/[A-Z]/g, function (letter) {
        return '-' + letter.toLowerCase();
      }));
      return true;
    },
    ownKeys() {
      return element.getAttributeNames()
        .filter(function (name) { return name.indexOf('data-') === 0; })
        .map(function (name) {
          return name.slice(5).replace(/-([a-z])/g, function (_all, letter) {
            return letter.toUpperCase();
          });
        });
    },
    getOwnPropertyDescriptor() {
      return { enumerable: true, configurable: true };
    }
  });
}

function createEvent(type, init) {
  return Object.assign({
    type: type,
    bubbles: true,
    cancelable: true,
    defaultPrevented: false,
    target: null,
    currentTarget: null,
    _stop: false,
    preventDefault: function () {
      this.defaultPrevented = true;
    },
    stopPropagation: function () {
      this._stop = true;
    }
  }, init || {});
}

class TextNode {
  constructor(value, doc) {
    this.nodeType = 3;
    this.nodeName = '#text';
    this.ownerDocument = doc;
    this.parentNode = null;
    this.childNodes = [];
    this.data = value === null || value === undefined ? '' : String(value);
  }

  get textContent() {
    return this.data;
  }

  set textContent(value) {
    this.data = value === null || value === undefined ? '' : String(value);
  }

  get nodeValue() {
    return this.data;
  }

  set nodeValue(value) {
    this.data = value === null || value === undefined ? '' : String(value);
  }

  cloneNode() {
    return new TextNode(this.data, this.ownerDocument);
  }
}

class Element {
  constructor(tag, doc) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.nodeName = this.tagName;
    this.ownerDocument = doc;
    this.parentNode = null;
    this.childNodes = [];
    this._attrs = Object.create(null);
    this._listeners = new Map();
    this._value = undefined;
    this.style = createStyle();
    this.classList = createClassList(this);
    this.dataset = createDataset(this);
  }

  // ---- 属性
  get id() {
    return this.getAttribute('id') || '';
  }

  set id(value) {
    this.setAttribute('id', value);
  }

  get className() {
    return this.getAttribute('class') || '';
  }

  set className(value) {
    this.setAttribute('class', value);
  }

  get attributes() {
    const names = Object.keys(this._attrs);
    return names.map(function (name) {
      return { name: name, value: this._attrs[name] };
    }, this);
  }

  setAttribute(name, value) {
    const key = String(name).toLowerCase();
    const text = value === null || value === undefined ? '' : String(value);
    this._attrs[key] = text;
    if (key === 'value') {
      this._value = text;
    }
  }

  getAttribute(name) {
    const key = String(name).toLowerCase();
    return Object.prototype.hasOwnProperty.call(this._attrs, key) ? this._attrs[key] : null;
  }

  getAttributeNames() {
    return Object.keys(this._attrs);
  }

  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this._attrs, String(name).toLowerCase());
  }

  removeAttribute(name) {
    const key = String(name).toLowerCase();
    delete this._attrs[key];
  }

  toggleAttribute(name, force) {
    const key = String(name).toLowerCase();
    const has = this.hasAttribute(key);
    if (force === true || (force === undefined && !has)) {
      this.setAttribute(key, '');
      return true;
    }
    this.removeAttribute(key);
    return false;
  }

  // ---- 反射到属性的布尔/值属性
  get value() {
    if (this._value !== undefined) {
      return this._value;
    }
    const attr = this.getAttribute('value');
    if (attr !== null) {
      return attr;
    }
    return this.tagName === 'TEXTAREA' ? this.textContent : '';
  }

  set value(value) {
    this._value = value === null || value === undefined ? '' : String(value);
    if (this.tagName === 'TEXTAREA') {
      this.textContent = this._value;
    }
  }

  get type() {
    const attr = this.getAttribute('type');
    if (attr !== null) {
      return attr;
    }
    return this.tagName === 'INPUT' ? 'text' : '';
  }

  set type(value) {
    this.setAttribute('type', value);
  }

  get name() {
    return this.getAttribute('name') || '';
  }

  set name(value) {
    this.setAttribute('name', value);
  }

  // ---- 树
  get children() {
    return this.childNodes.filter(function (node) {
      return node.nodeType === 1;
    });
  }

  get firstChild() {
    return this.childNodes[0] || null;
  }

  get lastChild() {
    return this.childNodes[this.childNodes.length - 1] || null;
  }

  get nextSibling() {
    if (!this.parentNode) {
      return null;
    }
    const siblings = this.parentNode.childNodes;
    const index = siblings.indexOf(this);
    return index === -1 ? null : siblings[index + 1] || null;
  }

  get previousSibling() {
    if (!this.parentNode) {
      return null;
    }
    const siblings = this.parentNode.childNodes;
    const index = siblings.indexOf(this);
    return index <= 0 ? null : siblings[index - 1] || null;
  }

  get textContent() {
    return this.childNodes.map(function (node) {
      return node.textContent;
    }).join('');
  }

  set textContent(value) {
    this.childNodes.forEach(function (node) {
      node.parentNode = null;
    });
    this.childNodes = [];
    const text = value === null || value === undefined ? '' : String(value);
    if (text !== '') {
      this.appendChild(new TextNode(text, this.ownerDocument));
    }
  }

  get innerText() {
    return this.textContent;
  }

  set innerText(value) {
    this.textContent = value;
  }

  appendChild(node) {
    if (!node || (node.nodeType !== 1 && node.nodeType !== 3 && node.nodeType !== 11)) {
      return node;
    }
    if (node.nodeType === 11) {
      Array.from(node.childNodes).forEach(function (child) {
        this.appendChild(child);
      }, this);
      return node;
    }
    if (node.parentNode) {
      node.parentNode.removeChild(node);
    }
    node.parentNode = this;
    this.childNodes.push(node);
    return node;
  }

  insertBefore(node, reference) {
    if (!reference) {
      return this.appendChild(node);
    }
    if (node.parentNode) {
      node.parentNode.removeChild(node);
    }
    const index = this.childNodes.indexOf(reference);
    node.parentNode = this;
    if (index === -1) {
      this.childNodes.push(node);
    } else {
      this.childNodes.splice(index, 0, node);
    }
    return node;
  }

  removeChild(node) {
    const index = this.childNodes.indexOf(node);
    if (index !== -1) {
      this.childNodes.splice(index, 1);
      node.parentNode = null;
    }
    return node;
  }

  replaceChild(node, reference) {
    this.insertBefore(node, reference);
    return this.removeChild(reference);
  }

  replaceChildren() {
    this.childNodes.forEach(function (node) {
      node.parentNode = null;
    });
    this.childNodes = [];
    Array.prototype.slice.call(arguments).forEach(function (node) {
      this.appendChild(coerceNode(node, this.ownerDocument));
    }, this);
  }

  append() {
    Array.prototype.slice.call(arguments).forEach(function (node) {
      this.appendChild(coerceNode(node, this.ownerDocument));
    }, this);
    return this;
  }

  prepend() {
    const nodes = Array.prototype.slice.call(arguments).map(function (node) {
      return coerceNode(node, this.ownerDocument);
    }, this);
    const first = this.childNodes[0] || null;
    nodes.forEach(function (node) {
      this.insertBefore(node, first);
    }, this);
    return this;
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }

  cloneNode(deep) {
    const copy = new Element(this.tagName.toLowerCase(), this.ownerDocument);
    const self = this;
    Object.keys(this._attrs).forEach(function (name) {
      copy.setAttribute(name, self._attrs[name]);
    });
    copy._value = this._value;
    if (deep) {
      this.childNodes.forEach(function (child) {
        copy.appendChild(child.cloneNode(true));
      });
    }
    return copy;
  }

  // ---- 查询
  matches(selector) {
    return matchesSelector(this, selector);
  }

  closest(selector) {
    let node = this;
    while (node && node.nodeType === 1) {
      if (matchesSelector(node, selector)) {
        return node;
      }
      node = node.parentNode;
    }
    return null;
  }

  querySelector(selector) {
    return querySelectorAll(this, selector)[0] || null;
  }

  querySelectorAll(selector) {
    return querySelectorAll(this, selector);
  }

  // ---- 事件
  addEventListener(type, listener) {
    if (typeof listener !== 'function' && (!listener || typeof listener.handleEvent !== 'function')) {
      return;
    }
    const key = String(type);
    if (!this._listeners.has(key)) {
      this._listeners.set(key, []);
    }
    const list = this._listeners.get(key);
    if (list.indexOf(listener) === -1) {
      list.push(listener);
    }
  }

  removeEventListener(type, listener) {
    const key = String(type);
    const list = this._listeners.get(key);
    if (!list) {
      return;
    }
    const index = list.indexOf(listener);
    if (index !== -1) {
      list.splice(index, 1);
    }
  }

  dispatchEvent(event) {
    const evt = event || createEvent('event');
    if (!evt.target) {
      evt.target = this;
    }
    const path = [];
    let node = this;
    while (node) {
      path.push(node);
      node = node.parentNode;
    }
    if (this.ownerDocument) {
      path.push(this.ownerDocument);
    }
    for (let i = 0; i < path.length; i += 1) {
      const current = path[i];
      evt.currentTarget = current;
      const list = current._listeners && current._listeners.get(evt.type);
      if (list) {
        list.slice().forEach(function (listener) {
          if (typeof listener === 'function') {
            listener.call(current, evt);
          } else {
            listener.handleEvent(evt);
          }
        });
      }
      const prop = current['on' + evt.type];
      if (typeof prop === 'function') {
        prop.call(current, evt);
      }
      if (evt._stop) {
        break;
      }
    }
    evt.currentTarget = null;
    return !evt.defaultPrevented;
  }

  click() {
    const isInput = this.tagName === 'INPUT';
    const isCheckable = isInput && (this.type === 'radio' || this.type === 'checkbox');
    if (isCheckable) {
      if (this.type === 'radio') {
        uncheckRadioGroup(this);
        this.setAttribute('checked', '');
      } else {
        this.toggleAttribute('checked');
      }
    }
    this.dispatchEvent(createEvent('click'));
    if (isCheckable) {
      this.dispatchEvent(createEvent('input'));
      this.dispatchEvent(createEvent('change'));
    }
    return true;
  }

  focus() {}
  blur() {}
  scrollIntoView() {}
  setSelectionRange() {}
  select() {}
  getBoundingClientRect() {
    return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
  }
}

BOOLEAN_PROPS.forEach(function (prop) {
  const name = prop.toLowerCase();
  Object.defineProperty(Element.prototype, prop, {
    configurable: true,
    enumerable: false,
    get: function () {
      return this.hasAttribute(name);
    },
    set: function (value) {
      if (value) {
        this.setAttribute(name, '');
      } else {
        this.removeAttribute(name);
      }
    }
  });
});

function uncheckRadioGroup(input) {
  const name = input.name;
  if (!name) {
    return;
  }
  let root = input;
  while (root.parentNode) {
    root = root.parentNode;
  }
  querySelectorAll(root, 'input[type="radio"]').forEach(function (candidate) {
    if (candidate !== input && candidate.name === name) {
      candidate.removeAttribute('checked');
    }
  });
}

function coerceNode(value, doc) {
  if (value && (value.nodeType === 1 || value.nodeType === 3 || value.nodeType === 11)) {
    return value;
  }
  return new TextNode(value, doc);
}

function createDocument() {
  const doc = {
    nodeType: 9,
    nodeName: '#document',
    createElement: function (tag) {
      return new Element(tag, doc);
    },
    createTextNode: function (value) {
      return new TextNode(value, doc);
    },
    createDocumentFragment: function () {
      const fragment = new Element('#fragment', doc);
      fragment.nodeType = 11;
      fragment.nodeName = '#document-fragment';
      return fragment;
    },
    addEventListener: function () {},
    removeEventListener: function () {},
    querySelector: function (selector) {
      return querySelectorAll(doc.documentElement, selector)[0] || null;
    },
    querySelectorAll: function (selector) {
      return querySelectorAll(doc.documentElement, selector);
    }
  };
  doc.documentElement = new Element('html', doc);
  doc.head = new Element('head', doc);
  doc.body = new Element('body', doc);
  doc.documentElement.appendChild(doc.head);
  doc.documentElement.appendChild(doc.body);
  return doc;
}

// ---- 选择器（够用的子集：tag / #id / .class / [attr] / [attr="v"]，后代与 > 组合）
const COMPOUND_RE = /([a-zA-Z][\w-]*|\*)|#([\w-]+)|\.([\w-]+)|\[\s*([\w:-]+)\s*(?:([~^$*|]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*))\s*)?\]/g;

function matchesCompound(element, compound) {
  const text = String(compound).trim();
  if (text === '' || text === '*') {
    return true;
  }
  COMPOUND_RE.lastIndex = 0;
  let consumed = 0;
  let ok = true;
  let match;
  while ((match = COMPOUND_RE.exec(text))) {
    if (match.index !== consumed) {
      return false;
    }
    consumed = COMPOUND_RE.lastIndex;
    if (match[1]) {
      if (match[1] !== '*' && element.tagName !== match[1].toUpperCase()) {
        ok = false;
      }
    } else if (match[2]) {
      if (element.getAttribute('id') !== match[2]) {
        ok = false;
      }
    } else if (match[3]) {
      if (!element.classList.contains(match[3])) {
        ok = false;
      }
    } else if (match[4]) {
      const actual = element.getAttribute(match[4]);
      if (actual === null) {
        ok = false;
      } else if (match[5]) {
        const expected = match[6] !== undefined ? match[6]
          : (match[7] !== undefined ? match[7] : match[8]);
        if (match[5] === '=' && actual !== expected) {
          ok = false;
        } else if (match[5] === '^=' && actual.indexOf(expected) !== 0) {
          ok = false;
        } else if (match[5] === '$=' && actual.slice(-expected.length) !== expected) {
          ok = false;
        } else if (match[5] === '*=' && actual.indexOf(expected) === -1) {
          ok = false;
        }
      }
    }
    if (!ok) {
      return false;
    }
  }
  return consumed === text.length && ok;
}

function matchesSelector(element, selector) {
  const groups = String(selector).split(',');
  for (let i = 0; i < groups.length; i += 1) {
    const group = groups[i].trim();
    if (!group) {
      continue;
    }
    if (/\s|>/.test(group)) {
      let root = element;
      while (root.parentNode) {
        root = root.parentNode;
      }
      if (querySelectorAll(root, group).indexOf(element) !== -1) {
        return true;
      }
    } else if (matchesCompound(element, group)) {
      return true;
    }
  }
  return false;
}

function descendantsOf(node) {
  const out = [];
  const stack = node.childNodes.slice().reverse();
  while (stack.length) {
    const current = stack.pop();
    if (current.nodeType !== 1) {
      continue;
    }
    out.push(current);
    const kids = current.childNodes;
    for (let i = kids.length - 1; i >= 0; i -= 1) {
      stack.push(kids[i]);
    }
  }
  return out;
}

function querySelectorAll(root, selector) {
  const groups = String(selector).split(',');
  const out = [];
  groups.forEach(function (raw) {
    const group = raw.trim();
    if (!group) {
      return;
    }
    const tokens = group.replace(/\s*>\s*/g, ' > ').split(/\s+/).filter(Boolean);
    let current = [root];
    let childMode = false;
    tokens.forEach(function (token) {
      if (token === '>') {
        childMode = true;
        return;
      }
      const next = [];
      current.forEach(function (base) {
        const pool = childMode ? base.children : descendantsOf(base);
        pool.forEach(function (candidate) {
          if (matchesCompound(candidate, token)) {
            next.push(candidate);
          }
        });
      });
      current = next.filter(function (element, index) {
        return next.indexOf(element) === index;
      });
      childMode = false;
    });
    current.forEach(function (element) {
      if (out.indexOf(element) === -1) {
        out.push(element);
      }
    });
  });
  return out;
}

// ----------------------------------------------------------- core/actions 装载

/**
 * 装载 core → channel → reply → 页面脚本（和 console_render 的 SCRIPT_ORDER 一致）。
 * options.channel：写进页面的通道节点（v1.1 通道模式）；options.fetch：假的 fetch；options.bare：只装 core + 页面（v0 形状）。
 */
function loadPages(options) {
  const opts = options || {};
  const sandbox = {};
  vm.createContext(sandbox);
  sandbox.window = sandbox;
  sandbox.document = createDocument();
  if (opts.channel) {
    const node = sandbox.document.createElement('script');
    node.setAttribute('id', 'console-channel');
    node.textContent = JSON.stringify(opts.channel);
    const realGet = sandbox.document.getElementById;
    sandbox.document.getElementById = function (id) {
      return id === 'console-channel' ? node : (typeof realGet === 'function' ? realGet.call(sandbox.document, id) : null);
    };
  }
  if (opts.fetch) {
    sandbox.fetch = opts.fetch;
  }
  sandbox.Promise = Promise;
  sandbox.console = { log: function () {}, warn: function () {}, error: function () {} };
  sandbox.setTimeout = function (fn) { return setTimeout(fn, 0); };
  sandbox.clearTimeout = clearTimeout;
  sandbox.Intl = Intl;
  sandbox.URL = URL;
  sandbox.URLSearchParams = URLSearchParams;

  vm.runInContext(CORE_SOURCE, sandbox, { filename: CORE_PATH });
  assert.ok(sandbox.AssistantConsole, 'core.js 未暴露 AssistantConsole');
  if (!opts.bare) {
    vm.runInContext(CHANNEL_SOURCE, sandbox, { filename: CHANNEL_PATH });
    vm.runInContext(REPLY_SOURCE, sandbox, { filename: REPLY_PATH });
  }
  try {
    vm.runInContext(ACTIONS_SOURCE, sandbox, { filename: ACTIONS_PATH });
  } catch (error) {
    assert.fail('actions.js 装载失败：' + (error && error.message));
  }
  return { NC: sandbox.AssistantConsole, doc: sandbox.document, sandbox: sandbox };
}

function createUi(doc, options) {
  const opts = options || {};
  const ui = {
    selection: opts.selection || { pending: {}, dispatch: {}, roles: {} },
    exportCalls: [],
    navigateCalls: [],
    avatarCalls: [],
    el: function (tag, attributes, text) {
      const element = doc.createElement(tag);
      if (attributes && typeof attributes === 'object') {
        Object.keys(attributes).forEach(function (key) {
          const value = attributes[key];
          if (value === null || value === undefined || value === false) {
            return;
          }
          if (key === 'dataset' && value && typeof value === 'object') {
            Object.keys(value).forEach(function (dataKey) {
              element.setAttribute(
                'data-' + dataKey.replace(/[A-Z]/g, function (letter) {
                  return '-' + letter.toLowerCase();
                }),
                String(value[dataKey])
              );
            });
            return;
          }
          if (key === 'style' && value && typeof value === 'object') {
            Object.assign(element.style, value);
            return;
          }
          if (key === 'class' || key === 'className') {
            element.setAttribute('class', String(value));
            return;
          }
          if (key === 'value') {
            element.value = value;
            element.setAttribute('value', String(value));
            return;
          }
          if (BOOLEAN_PROPS.indexOf(key) !== -1) {
            element[key] = Boolean(value);
            return;
          }
          if (typeof value === 'function') {
            // 契约禁止 ui.el 传 on* 处理器；这里静默忽略而不是执行。
            return;
          }
          element.setAttribute(key, String(value));
        });
      }
      if (text !== null && text !== undefined && text !== false) {
        if (Array.isArray(text)) {
          text.forEach(function (item) {
            element.appendChild(coerceNode(item, doc));
          });
        } else {
          element.appendChild(coerceNode(text, doc));
        }
      }
      return element;
    },
    avatar: function (roleId) {
      ui.avatarCalls.push(roleId === null || roleId === undefined ? null : String(roleId));
      const node = doc.createElement('span');
      node.setAttribute('class', 'avatar');
      node.setAttribute('data-avatar', roleId === null || roleId === undefined ? 'unknown' : String(roleId));
      node.setAttribute('aria-hidden', 'true');
      return node;
    },
    openExport: function (lines) {
      // 跨 vm realm：不能把 vm 的 Array 直接存进来，否则 deepStrictEqual 会因原型不同失败。
      const copy = [];
      if (Array.isArray(lines)) {
        for (let i = 0; i < lines.length; i += 1) {
          copy.push(String(lines[i]));
        }
      }
      ui.exportCalls.push(copy);
    },
    navigate: function (pageId, itemId) {
      ui.navigateCalls.push([pageId, itemId]);
    }
  };
  return ui;
}

function mountPage(snapshot, pageId, options) {
  const opts = options || {};
  const context = opts.context || loadPages();
  const doc = context.doc;
  const container = opts.container || doc.createElement('main');
  if (!container.getAttribute('data-page')) {
    container.setAttribute('data-page', pageId);
  }
  const ui = opts.ui || createUi(doc, opts.uiOptions);
  const page = context.NC.getPage(pageId);
  assert.ok(
    page && typeof page.mount === 'function',
    'AssistantConsole.getPage("' + pageId + '") 未注册 mount：actions.js 必须注册 pending 与 dispatch'
  );
  const instance = page.mount(container, snapshot, ui);
  assert.ok(
    instance && typeof instance.dispose === 'function',
    pageId + '.mount 必须返回 { dispose() }，以便切页不重复绑定'
  );
  return {
    NC: context.NC,
    doc: doc,
    container: container,
    ui: ui,
    instance: instance,
    context: context,
    dispose: function () {
      instance.dispose();
    }
  };
}

// --------------------------------------------------------------- 查询助手

function normText(value) {
  return String(value === null || value === undefined ? '' : value).replace(/\s+/g, ' ').trim();
}

/** 卡片可见文本 + title/aria-label/href/value 等承载文案的属性。 */
function surfaceText(element) {
  const parts = [normText(element.textContent)];
  [element].concat(descendantsOf(element)).forEach(function (node) {
    ['title', 'aria-label', 'data-label', 'href', 'value', 'alt', 'placeholder'].forEach(function (attr) {
      const value = node.getAttribute && node.getAttribute(attr);
      if (value) {
        parts.push(normText(value));
      }
    });
  });
  return parts.join(' ');
}

function isInteractive(element) {
  const tag = element.tagName;
  if (['BUTTON', 'A', 'INPUT', 'LABEL', 'SUMMARY', 'SELECT', 'TEXTAREA', 'OPTION'].indexOf(tag) !== -1) {
    return true;
  }
  if (element.hasAttribute('data-action') || element.hasAttribute('tabindex')) {
    return true;
  }
  const role = element.getAttribute('role');
  return role !== null && ['button', 'radio', 'checkbox', 'tab', 'option', 'menuitem', 'switch'].indexOf(role) !== -1;
}

function describeInteractives(root) {
  const seen = [];
  [root].concat(descendantsOf(root)).forEach(function (element) {
    if (!isInteractive(element)) {
      return;
    }
    const text = normText(element.textContent) || element.getAttribute('data-action') || element.tagName;
    seen.push(
      element.tagName.toLowerCase() +
      (element.getAttribute('data-action') ? '[' + element.getAttribute('data-action') + ']' : '') +
      ':' + text.slice(0, 24)
    );
  });
  return seen.slice(0, 60).join(' | ');
}

function itemCard(root, itemId) {
  const matches = [root].concat(descendantsOf(root)).filter(function (element) {
    return element.getAttribute('data-item-id') === itemId;
  });
  if (!matches.length) {
    return null;
  }
  matches.sort(function (a, b) {
    return descendantsOf(b).length - descendantsOf(a).length;
  });
  return matches[0];
}

function requireCard(root, itemId) {
  const card = itemCard(root, itemId);
  assert.ok(
    card,
    '未找到 data-item-id="' + itemId + '" 的卡片；可见交互元素：' + describeInteractives(root)
  );
  return card;
}

function textMatches(element, target) {
  if (normText(element.textContent) === target) {
    return true;
  }
  if (element.getAttribute('aria-label') === target) {
    return true;
  }
  if (element.getAttribute('data-label') === target) {
    return true;
  }
  const tag = element.tagName;
  if ((tag === 'INPUT' || tag === 'BUTTON' || tag === 'TEXTAREA' || tag === 'OPTION') &&
      normText(element.value) === target) {
    return true;
  }
  return false;
}

function clickableByText(root, label) {
  const target = normText(label);
  const all = [root].concat(descendantsOf(root));
  const matched = all.filter(function (element) {
    return textMatches(element, target);
  });
  const deepest = matched.filter(function (element) {
    return !matched.some(function (other) {
      return other !== element && descendantsOf(element).indexOf(other) !== -1;
    });
  });
  const interactive = deepest.filter(isInteractive);
  return interactive.length ? interactive : deepest;
}

function clickElement(element) {
  if (element.tagName === 'LABEL') {
    const control = descendantsOf(element).filter(function (node) {
      return node.tagName === 'INPUT' || node.tagName === 'BUTTON';
    })[0];
    if (control) {
      return clickElement(control);
    }
  } else if (element.tagName !== 'INPUT') {
    const label = element.closest ? element.closest('label') : null;
    if (label) {
      const control = descendantsOf(label).filter(function (node) {
        return node.tagName === 'INPUT';
      })[0];
      if (control) {
        return clickElement(control);
      }
    }
  }
  element.click();
  return element;
}

function clickText(root, label) {
  const candidates = clickableByText(root, label);
  assert.ok(
    candidates.length,
    '未找到可点击文案「' + label + '」；可见交互元素：' + describeInteractives(root)
  );
  return clickElement(candidates[0]);
}

function choose(root, label) {
  return clickText(root, label);
}

function noteField(card) {
  const fields = [card].concat(descendantsOf(card)).filter(function (element) {
    if (element.tagName === 'TEXTAREA') {
      return true;
    }
    if (element.tagName !== 'INPUT') {
      return false;
    }
    const type = element.type;
    return type === 'text' || type === 'search' || type === '' || type === 'textarea';
  });
  return fields[0] || null;
}

function typeNote(card, value) {
  const field = noteField(card);
  assert.ok(field, '卡片内未找到补充输入框（textarea / input[type=text]）');
  field.value = value;
  field.dispatchEvent(createEvent('input'));
  field.dispatchEvent(createEvent('change'));
  return field;
}

function hasSelectionMarker(card) {
  const attributes = ['aria-checked', 'aria-selected', 'aria-pressed', 'data-selected', 'data-chosen'];
  const nodes = [card].concat(descendantsOf(card));
  return nodes.some(function (node) {
    if (attributes.some(function (attr) {
      return node.getAttribute(attr) === 'true';
    })) {
      return true;
    }
    return /(?:^|\s)(?:selected|chosen|picked|is-selected|is-chosen)(?:\s|$)/i.test(node.getAttribute('class') || '');
  });
}

/** 找到「标签 + N / M」形式的进度文本（今日关卡 / 今日出发）。 */
function labeledProgress(root, label) {
  const all = [root].concat(descendantsOf(root));
  const holders = all.filter(function (element) {
    if (normText(element.textContent).indexOf(label) === -1) {
      return false;
    }
    return !descendantsOf(element).some(function (child) {
      return normText(child.textContent).indexOf(label) !== -1;
    });
  });
  for (let i = 0; i < holders.length; i += 1) {
    const scopes = [holders[i], holders[i].parentNode, holders[i].parentNode && holders[i].parentNode.parentNode];
    for (let j = 0; j < scopes.length; j += 1) {
      const scope = scopes[j];
      if (!scope) {
        continue;
      }
      const text = normText(scope.textContent);
      const match = text.match(/(\d+)\s*\/\s*(\d+)/);
      if (match) {
        return {
          label: label,
          numerator: Number(match[1]),
          denominator: Number(match[2]),
          text: text
        };
      }
    }
  }
  return null;
}

function headingElement(root, label) {
  const candidates = [root].concat(descendantsOf(root)).filter(function (element) {
    if (['INPUT', 'BUTTON', 'TEXTAREA', 'LABEL'].indexOf(element.tagName) !== -1) {
      return false;
    }
    const text = normText(element.textContent);
    if (!text || text.length > 40 || text.indexOf(label) !== 0) {
      return false;
    }
    return /^H[1-6]$/.test(element.tagName) ||
      /col|column|title|heading|head|label/i.test(element.getAttribute('class') || '') ||
      element.hasAttribute('data-column') ||
      element.hasAttribute('data-column-label');
  });
  return candidates[0] || null;
}

function ancestorHolding(card, predicate) {
  let node = card;
  while (node && node.nodeType === 1) {
    if (predicate(node)) {
      return node;
    }
    node = node.parentNode;
  }
  return null;
}

function lastExport(ui) {
  assert.ok(ui.exportCalls.length > 0, '未调用 ui.openExport');
  return ui.exportCalls[ui.exportCalls.length - 1];
}

function exportedText(ui) {
  return ui.exportCalls.map(function (lines) {
    return lines.join('\n');
  }).join('\n');
}

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    Object.keys(value).forEach(function (key) {
      deepFreeze(value[key]);
    });
  }
  return value;
}

// ------------------------------------------------------------- 手写期望

const PENDING_IDS = ['N1020-01', 'N1020-02', 'N1020-03', 'N1020-04', 'N1020-05', 'N1020-06', 'N1020-07'];
const DAILY = 'plans/daily/2026-10-20.md';
const DISPATCH_IDS = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8'].map(function (taskNo) {
  return DAILY + '#' + taskNo;
});

const LINE = {
  pendingDecisionB: '【控制台指令】拍板：示例项目A：控制台范围 · 控制台范围 → B 缩小范围 · 补充：先按缩小范围处理',
  pendingDecisionA: '【控制台指令】拍板：示例项目A：控制台范围 · 控制台范围 → A 继续原范围',
  pendingAccept: '【控制台指令】验收：示例项目A：控制台三栏 · 控制台三栏 → 验收通过',
  pendingReject: '【控制台指令】验收：示例项目A：控制台三栏 · 控制台三栏 → 打回 · 补充：请核对交付结果',
  t2: '【控制台指令】确认派发：示例项目A：T2 只读排查示例 · 角色 排查 · 执行器 Codex · 现有目录',
  t2UserOverride: '【控制台指令】确认派发：示例项目A：T2 只读排查示例 · 角色 产品 · 执行器 Claude Code · 新建工作区',
  t2Local: '【控制台指令】确认派发：示例项目A：T2 只读排查示例 · 角色 排查 · 执行器 Codex · 新终端窗口',
  t3: '【控制台指令】确认派发：示例项目A：T3 控制台示例修复 · 角色 开发 · 执行器 OpenCode · 新建工作区',
  t4: '【控制台指令】确认派发：示例项目B：T4 示例需求交付 · 开发交付流水线',
  t5: '【控制台指令】确认派发：示例项目B：T5 示例调研（无角色） · 角色 未指定 · 执行器 Claude Code · 现有目录',
  t6: '【控制台指令】确认派发：示例项目B：T6 无角色无执行器 · 角色 未指定 · 执行器 OpenCode · 新建工作区',
  t8: '【控制台指令】确认派发：示例项目A：T8 示例产品澄清 · 角色 产品 · 执行器 Codex · 新建工作区',
  t8AsInvestigate: '【控制台指令】确认派发：示例项目A：T8 示例产品澄清 · 角色 排查 · 执行器 Codex · 现有目录',
  t6AsDev: '【控制台指令】确认派发：示例项目B：T6 无角色无执行器 · 角色 开发 · 执行器 OpenCode · 新建工作区',
  t6AsResearch: '【控制台指令】确认派发：示例项目B：T6 无角色无执行器 · 角色 调研 · 执行器 Claude Code · 现有目录',
  t6AsProduct: '【控制台指令】确认派发：示例项目B：T6 无角色无执行器 · 角色 产品 · 执行器 Claude Code · 新建工作区',
  t6AsInvestigate: '【控制台指令】确认派发：示例项目B：T6 无角色无执行器 · 角色 排查 · 执行器 Codex · 现有目录'
};

function card(handle, itemId) {
  return requireCard(handle.container, itemId);
}

function enterTask(handle, itemId) {
  clickText(card(handle, itemId), '组队');
  return card(handle, itemId);
}

function dispatchAll(handle) {
  const before = handle.ui.exportCalls.length;
  clickText(handle.container, '全部出发 · 生成指令');
  if (handle.ui.exportCalls.length === before) {
    return [];
  }
  return lastExport(handle.ui);
}

function generate(handle) {
  const before = handle.ui.exportCalls.length;
  clickText(handle.container, '生成指令');
  if (handle.ui.exportCalls.length === before) {
    // 实现可以选择“不打开导出面板”；等价于没有可导出的指令。
    return [];
  }
  return handle.ui.exportCalls[handle.ui.exportCalls.length - 1];
}

// =========================================================== 待处理页

pageTest('待处理：三栏名称/条数/优先标记，未处理交接只给提示', function () {
  const handle = mountPage(snap('full-orca'), 'pending');
  const { container } = handle;

  const expectedCounts = { '要拍板': 1, '卡住': 1, '待验收': 5 };
  const assigned = { '要拍板': [], '卡住': [], '待验收': [] };

  PENDING_IDS.forEach(function (id) {
    const card = requireCard(container, id);
    let column = null;
    let node = card;
    while (node && node !== container) {
      const labels = Object.keys(expectedCounts).filter(function (label) {
        const heading = headingElement(node, label);
        return Boolean(heading);
      });
      if (labels.length === 1) {
        column = labels[0];
        break;
      }
      if (labels.length > 1) {
        break;
      }
      node = node.parentNode;
    }
    assert.ok(column, id + ' 未落在三栏之一（找不到唯一的栏标题）');
    assigned[column].push(id);
  });

  Object.keys(expectedCounts).forEach(function (label) {
    assert.ok(headingElement(container, label), '缺少栏标题「' + label + '」');
    assert.equal(
      assigned[label].length,
      expectedCounts[label],
      label + ' 栏卡片数量'
    );
    const pattern = new RegExp(label + '[^\\d]{0,8}' + expectedCounts[label]);
    assert.match(normText(container.textContent), pattern, label + ' 栏必须在标题附近标出条数');
  });

  // 未处理交接：全局计数与提示，但不出现在三栏卡片集合里。
  assert.equal(assigned['要拍板'].length + assigned['卡住'].length + assigned['待验收'].length, 7);
  const containerText = normText(container.textContent);
  assert.match(containerText, /交接/, '未处理交接必须有计数提示');
  assert.match(containerText, /未处理/, '未处理交接要写明「未处理」');
  assert.equal(
    [container].concat(descendantsOf(container)).filter(function (element) {
      return /^交接/.test(normText(element.textContent)) &&
        /title|heading|col/i.test(element.getAttribute('class') || '');
    }).length,
    0,
    '交接不能另立第四栏'
  );

  // 要拍板栏标「优先」。
  const priorityHost = ancestorHolding(requireCard(container, 'N1020-01'), function (node) {
    return normText(node.textContent).indexOf('优先') !== -1 && Boolean(headingElement(node, '要拍板'));
  });
  assert.ok(priorityHost, '要拍板栏必须标「优先」');
});

pageTest('待处理：卡片含角色形象、来源、时间、标题、说明与绝对详情路径', function () {
  const handle = mountPage(snap('full-orca'), 'pending');
  const card = requireCard(handle.container, 'N1020-01');
  const text = surfaceText(card);

  assert.ok(handle.ui.avatarCalls.indexOf('product') !== -1, '必须用 ui.avatar(roleId) 取角色形象');
  assert.match(text, /产品 Agent/, '发消息角色名字');
  assert.match(text, /示例项目A/, '来源项目');
  assert.match(text, /控制台范围/, '事项/标题');
  assert.match(text, /两个方案等你选/, '一句话说明');
  assert.match(text, /\d+\s*分钟前/, '要有「多久之前」（09:12 相对生成时间 10:00）');
  assert.ok(
    text.indexOf('C:/example-device/assistant/runs/2026-10-20-example-gamma.md') !== -1,
    '详情必须是绝对路径，实际：' + text.slice(0, 200)
  );
  assert.equal(/\.\.?[\\/]/.test(text), false, '不能出现相对路径');
  assert.ok(hasSelectionMarker(card) === false, '未选择时不应有选中高亮');

  // 三栏里其余角色形象都被用到。
  ['investigate', 'dev', 'accept', 'research'].forEach(function (roleId) {
    assert.ok(handle.ui.avatarCalls.indexOf(roleId) !== -1, '缺少角色形象：' + roleId);
  });
});

pageTest('待处理：五项体验事实各自如实展示，不互相冒充', function () {
  const handle = mountPage(snap('full-orca'), 'pending');
  const { container } = handle;

  const started = surfaceText(requireCard(container, 'N1020-03'));
  assert.match(started, /体验环境已启动/, '已启动体验');
  assert.match(started, /记录于/, '必须注明记录时间，生成后状态可能变化');
  assert.match(started, /6\s*\/\s*7|6 通过|通过 6/, '用例通过情况 6/7');
  assert.match(started, /打开体验/, '打开体验按钮');
  assert.ok(
    started.indexOf('http://127.0.0.1:8801/console?view=summary') !== -1,
    '本地体验链接'
  );
  ['整体视图五项指标', '待处理三栏计数', '体验区链接'].forEach(function (focus) {
    assert.ok(started.indexOf(focus) !== -1, '重点体验缺：' + focus);
  });
  assert.match(started, /关(掉|闭)/, '必须提示验收后环境会自动关掉');

  const noUi = surfaceText(requireCard(container, 'N1020-04'));
  assert.match(noUi, /没有界面/, '无界面卡片必须明确说明');
  ['验收通过', '打回', '稍后'].forEach(function (label) {
    assert.ok(clickableByText(requireCard(container, 'N1020-04'), label).length, '无界面也可选：' + label);
  });

  const unknown = surfaceText(requireCard(container, 'N1020-05'));
  assert.equal(/体验环境已启动/.test(unknown), false, '缺体验信息不能声称已启动');
  assert.equal(/没有界面/.test(unknown), false, '缺体验信息不能冒充无界面');

  const staticExp = surfaceText(requireCard(container, 'N1020-06'));
  assert.match(staticExp, /静态/, '静态页面需注明');
  assert.equal(/体验环境已启动/.test(staticExp), false, '静态页面不能声称环境已启动');

  const expired = surfaceText(requireCard(container, 'N1020-07'));
  assert.match(expired, /过期/, '过期体验需注明');
  assert.equal(/体验环境已启动/.test(expired), false, '过期体验不能声称已启动');
});

pageTest('待处理：已处理交接折叠在底部，未处理交接没有决定选项', function () {
  const handle = mountPage(snap('full-orca'), 'pending');
  const { container } = handle;

  const detailsNodes = [container].concat(descendantsOf(container)).filter(function (element) {
    return element.tagName === 'DETAILS';
  });
  assert.ok(detailsNodes.length > 0, '「已自动处理」必须用可折叠容器（details）呈现');
  const folded = detailsNodes.filter(function (node) {
    return normText(node.textContent).indexOf('已自动处理') !== -1;
  })[0];
  assert.ok(folded, '折叠区标题必须写明「已自动处理」');
  assert.match(surfaceText(folded), /已合并并更新计划/, '已处理交接必须带处理说明 note');
  assert.equal(Boolean(folded.open), false, '已自动处理默认折叠');

  const handoff = itemCard(container, 'N1020-08');
  if (handoff) {
    [handoff].concat(descendantsOf(handoff)).forEach(function (node) {
      if (isInteractive(node)) {
        clickElement(node);
      }
    });
    assert.equal(
      Object.prototype.hasOwnProperty.call(handle.ui.selection.pending, 'N1020-08'),
      false,
      '未处理交接不能产生人工决定'
    );
  }
  const text = normText(container.textContent);
  assert.match(text, /未处理/, '必须给出未处理交接提示');
  assert.match(text, /交接/, '提示里要写明交接');
});

pageTest('待处理：决定可改选、补充可见，今日关卡按 ID 去重', function () {
  const handle = mountPage(snap('full-orca'), 'pending');
  const { container, ui } = handle;

  const initial = labeledProgress(container, '今日关卡');
  assert.ok(initial, '缺少「今日关卡」进度');
  assert.equal(initial.denominator, 8, '今日关卡分母 = 当日创建/跨日未处理 8 条（含未处理交接）');
  assert.equal(initial.numerator, 0, '未选择时已处理为 0');

  choose(card(handle, 'N1020-01'), 'A 继续原范围');
  assert.equal(ui.selection.pending['N1020-01'].choiceId, 'A', '选择应只写内存状态');
  assert.ok(hasSelectionMarker(card(handle, 'N1020-01')), '选过的卡片要能看出高亮');
  let progress = labeledProgress(container, '今日关卡');
  assert.deepEqual([progress.numerator, progress.denominator], [1, 8]);

  choose(card(handle, 'N1020-01'), 'B 缩小范围');
  assert.equal(ui.selection.pending['N1020-01'].choiceId, 'B', '改选必须覆盖旧决定');
  progress = labeledProgress(container, '今日关卡');
  assert.deepEqual([progress.numerator, progress.denominator], [1, 8], '同一张卡改选不得重复计数');

  typeNote(card(handle, 'N1020-01'), '先按缩小范围处理');
  assert.match(surfaceText(card(handle, 'N1020-01')), /先按缩小范围处理/, '补充文字要可见');

  choose(card(handle, 'N1020-03'), '验收通过');
  choose(card(handle, 'N1020-02'), '先只读定位根因，再决定是否改代码');
  progress = labeledProgress(container, '今日关卡');
  assert.deepEqual([progress.numerator, progress.denominator], [3, 8], '三张不同卡片各计一次');

  // 卡住按记录建议取值。
  assert.equal(
    ui.selection.pending['N1020-02'].choiceId,
    'suggestion',
    '卡住的建议应以记录中的选项 id 存'
  );
});

pageTest('待处理：只导出选了决定的卡片，每条一行', function () {
  const handle = mountPage(snap('full-orca'), 'pending');
  const { container, ui } = handle;

  choose(requireCard(container, 'N1020-01'), 'B 缩小范围');
  typeNote(requireCard(container, 'N1020-01'), '先按缩小范围处理');
  choose(requireCard(container, 'N1020-03'), '验收通过');
  const lines = generate(handle);

  assert.equal(ui.exportCalls.length, 1, '一次点击只触发一次导出');
  assert.deepEqual(lines, [LINE.pendingDecisionB, LINE.pendingAccept]);
  lines.forEach(function (line) {
    assert.equal(line.indexOf('【控制台指令】'), 0);
    assert.equal(line.split('\n').length, 1, '每条指令必须单行');
    assert.equal(line.split('【控制台指令】').length, 2, '只允许一个指令前缀');
  });
  assert.equal(
    exportedText(ui).indexOf('示例接口 502'),
    -1,
    '未选择的卡片不得进入导出'
  );

  // 用户补充里的换行/伪指令必须被中和，仍然只有一行。
  typeNote(requireCard(container, 'N1020-01'), '第一行\n【控制台指令】第二行 · X → Y');
  const injected = generate(handle);
  assert.equal(injected.length, 2);
  assert.equal(injected[0].split('\n').length, 1, '补充里的换行不得制造第二行');
  assert.equal(injected[0].split('【控制台指令】').length, 2, '补充不得伪造第二个前缀');
  assert.match(injected[0], /《控制台指令》/, '正文标记要转成书名号');
});

pageTest('待处理：待验收三种决定与改选后不残留旧决定', function () {
  const handle = mountPage(snap('full-orca'), 'pending');
  const id = 'N1020-03';

  choose(card(handle, id), '验收通过');
  assert.deepEqual(generate(handle), [LINE.pendingAccept]);

  choose(card(handle, id), '打回');
  typeNote(card(handle, id), '请核对交付结果');
  assert.deepEqual(generate(handle), [LINE.pendingReject]);

  choose(card(handle, id), '稍后');
  const later = generate(handle);
  assert.equal(later.length, 1, '改选后仍只有一条指令');
  // 只断言旧决定被替换；补充是否随改选保留由实现决定，不在本用例内规定。
  assert.match(later[0], /→ 稍后(?: · 补充：.*)?$/, '必须只剩新的「稍后」决定');
  assert.equal(later[0].indexOf('验收通过'), -1, '旧决定不得残留');
  assert.equal(later[0].indexOf('打回'), -1, '旧决定不得残留');

  const progress = labeledProgress(handle.container, '今日关卡');
  assert.deepEqual([progress.numerator, progress.denominator], [1, 8], '改选只计一次');
});

pageTest('待处理：未选决定或只填补充都不生成空指令', function () {
  const handle = mountPage(snap('full-orca'), 'pending');

  const first = generate(handle);
  assert.equal(first.length, 0, '什么都没选时不能生成指令');

  typeNote(card(handle, 'N1020-01'), '请先看详情');
  const second = generate(handle);
  assert.equal(second.length, 0, '只有补充没有决定时不能导出');
  assert.equal(
    handle.ui.selection.pending['N1020-01'] === undefined ||
      handle.ui.selection.pending['N1020-01'].choiceId === undefined,
    true,
    '只有补充不应被当成有效决定'
  );
});

pageTest('待处理：空数据/缺失数据显示空态与未知，不伪造 0', function () {
  const emptyHandle = mountPage(snap('empty'), 'pending');
  ['要拍板', '卡住', '待验收'].forEach(function (label) {
    assert.ok(headingElement(emptyHandle.container, label), '空态也要保留三栏标题：' + label);
  });
  assert.equal(
    [emptyHandle.container].concat(descendantsOf(emptyHandle.container)).filter(function (element) {
      return element.hasAttribute('data-item-id');
    }).length,
    0,
    '空数据不得虚构卡片'
  );
  assert.match(normText(emptyHandle.container.textContent), /待处理|空|没有/, '要有空状态文案');
  assert.equal(generate(emptyHandle).length, 0, '空数据不能生成空指令');

  const missingHandle = mountPage(snap('missing'), 'pending');
  const missingText = normText(missingHandle.container.textContent);
  ['要拍板', '卡住', '待验收'].forEach(function (label) {
    assert.ok(headingElement(missingHandle.container, label), '缺数据也要保留三栏标题：' + label);
  });
  assert.match(missingText, /—|未知|缺失/, '缺失的计数必须显示未知而不是 0');
  assert.equal(/0\s*\/\s*0/.test(missingText), false, '缺失数据不能画伪进度');
  assert.equal(generate(missingHandle).length, 0);
});

pageTest('待处理：敏感补充被拒绝导出，原文不外泄', function () {
  const handle = mountPage(snap('hostile'), 'pending');
  const id = 'N1020-01';
  choose(card(handle, id), '验收通过');
  typeNote(card(handle, id), 'token=FICTIONAL_ONLY');

  const lines = generate(handle);
  assert.deepEqual(lines, [], '敏感补充必须整条拒绝导出');
  assert.equal(exportedText(handle.ui).indexOf('FICTIONAL_ONLY'), -1, '导出不得出现敏感原文');

  // 换成安全补充仍可导出，证明拒绝只针对敏感值。
  typeNote(card(handle, id), '已核对用例结果');
  const ok = generate(handle);
  assert.equal(ok.length, 1);
  assert.match(ok[0], /补充：已核对用例结果/);
});

// =========================================================== 派发确认页

pageTest('派发确认：候选范围、卡片信息、难度星级与阶段产出', function () {
  const handle = mountPage(snap('full-orca'), 'dispatch');
  const { container } = handle;

  const found = [container].concat(descendantsOf(container)).filter(function (element) {
    return element.hasAttribute('data-item-id');
  }).map(function (element) {
    return element.getAttribute('data-item-id');
  });
  const unique = found.filter(function (id, index) {
    return found.indexOf(id) === index && DISPATCH_IDS.indexOf(id) !== -1;
  });
  assert.deepEqual(unique.slice().sort(), DISPATCH_IDS.slice().sort(), '7 个待派候选各一张卡片');

  const t2 = surfaceText(requireCard(container, DAILY + '#T2'));
  assert.match(t2, /T2/);
  assert.match(t2, /只读排查示例/);
  assert.match(t2, /示例项目A/);
  assert.match(t2, /只读定位示例问题根因/);
  assert.match(t2, /复现/);
  assert.match(t2, /定位根因/);
  assert.match(t2, /出结论/);
  assert.match(t2, /排查结论/);
  assert.equal((t2.match(/★/g) || []).length, 1, '不改代码单任务 ★');

  const t3 = surfaceText(requireCard(container, DAILY + '#T3'));
  assert.equal((t3.match(/★/g) || []).length, 2, '改代码单任务 ★★');
  assert.match(t3, /代码提交/);

  const t4 = surfaceText(requireCard(container, DAILY + '#T4'));
  assert.equal((t4.match(/★/g) || []).length, 3, '团队副本 ★★★');

  const t5 = surfaceText(requireCard(container, DAILY + '#T5'));
  assert.equal((t5.match(/★/g) || []).length, 1);

  const t7 = surfaceText(requireCard(container, DAILY + '#T7'));
  assert.match(t7, /—/, '难度证据不足显示「—」');
  assert.equal((t7.match(/★/g) || []).length, 0, '证据不足不得编造星级');
});

pageTest('派发确认：默认方案按 PRD 四角色/任务声明/兜底，且不改写声明', function () {
  const handle = mountPage(snap('full-orca'), 'dispatch');
  const { container } = handle;

  // 兜底标记只给真正兜底的卡片。
  assert.match(surfaceText(requireCard(container, DAILY + '#T6')), /兜底/, '无角色无执行器要标兜底');
  assert.equal(
    /兜底/.test(surfaceText(requireCard(container, DAILY + '#T2'))),
    false,
    '任务已声明执行器时不能标兜底'
  );
  assert.match(
    surfaceText(requireCard(container, DAILY + '#T2')),
    /任务已声明/,
    '默认值旁要有理由'
  );
  assert.match(
    surfaceText(requireCard(container, DAILY + '#T8')),
    /声明/,
    '任务声明执行器的理由要写明'
  );
  assert.match(
    surfaceText(requireCard(container, DAILY + '#T7')),
    /等你选择|证据不足/,
    '证据不足要说明等用户选择'
  );

  // 以导出为准核对默认执行器/地点（展示里可能列出全部可选项）。
  ['T2', 'T3', 'T5', 'T6', 'T8'].forEach(function (taskNo) {
    enterTask(handle, DAILY + '#' + taskNo);
  });
  assert.deepEqual(dispatchAll(handle), [
    LINE.t2,
    LINE.t3,
    LINE.t5,
    LINE.t6,
    LINE.t8
  ]);
});

pageTest('派发确认：四个角色的默认映射（用户改角色后重算）', function () {
  const handle = mountPage(snap('full-orca'), 'dispatch');
  const { ui } = handle;
  const id = DAILY + '#T6';
  enterTask(handle, id);

  const cases = [
    ['dev', LINE.t6AsDev],
    ['research', LINE.t6AsResearch],
    ['product', LINE.t6AsProduct],
    ['investigate', LINE.t6AsInvestigate]
  ];
  cases.forEach(function (pair) {
    ui.selection.dispatch[id].roleId = pair[0];
    assert.deepEqual(dispatchAll(handle), [pair[1]], pair[0] + ' 默认映射');
    ui.exportCalls.length = 0;
  });
});

pageTest('派发确认：用户显式选择覆盖默认，任务声明不被角色默认改写', function () {
  const handle = mountPage(snap('full-orca'), 'dispatch');
  const { ui } = handle;
  const id = DAILY + '#T2';

  // 只改执行器与地点，角色保持排查。
  choose(card(handle, id), 'Claude Code');
  choose(card(handle, id), '新建工作区');
  clickText(card(handle, id), '组队');
  assert.deepEqual(dispatchAll(handle), [
    '【控制台指令】确认派发：示例项目A：T2 只读排查示例 · 角色 排查 · 执行器 Claude Code · 新建工作区'
  ]);

  // 再改角色：已显式选择的执行器/地点不被角色默认顶掉。
  choose(card(handle, id), '产品');
  dispatchAll(handle);
  assert.deepEqual(lastExport(ui), [LINE.t2UserOverride]);

  // 任务声明优先于 PRD 角色默认：T8 声明 Codex，改成排查仍是 Codex。
  const t8id = DAILY + '#T8';
  choose(card(handle, t8id), '排查');
  clickText(card(handle, t8id), '组队');
  assert.deepEqual(dispatchAll(handle).slice(-1), [LINE.t8AsInvestigate]);
});

/** 卡片「关卡」一行的文本；没有关卡（信息未记录）时返回 null。 */
function stagesText(cardElement) {
  const track = descendantsOf(cardElement).filter(function (node) {
    return node.classList.contains('act-track');
  })[0];
  return track ? normText(track.textContent).replace(/\s*›\s*/g, '›') : null;
}

pageTest('派发确认：改选角色后关卡按 PRD 4.7 跟随当前有效角色（取消、重挂载、不改快照）', function () {
  const snapshot = deepFreeze(snap('full-orca'));
  const pristine = JSON.stringify(snapshot);
  const context = loadPages();
  const doc = context.doc;
  const container = doc.createElement('main');
  const ui = createUi(doc);
  const handle = mountPage(snapshot, 'dispatch', { context: context, container: container, ui: ui });
  const t2 = DAILY + '#T2';

  assert.equal(stagesText(card(handle, t2)), '复现›定位根因›出结论', '排查任务初始关卡');

  choose(card(handle, t2), '开发');
  assert.equal(stagesText(card(handle, t2)), '实现›自测›提交', '改成开发后关卡跟着变');

  choose(card(handle, t2), '开发');
  assert.equal(ui.selection.dispatch[t2].roleId, undefined, '再点一次取消覆盖');
  assert.equal(stagesText(card(handle, t2)), '复现›定位根因›出结论', '取消覆盖回到原角色关卡');

  choose(card(handle, t2), '产品');
  assert.equal(stagesText(card(handle, t2)), null, '产品没有 PRD 固定关卡，不能沿用排查关卡');
  assert.match(surfaceText(card(handle, t2)), /信息未记录/);

  choose(card(handle, t2), '调研');
  assert.equal(stagesText(card(handle, t2)), '收集›整理›出报告', '改成调研');

  // 无角色任务（T6 记录里没有关卡）选了开发后也给出 PRD 关卡。
  const t6 = DAILY + '#T6';
  assert.equal(stagesText(card(handle, t6)), null);
  choose(card(handle, t6), '开发');
  assert.equal(stagesText(card(handle, t6)), '实现›自测›提交');

  // 切页重挂载：沿用内存选择。
  handle.dispose();
  const again = mountPage(snapshot, 'dispatch', { context: context, container: container, ui: ui });
  assert.equal(stagesText(card(again, t2)), '收集›整理›出报告', '重挂载后关卡沿用调研选择');
  assert.equal(stagesText(card(again, t6)), '实现›自测›提交');

  // 团队副本保持固定流水线关卡。
  assert.equal(
    stagesText(card(again, DAILY + '#T4')),
    '实现计划 + 用例›工作包开发›审查与修复›集成测试›合回 develop›你来体验'
  );
  assert.equal(JSON.stringify(snapshot), pristine, '改选角色不得写回快照');
});

pageTest('派发确认：团队副本固定三人队、不可编辑、缺 PRD 不可组队', function () {
  const handle = mountPage(snap('full-orca'), 'dispatch');
  const { container, ui } = handle;
  const id = DAILY + '#T4';
  const teamCard = requireCard(container, id);
  const text = surfaceText(teamCard);

  assert.match(text, /开发/);
  assert.match(text, /OpenCode/);
  assert.match(text, /验收/);
  assert.match(text, /Codex/);
  assert.match(text, /跟进/);
  assert.match(text, /Claude Code/);
  assert.ok(
    text.indexOf('C:/example-device/assistant/docs/delivery/demo/prd.md') !== -1,
    '团队副本要展示 PRD 绝对路径：' + text.slice(0, 240)
  );

  // 队伍不可改：卡片内任何可交互元素都不应写出 roleId/executor/mode。
  // （「组队 / 已组队」是组队开关，不属于队伍配置，先跳过。）
  [teamCard].concat(descendantsOf(teamCard)).forEach(function (node) {
    if (!isInteractive(node)) {
      return;
    }
    const label = normText(node.textContent);
    if (label.indexOf('组队') !== -1 || label.indexOf('已组队') !== -1) {
      return;
    }
    clickElement(node);
  });
  const pick = ui.selection.dispatch[id] || {};
  assert.equal(pick.roleId, undefined, '团队副本不能改角色');
  assert.equal(pick.executor, undefined, '团队副本不能改执行器');
  assert.equal(pick.mode, undefined, '团队副本不能改地点');

  clickText(card(handle, id), '组队');
  assert.deepEqual(dispatchAll(handle), [LINE.t4]);

  // 缺 PRD：信息不完整，不可组队，导出也不出团队指令。
  const broken = snap('full-orca');
  broken.dispatchTasks.forEach(function (task) {
    if (task.id === id) {
      task.prdPath = null;
    }
  });
  const brokenHandle = mountPage(broken, 'dispatch');
  const brokenCard = requireCard(brokenHandle.container, id);
  assert.match(
    surfaceText(brokenCard),
    /信息不完整|缺少|缺.{0,4}PRD/,
    '缺 PRD 必须说明信息不完整'
  );
  const enter = clickableByText(brokenCard, '组队');
  if (enter.length) {
    clickElement(enter[0]);
  }
  const lines = dispatchAll(brokenHandle);
  assert.equal(
    lines.some(function (line) {
      return line.indexOf('T4 示例需求交付') !== -1;
    }),
    false,
    '缺 PRD 的团队副本不得导出确认派发'
  );
});

pageTest('派发确认：组队切换与今日出发计数、只导出已组队', function () {
  const handle = mountPage(snap('full-orca'), 'dispatch');
  const { container, ui } = handle;
  const t2id = DAILY + '#T2';
  const t4id = DAILY + '#T4';

  let progress = labeledProgress(container, '今日出发');
  assert.ok(progress, '缺少「今日出发」进度');
  assert.deepEqual([progress.numerator, progress.denominator], [0, 7]);

  clickText(card(handle, t2id), '组队');
  progress = labeledProgress(container, '今日出发');
  assert.deepEqual([progress.numerator, progress.denominator], [1, 7]);

  clickText(card(handle, t4id), '组队');
  progress = labeledProgress(container, '今日出发');
  assert.deepEqual([progress.numerator, progress.denominator], [2, 7]);

  // 「已组队」按钮即取消。
  clickText(card(handle, t2id), '已组队');
  progress = labeledProgress(container, '今日出发');
  assert.deepEqual([progress.numerator, progress.denominator], [1, 7], '取消组队后计数回落');
  assert.equal(ui.selection.dispatch[t2id].selected, false, '取消要写回内存选择');

  clickText(card(handle, t2id), '组队');
  progress = labeledProgress(container, '今日出发');
  assert.deepEqual([progress.numerator, progress.denominator], [2, 7]);

  assert.deepEqual(dispatchAll(handle), [LINE.t2, LINE.t4], '只导出已组队，按源顺序');
});

pageTest('派发确认：全部未组队与无候选都不生成空派发', function () {
  const handle = mountPage(snap('full-orca'), 'dispatch');
  const lines = dispatchAll(handle);
  assert.equal(lines.length, 0, '未组队时不能生成确认派发');

  const emptyHandle = mountPage(snap('empty'), 'dispatch');
  assert.equal(
    [emptyHandle.container].concat(descendantsOf(emptyHandle.container)).filter(function (element) {
      return element.hasAttribute('data-item-id');
    }).length,
    0,
    '无候选不得虚构卡片'
  );
  assert.match(normText(emptyHandle.container.textContent), /没有|空|待派|全部出去/, '要有空状态');
  assert.equal(dispatchAll(emptyHandle).length, 0, '无候选不能生成指令');

  const missingHandle = mountPage(snap('missing'), 'dispatch');
  assert.match(normText(missingHandle.container.textContent), /—|未知|缺失/, '缺数据按未知展示');
  assert.equal(dispatchAll(missingHandle).length, 0);
});

pageTest('派发确认：不在 Orca 时只有新终端窗口，团队不可进入并二次校验', function () {
  const handle = mountPage(snap('full-local'), 'dispatch');
  const { container, ui } = handle;
  const t2 = requireCard(container, DAILY + '#T2');
  const t2Text = surfaceText(t2);
  assert.match(t2Text, /新终端窗口/, '非 Orca 地点必须是新终端窗口');
  assert.equal(/新建工作区/.test(t2Text), false, '非 Orca 不得提供新建工作区');
  assert.equal(/现有目录/.test(t2Text), false, '非 Orca 不得提供现有目录');

  clickText(t2, '组队');
  assert.deepEqual(dispatchAll(handle), [LINE.t2Local]);

  // 手工改内存选择绕过 UI：导出仍强制新终端窗口。
  ui.selection.dispatch[DAILY + '#T2'].mode = 'worktree';
  dispatchAll(handle);
  assert.deepEqual(lastExport(ui), [LINE.t2Local], '非 Orca 双校验必须覆盖旧选择');

  const t4 = requireCard(container, DAILY + '#T4');
  assert.match(surfaceText(t4), /不在 Orca/, '团队副本要说明禁用原因');
  const enterTeam = clickableByText(t4, '组队');
  if (enterTeam.length) {
    clickElement(enterTeam[0]);
  }
  assert.equal(
    (ui.selection.dispatch[DAILY + '#T4'] || {}).selected,
    undefined,
    '非 Orca 团队副本不能进入'
  );
  const teamLines = dispatchAll(handle);
  assert.equal(
    teamLines.some(function (line) {
      return line.indexOf('开发交付流水线') !== -1;
    }),
    false,
    '非 Orca 不导出团队副本'
  );
});

pageTest('派发确认：Orca 内手工把地点改成 terminal 会被拒绝导出', function () {
  const handle = mountPage(snap('full-orca'), 'dispatch');
  const { ui } = handle;
  const id = DAILY + '#T2';
  enterTask(handle, id);
  ui.selection.dispatch[id].mode = 'terminal';
  const lines = dispatchAll(handle);
  assert.equal(
    lines.some(function (line) {
      return line.indexOf('T2 只读排查示例') !== -1;
    }),
    false,
    'Orca 内 terminal 不是合法地点，必须拒绝而不是静默换位置'
  );
});

// =========================================================== 生命周期/只读

pageTest('切页：dispose 后重挂载保留内存选择，且不重复触发事件', function () {
  const snapshot = snap('full-orca');
  const context = loadPages();
  const doc = context.doc;
  const container = doc.createElement('main');
  const ui = createUi(doc);
  const first = mountPage(snapshot, 'pending', { context: context, container: container, ui: ui });

  choose(requireCard(container, 'N1020-01'), 'A 继续原范围');
  assert.equal(ui.selection.pending['N1020-01'].choiceId, 'A');
  const staleButton = clickableByText(container, '生成指令')[0];
  assert.ok(staleButton, '需要「生成指令」按钮做重复绑定探针');
  first.dispose();

  const second = mountPage(snapshot, 'pending', { context: context, container: container, ui: ui });
  assert.equal(ui.selection.pending['N1020-01'].choiceId, 'A', '切页后选择必须保留');

  const progress = labeledProgress(container, '今日关卡');
  assert.deepEqual([progress.numerator, progress.denominator], [1, 8], '重挂载后进度沿用选择');

  const before = ui.exportCalls.length;
  clickText(container, '生成指令');
  assert.equal(ui.exportCalls.length, before + 1, '重挂载后一次点击只触发一次导出');
  assert.deepEqual(lastExport(ui), [LINE.pendingDecisionA]);

  clickElement(staleButton);
  assert.equal(ui.exportCalls.length, before + 1, 'dispose 后旧节点不得再触发导出');
  second.dispose();

  // 同一 selection 跨页保留：mount dispatch 不清空 pending 选择。
  const dispatchHandle = mountPage(snapshot, 'dispatch', { context: context, container: doc.createElement('main'), ui: ui });
  assert.equal(ui.selection.pending['N1020-01'].choiceId, 'A', '进入派发页不能丢待处理选择');
  enterTask(dispatchHandle, DAILY + '#T2');
  dispatchAll(dispatchHandle);
  assert.equal(ui.exportCalls.filter(function (lines) {
    return lines.length && lines[0] === LINE.pendingDecisionA;
  }).length, 1, '跨页不应重复生成待处理指令');
});

pageTest('只读：交互与导出不修改原 snapshot（深冻结下仍可挂载）', function () {
  const pristine = JSON.stringify(snap('full-orca'));
  const frozen = deepFreeze(JSON.parse(pristine));

  const pendingHandle = mountPage(frozen, 'pending');
  choose(requireCard(pendingHandle.container, 'N1020-01'), 'B 缩小范围');
  typeNote(requireCard(pendingHandle.container, 'N1020-01'), '先按缩小范围处理');
  choose(requireCard(pendingHandle.container, 'N1020-03'), '验收通过');
  const pendingLines = generate(pendingHandle);
  assert.deepEqual(pendingLines, [LINE.pendingDecisionB, LINE.pendingAccept]);

  const dispatchHandle = mountPage(frozen, 'dispatch');
  enterTask(dispatchHandle, DAILY + '#T2');
  assert.deepEqual(dispatchAll(dispatchHandle), [LINE.t2]);

  assert.equal(JSON.stringify(frozen), pristine, '挂载/点击/导出都不得写回 snapshot');
});

pageTest('待处理：缺选项与字段不完整的卡片不给猜测决定，未导出有提示', function () {
  const handle = mountPage(snap('conflict'), 'pending');
  const { container } = handle;

  const noChoices = requireCard(container, 'N1020-04');
  assert.match(surfaceText(noChoices), /记录不完整/, '缺选项要说明记录不完整');
  assert.equal(
    [noChoices].concat(descendantsOf(noChoices)).filter(function (node) {
      return node.getAttribute('data-action') === 'pending-choice';
    }).length,
    0,
    '缺选项不能出现可点的决定'
  );

  // 项目为空的卡住记录：可以点建议，但导出端拒绝，页面要说明没导出。
  choose(requireCard(container, 'N1020-03'), '无法拆分，等主会话确认');
  assert.deepEqual(generate(handle), []);
  assert.match(normText(container.textContent), /没有导出/, '已选但被拒绝导出要有提示');

  choose(requireCard(container, 'N1020-01'), 'A 保留');
  assert.deepEqual(generate(handle), ['【控制台指令】拍板：示例项目A：范围确认 · 范围确认 → A 保留']);
});

pageTest('安全：屏蔽字段显示占位、危险链接不可点、敏感原文不进 DOM', function () {
  const pendingHandle = mountPage(snap('hostile'), 'pending');
  const dispatchHandle = mountPage(snap('hostile'), 'dispatch');
  [pendingHandle, dispatchHandle].forEach(function (handle) {
    const text = surfaceText(handle.container);
    ['FICTIONAL_ONLY', 'BEGIN', 'Bearer', 'private/'].forEach(function (sentinel) {
      assert.equal(text.indexOf(sentinel), -1, '页面不得出现：' + sentinel);
    });
  });
  const blocked = surfaceText(requireCard(pendingHandle.container, 'N1020-02'));
  assert.match(blocked, /详情路径未记录或已屏蔽/);
  assert.match(blocked, /体验链接未通过安全校验/);

  // 纵深：即便快照里混进危险链接与敏感标题，页面也不渲染成可点链接/原文。
  const tampered = snap('full-orca');
  tampered.pending.forEach(function (item) {
    if (item.id === 'N1020-03') {
      item.experience.url = 'javascript:alert(1)';
    }
    if (item.id === 'N1020-06') {
      item.experience.url = 'http://localhost.evil.example/x';
    }
    if (item.id === 'N1020-01') {
      item.title = 'token=FICTIONAL_ONLY';
    }
  });
  const handle = mountPage(tampered, 'pending');
  const anchors = descendantsOf(handle.container).filter(function (node) {
    return node.tagName === 'A';
  });
  anchors.forEach(function (anchor) {
    assert.match(anchor.getAttribute('href') || '', /^(?:https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\])[:/]|file:\/\/\/)/);
  });
  assert.equal(itemCard(handle.container, 'N1020-03').querySelectorAll('a').length, 0, 'javascript: 链接不能出现');
  assert.equal(itemCard(handle.container, 'N1020-06').querySelectorAll('a').length, 0, '非 loopback 链接不能出现');
  assert.equal(surfaceText(handle.container).indexOf('FICTIONAL_ONLY'), -1, '敏感标题不得进 DOM');
  assert.match(surfaceText(requireCard(handle.container, 'N1020-01')), /标题未记录/);
});

// ================================================================ CSS

// ============================================================ v1.1 P6b 回复界面（通道页面 / 本机文件）

const P6B_TOKEN = 'tok_' + 'c'.repeat(40);
const P6B_CHANNEL = {
  mode: 'channel', url: 'http://127.0.0.1:17318/', token: P6B_TOKEN, instance: 'abcdef12',
  limits: { maxChars: 500, perMinute: 10, dedupeSeconds: 10, pollSeconds: 20, maxLines: 20 }
};

/** 按 P2 的资格规则给虚构快照补回复区（P0 夹具是 v1 形状，没有 reply）。 */
function withReplySlots(snapshot) {
  const out = clone(snapshot);
  const device = { id: null, name: null, local: null };
  out.pending.forEach(function (item) {
    if (item.status !== '待处理' || ['要拍板', '卡住', '完成'].indexOf(item.type) === -1) {
      return;
    }
    let choices;
    let needText = [];
    let pageOnly = [];
    if (item.type === '要拍板') {
      choices = item.choices.map(function (c) { return { id: c.id, label: c.label }; });
    } else if (item.type === '卡住') {
      choices = [{ id: 'follow', label: '按建议办' }, { id: 'other', label: '换个办法' }, { id: 'pause', label: '先停下' }];
      needText = ['other'];
    } else {
      choices = [{ id: 'accept', label: '验收通过' }, { id: 'reject', label: '打回' }, { id: 'later', label: '稍后' }];
      needText = ['reject'];
      pageOnly = ['later'];
    }
    item.reply = {
      card: 'pending:' + item.id, allowed: true, reason: null,
      kind: item.type === '完成' ? 'main' : 'forward', receiver: item.type === '完成' ? '主会话' : '跟进 Agent',
      association: item.id, choices: choices, needText: needText, pageOnly: pageOnly, device: device
    };
  });
  out.replies = [];
  return out;
}

/** 假通道：记下每次 POST 的正文，按 answer(body, n) 回话。 */
function fakeChannel(answer) {
  const posts = [];
  const fetch = function (url, init) {
    const body = init && init.body ? JSON.parse(init.body) : null;
    if (init && init.method === 'POST') {
      posts.push({ url: url, headers: Object.assign({}, init.headers), body: body });
    }
    const data = init && init.method === 'POST'
      ? (typeof answer === 'function' ? answer(body, posts.length) : { ok: true, id: 'N1021-' + String(10 + posts.length), status: 'delivered', message: '已送达主会话 · 编号 N1021-' + String(10 + posts.length) })
      : { ok: true, replies: [] };
    if (data instanceof Error) {
      return Promise.reject(data);
    }
    return Promise.resolve({ json: function () { return Promise.resolve(data); } });
  };
  return { posts: posts, fetch: fetch };
}

function channelPage(snapshot, pageId, answer) {
  const fake = fakeChannel(answer);
  const handle = mountPage(snapshot, pageId, { context: loadPages({ channel: P6B_CHANNEL, fetch: fake.fetch }) });
  handle.posts = fake.posts;
  return handle;
}

async function settle(times) {
  for (let i = 0; i < (times || 8); i += 1) {
    await new Promise(function (resolve) { setImmediate(resolve); });
  }
}

function replyState(cardElement) {
  const cell = querySelectorAll(cardElement, '[data-reply-state]')[0];
  return cell ? normText(cell.textContent) : null;
}

function panelOpen(cardElement) {
  const panel = querySelectorAll(cardElement, '.act-x')[0];
  return !!panel && panel.getAttribute('hidden') === null && !panel.hidden;
}

function plainPosts(handle) {
  return JSON.parse(JSON.stringify(handle.posts.map(function (p) { return p.body; })));
}

pageTest('P6b 待处理 · 通道页面：卡住改用回复选项；行内点决定后「发送已选（N）」每行一条消息，各自回复列变已送达（AC-14、AC-15、AC-17）', async function () {
  const handle = channelPage(withReplySlots(snap('full-orca')), 'pending');
  assert.ok(surfaceText(handle.container).indexOf('最后「发送已选」直接送到主会话') !== -1);
  const blocked = card(handle, 'N1020-02');
  ['按建议办', '换个办法', '先停下'].forEach(function (label) {
    assert.equal(clickableByText(blocked, label).length, 1, '卡住行缺回复选项：' + label);
  });
  assert.equal(clickableByText(blocked, '先只读定位根因，再决定是否改代码').length, 0, '通道页面上卡住不再用记录里的建议项');
  assert.equal(replyState(card(handle, 'N1020-01')), '—');
  assert.equal(clickableByText(handle.container, '生成指令').length, 0, '通道页面底部不再是生成指令');
  const idle = clickableByText(handle.container, '发送已选（0）')[0];
  assert.ok(idle && idle.disabled, '没选时发送已选不可点');

  choose(card(handle, 'N1020-01'), 'A 继续原范围');
  choose(card(handle, 'N1020-02'), '按建议办');
  choose(card(handle, 'N1020-03'), '验收通过');
  choose(card(handle, 'N1020-04'), '稍后');
  assert.ok(surfaceText(handle.container).indexOf('1 条不会发') !== -1, '「稍后」要说明不会发');
  clickText(handle.container, '发送已选（3）');
  assert.ok(clickableByText(handle.container, '发送中…')[0].disabled, '发送中不可点');
  await settle(20);
  assert.deepEqual(plainPosts(handle), [
    { card: 'pending:N1020-01', quick: 'A', text: '', lines: null },
    { card: 'pending:N1020-02', quick: 'follow', text: '', lines: null },
    { card: 'pending:N1020-03', quick: 'accept', text: '', lines: null }
  ]);
  handle.posts.forEach(function (post) {
    assert.equal(post.url, '/api/replies');
    assert.equal(post.headers['X-Assistant-Token'], P6B_TOKEN);
  });
  assert.equal(replyState(card(handle, 'N1020-01')), '已送达主会话');
  assert.equal(replyState(card(handle, 'N1020-03')), '已送达主会话');
  assert.equal(replyState(card(handle, 'N1020-04')), '—');
  assert.ok(surfaceText(handle.container).indexOf('已发出 3 条') === -1, '「稍后」还在已选里，底部仍是已选状态');
  const gate = labeledProgress(handle.container, '今日关卡');
  assert.ok(gate, '缺今日关卡');
  assert.equal(surfaceText(handle.container).indexOf(P6B_TOKEN), -1, '令牌不进页面文字');
});

pageTest('P6b 待处理 · 通道页面：打回 / 换个办法点了自动展开这一行，写了原因才能发；展开行「发送这一条」只发这一条（AC-15、AC-41）', async function () {
  const handle = channelPage(withReplySlots(snap('full-orca')), 'pending');
  choose(card(handle, 'N1020-02'), '换个办法');
  let blocked = card(handle, 'N1020-02');
  assert.equal(panelOpen(blocked), true, '换个办法要自动展开');
  assert.ok(surfaceText(blocked).indexOf('原因（必填）') !== -1);
  assert.ok(surfaceText(blocked).indexOf('送到主会话 → 转给 跟进 Agent') !== -1);
  assert.ok(surfaceText(blocked).indexOf('关联 N1020-02') !== -1);
  const sendOne = clickableByText(blocked, '发送这一条')[0];
  assert.ok(sendOne.disabled, '没写原因不能发');
  assert.ok(surfaceText(handle.container).indexOf('1 条不会发') !== -1);
  typeNote(blocked, '先回滚到上一个版本再查');
  blocked = card(handle, 'N1020-02');
  assert.equal(replyState(blocked), '草稿');
  assert.equal(clickableByText(blocked, '发送这一条')[0].disabled, false);
  assert.ok(clickableByText(handle.container, '发送已选（1）').length, '写了原因就能进发送已选');
  clickText(blocked, '发送这一条');
  await settle();
  assert.deepEqual(plainPosts(handle), [{ card: 'pending:N1020-02', quick: 'other', text: '先回滚到上一个版本再查', lines: null }]);
  blocked = card(handle, 'N1020-02');
  assert.equal(replyState(blocked), '已送达主会话');
  assert.ok(surfaceText(blocked).indexOf('已送达主会话 · 编号 N1021-11') !== -1, '发送反馈带编号');
  assert.ok(surfaceText(blocked).indexOf('换个办法：先回滚到上一个版本再查') !== -1, '回复记录里有这一条');
  assert.ok(clickableByText(handle.container, '发送已选（0）').length, '发出去的从已选里拿掉');
});

pageTest('P6b 待处理 · 通道页面：没发出去给「重试 / 复制指令」，复制得到【控制台指令】回复一行（AC-17、AC-20）', async function () {
  const handle = channelPage(withReplySlots(snap('full-orca')), 'pending', function () { return new Error('net'); });
  choose(card(handle, 'N1020-03'), '打回');
  typeNote(card(handle, 'N1020-03'), '深色模式下导出按钮看不清');
  clickText(card(handle, 'N1020-03'), '发送这一条');
  await settle();
  const row = card(handle, 'N1020-03');
  assert.equal(replyState(row), '没发出去');
  assert.ok(surfaceText(row).indexOf('本地通道连不上，改用复制指令') !== -1);
  assert.equal(clickableByText(row, '重试').length, 0, '断线后不再给「重试」');
  clickText(row, '复制指令');
  assert.deepEqual(lastExport(handle.ui), ['【控制台指令】回复：示例项目A：控制台三栏 · 打回：深色模式下导出按钮看不清 · 关联 N1020-03']);
  assert.equal(replyState(card(handle, 'N1020-03')), '已复制指令');
});

pageTest('P6b 待处理 · 页面过期：通道回 stale 后底部改「生成指令（N）」，生成回复指令行（AC-25）', async function () {
  const handle = channelPage(withReplySlots(snap('full-orca')), 'pending', function () {
    return { ok: false, code: 'stale', message: '通道重启过，刷新页面后重发' };
  });
  choose(card(handle, 'N1020-01'), 'B 缩小范围');
  clickText(handle.container, '发送已选（1）');
  await settle(20);
  assert.equal(replyState(card(handle, 'N1020-01')), '没发出去');
  clickText(handle.container, '生成指令（1）');
  assert.deepEqual(lastExport(handle.ui), ['【控制台指令】回复：示例项目A：控制台范围 · B 缩小范围 · 关联 N1020-01']);
  assert.ok(surfaceText(handle.container).indexOf('通道重启过') !== -1);
});

pageTest('P6b 待处理 · 本机文件打开：回复框只给「复制指令」；底部照 v0「生成指令」，导出和不加载 reply.js 时逐字相同（AC-20、AC-28）', function () {
  const picksOf = function (handle) {
    choose(card(handle, 'N1020-01'), 'B 缩小范围');
    clickText(card(handle, 'N1020-01'), '›');
    typeNote(card(handle, 'N1020-01'), '先按缩小范围处理');
    choose(card(handle, 'N1020-03'), '验收通过');
    return generate(handle);
  };
  const withReply = mountPage(withReplySlots(snap('full-orca')), 'pending');
  const bare = mountPage(snap('full-orca'), 'pending', { context: loadPages({ bare: true }) });
  assert.deepEqual(picksOf(withReply), picksOf(bare));
  assert.deepEqual(lastExport(withReply.ui), [LINE.pendingDecisionB, LINE.pendingAccept]);
  const row = card(withReply, 'N1020-01');
  assert.equal(clickableByText(row, '发送这一条').length, 0);
  clickText(row, '复制指令');
  assert.deepEqual(lastExport(withReply.ui), ['【控制台指令】回复：示例项目A：控制台范围 · B 缩小范围：先按缩小范围处理 · 关联 N1020-01']);
  assert.equal(clickableByText(withReply.container, '发送已选（0）').length, 0);
});

pageTest('P6b 派发确认 · 通道页面：「全部出发（N）」把 v0 的确认派发指令作为一条发出，发出后显示编号，组队没变不能重发（AC-14）', async function () {
  const handle = channelPage(snap('full-orca'), 'dispatch', function () {
    return { ok: true, id: 'N1021-09', status: 'delivered', message: '已送达主会话 · 编号 N1021-09' };
  });
  const file = mountPage(snap('full-orca'), 'dispatch');
  [handle, file].forEach(function (one) {
    enterTask(one, DISPATCH_IDS[0]);
    enterTask(one, DISPATCH_IDS[1]);
  });
  const fileLines = dispatchAll(file);
  assert.deepEqual(fileLines, [LINE.t2, LINE.t3]);
  clickText(handle.container, '全部出发（2）');
  await settle();
  assert.deepEqual(plainPosts(handle), [{ card: 'dispatch', quick: null, text: '', lines: fileLines }]);
  assert.ok(surfaceText(handle.container).indexOf('已发给主会话 · 编号 N1021-09') !== -1);
  assert.ok(clickableByText(handle.container, '全部出发（2）')[0].disabled, '同一批不能重发');
  assert.equal(replyState(handle.container), '已送达主会话');
  enterTask(handle, DISPATCH_IDS[4]);
  assert.equal(clickableByText(handle.container, '全部出发（3）')[0].disabled, false, '组队变了可以再发');
});

pageTest('P6b 派发确认 · 没发出去：给「复制指令」退回 v0 导出；本机文件打开底部是「全部出发 · 生成指令」（PRD 4.5 第 9 项）', async function () {
  const handle = channelPage(snap('full-orca'), 'dispatch', function () { return new Error('net'); });
  enterTask(handle, DISPATCH_IDS[0]);
  clickText(handle.container, '全部出发（1）');
  await settle();
  assert.equal(replyState(handle.container), '没发出去');
  assert.ok(surfaceText(handle.container).indexOf('本地通道连不上，改用复制指令') !== -1);
  // 断线后底部直接降级成生成指令（等同复制指令），不再单独给一个「复制指令」
  assert.equal(clickableByText(handle.container, '复制指令').length, 0);
  clickText(handle.container, '全部出发 · 生成指令');
  assert.deepEqual(lastExport(handle.ui), [LINE.t2]);

  const file = mountPage(snap('full-orca'), 'dispatch');
  assert.equal(clickableByText(file.container, '全部出发 · 生成指令').length, 1);
  assert.equal(clickableByText(file.container, '全部出发').length, 0, '本机文件打开也用规定的降级文案');
  assert.equal(clickableByText(file.container, '全部出发（0）').length, 0);
});

pageTest('P6b 待处理 · 打字时底栏「发送已选」计数和可用状态跟着更新（P6b-r1 R4）', function () {
  const handle = channelPage(withReplySlots(snap('full-orca')), 'pending');
  choose(card(handle, 'N1020-03'), '打回');
  const idle = clickableByText(handle.container, '发送已选（0）')[0];
  assert.ok(idle && idle.disabled, '没写原因时底部不可发');
  typeNote(card(handle, 'N1020-03'), '深色模式下导出按钮看不清');
  const ready = clickableByText(handle.container, '发送已选（1）')[0];
  assert.ok(ready && !ready.disabled, '写了原因，底栏计数和可用状态要跟着变');
});

pageTest('P6b 待处理 · 打字从有效改成超过 500 字、再改回有效，底栏「发送已选」计数和可用状态都跟着变（P6b-r2 R4）', async function () {
  const handle = channelPage(withReplySlots(snap('full-orca')), 'pending');
  choose(card(handle, 'N1020-03'), '打回');
  typeNote(card(handle, 'N1020-03'), '深色模式下导出按钮看不清');
  assert.ok(!clickableByText(handle.container, '发送已选（1）')[0].disabled, '有效原因：底部可发 1 条');
  typeNote(card(handle, 'N1020-03'), 'x'.repeat(501));
  const over = clickableByText(handle.container, '发送已选（0）')[0];
  assert.ok(over && over.disabled, '超过 500 字：底部变成 0 条、不可点');
  assert.ok(clickableByText(card(handle, 'N1020-03'), '发送这一条')[0].disabled, '行内发送也不可点');
  assert.equal(noteField(card(handle, 'N1020-03')).value, 'x'.repeat(501), '重画后输入框里的字还在');
  typeNote(card(handle, 'N1020-03'), 'x'.repeat(500));
  assert.ok(!clickableByText(handle.container, '发送已选（1）')[0].disabled, '改回 500 字以内：底部恢复可发 1 条');
  clickText(handle.container, '发送已选（1）');
  await settle(20);
  assert.equal(handle.posts.length, 1, '恢复后底部真能发出');
  assert.equal(handle.posts[0].body.text, 'x'.repeat(500));
});

pageTest('P6b 待处理 · 发送这一条等结果时又写了新原因：成功回来后输入框和决定都留着，只清这次发出去的（P6b-r2 R1）', async function () {
  let release = null;
  const handle = channelPage(withReplySlots(snap('full-orca')), 'pending', function () {
    return new Promise(function (resolve) {
      release = function () { resolve({ ok: true, id: 'N1021-11', status: 'delivered', message: '已送达主会话 · 编号 N1021-11' }); };
    });
  });
  choose(card(handle, 'N1020-03'), '打回');
  typeNote(card(handle, 'N1020-03'), 'first reason');
  clickText(card(handle, 'N1020-03'), '发送这一条');
  await settle(4);
  assert.equal(handle.posts.length, 1);
  typeNote(card(handle, 'N1020-03'), 'second reason while sending');
  release();
  await settle(20);
  assert.deepEqual(plainPosts(handle), [{ card: 'pending:N1020-03', quick: 'reject', text: 'first reason', lines: null }]);
  const row = card(handle, 'N1020-03');
  assert.equal(noteField(row).value, 'second reason while sending', '发送期间新写的字不能从输入框消失');
  assert.equal(hasSelectionMarker(row), true, '对应的决定也要留着');
  assert.equal(replyState(row), '已送达主会话');
  assert.ok(!clickableByText(handle.container, '发送已选（1）')[0].disabled, '新写的原因还能接着发');
});

pageTest('P6b 待处理 · 发送期间没改动：成功回来后清掉这一行的决定和原因（P6b-r2 R1 对照）', async function () {
  const handle = channelPage(withReplySlots(snap('full-orca')), 'pending');
  choose(card(handle, 'N1020-03'), '打回');
  typeNote(card(handle, 'N1020-03'), 'first reason');
  clickText(card(handle, 'N1020-03'), '发送这一条');
  await settle(20);
  assert.equal(handle.posts.length, 1);
  const row = card(handle, 'N1020-03');
  assert.equal(noteField(row) ? noteField(row).value : '', '', '发出去的原因清掉');
  assert.equal(hasSelectionMarker(row), false, '发出去的决定清掉');
  assert.ok(clickableByText(handle.container, '发送已选（0）')[0].disabled);
});

function readCss() {
  return fs.readFileSync(CSS_PATH, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
}

function declarations(css, property) {
  const pattern = new RegExp('(?:^|[;{\\s])' + property + '\\s*:\\s*([^;}]+)', 'gi');
  const out = [];
  let match;
  while ((match = pattern.exec(css))) {
    out.push(match[1].trim());
  }
  return out;
}

function mediaBlocks(css) {
  const blocks = [];
  const pattern = /@media([^{]+)\{/g;
  let match;
  while ((match = pattern.exec(css))) {
    let depth = 1;
    let index = pattern.lastIndex;
    while (index < css.length && depth > 0) {
      const char = css[index];
      if (char === '{') {
        depth += 1;
      } else if (char === '}') {
        depth -= 1;
      }
      index += 1;
    }
    blocks.push({ query: match[1].trim(), body: css.slice(pattern.lastIndex, index - 1) });
  }
  return blocks;
}

test('actions.css：字号只用 12/13/14/16/20/28 六级', { skip: CSS_SKIP }, function () {
  const css = readCss();
  const sizes = declarations(css, 'font-size');
  const shorthands = declarations(css, 'font');
  shorthands.forEach(function (value) {
    const size = value.match(/var\(--fs-\d+\)|\b\d+(?:\.\d+)?px\b/);
    if (size) {
      sizes.push(size[0]);
    }
  });
  assert.ok(sizes.length > 0, 'actions.css 至少要声明一处字号');
  const allowed = new Set([
    '12px', '13px', '14px', '16px', '20px', '28px',
    'var(--fs-12)', 'var(--fs-13)', 'var(--fs-14)', 'var(--fs-16)', 'var(--fs-20)', 'var(--fs-28)'
  ]);
  sizes.forEach(function (value) {
    const normalized = value.replace(/\s+/g, '');
    assert.ok(allowed.has(normalized), '非法字号：' + value + '（只允许六级或 --fs-* token）');
  });
});

test('actions.css：颜色走 ui-tokens，不硬编码十六进制色值', { skip: CSS_SKIP }, function () {
  const css = readCss();
  const properties = ['color', 'background', 'background-color', 'border-color', 'border-left-color', 'border-top-color'];
  properties.forEach(function (property) {
    declarations(css, property).forEach(function (value) {
      const lower = value.toLowerCase();
      if (/^(transparent|inherit|initial|unset|none|currentcolor|0|auto)$/.test(lower)) {
        return;
      }
      assert.ok(
        lower.indexOf('var(--') !== -1,
        property + ' 必须使用 tokens：' + value
      );
      assert.equal(
        /#[0-9a-f]{3,8}\b/i.test(lower),
        false,
        property + ' 不应内联十六进制色值：' + value
      );
    });
  });
  ['--red', '--orange', '--blue'].forEach(function (token) {
    assert.ok(css.indexOf('var(' + token) !== -1, '三栏状态色要用 token ' + token);
  });
});

test('actions.css：窄屏单列、无外部资源与动画', { skip: CSS_SKIP }, function () {
  const css = readCss();
  const blocks = mediaBlocks(css);
  const narrow = blocks.filter(function (block) {
    return /max-width\s*:\s*\d+px/.test(block.query);
  });
  assert.ok(narrow.length > 0, '缺少窄屏响应（max-width）');
  const singleColumn = narrow.some(function (block) {
    if (/flex-direction\s*:\s*column/.test(block.body)) {
      return true;
    }
    const pattern = /grid-template-columns\s*:\s*([^;}]+)/g;
    let match;
    while ((match = pattern.exec(block.body))) {
      const value = match[1].trim();
      const withoutFns = value.replace(/[a-z-]+\([^)]*\)/gi, 'FN');
      const tracks = withoutFns.split(/\s+/).filter(Boolean);
      if (tracks.length === 1 || /^minmax\(|^1fr$|^100%$|^auto$/i.test(value)) {
        return true;
      }
    }
    return false;
  });
  assert.ok(singleColumn, '窄屏必须把看板/卡片改为单列');

  assert.equal(/@import/.test(css), false, '不能 @import 外部样式');
  assert.equal(/url\(\s*['"]?(?:https?:)?\/\//i.test(css), false, '不能引用外部资源');
  assert.equal(/@keyframes/.test(css), false, '不能有动画关键帧');
  assert.equal(/(?:^|[;{\s])animation\s*:/i.test(css), false, '不能有动画（PRD 6 不打扰）');
});


// ============================================ v1.1 P6a 第 1 轮审查修复（R1、R5、R6）

pageTest('P6a-R1：按默认全部组队用默认方案，覆盖没组队时手改的执行器；单独组队仍按改过的方案', function () {
  const t2 = DAILY + '#T2';
  const handle = mountPage(snap('full-orca'), 'dispatch');
  clickText(card(handle, t2), 'OpenCode');
  clickText(handle.container, '按默认全部组队');
  const lines = dispatchAll(handle);
  assert.ok(lines.indexOf(LINE.t2) !== -1, '默认方案应是 Codex：' + lines.join(' / '));
  assert.equal(lines.some(function (line) { return line.indexOf('T2 只读排查示例') !== -1 && line.indexOf('OpenCode') !== -1; }), false);

  const single = mountPage(snap('full-orca'), 'dispatch');
  clickText(card(single, t2), 'OpenCode');
  clickText(card(single, t2), '组队');
  const kept = dispatchAll(single);
  assert.deepEqual(kept, ['【控制台指令】确认派发：示例项目A：T2 只读排查示例 · 角色 排查 · 执行器 OpenCode · 现有目录']);
});

pageTest('P6a-R5：快捷决定按钮不截断文字（不设 max-width / 省略号）', function () {
  const css = readCss();
  const rule = css.match(/\.act-chip\s*\{([^}]*)\}/);
  assert.ok(rule, '缺少 .act-chip 规则');
  assert.equal(/text-overflow\s*:\s*ellipsis/.test(rule[1]), false, '按钮文字不能省略号截断');
  assert.equal(/max-width\s*:\s*\d/.test(rule[1]), false, '按钮不能限宽裁切');
});

pageTest('P6a-R6：展开行给行内被截断字段的全文（待处理项目：事项、派发确认项目 · 任务号）', function () {
  const pending = mountPage(snap('full-orca'), 'pending');
  const row = card(pending, 'N1020-01');
  const full = descendantsOf(row).filter(function (node) {
    return /(?:^|\s)act-x-item(?:\s|$)/.test(node.getAttribute('class') || '');
  })[0];
  assert.ok(full, '待处理展开行缺少项目：事项全文');
  assert.equal(normText(full.textContent), '示例项目A：控制台范围');

  const dispatch = mountPage(snap('full-orca'), 'dispatch');
  const quest = card(dispatch, DAILY + '#T2');
  assert.ok(descendantsOf(quest).some(function (node) {
    return /(?:^|\s)act-x-item(?:\s|$)/.test(node.getAttribute('class') || '') &&
      normText(node.textContent) === '示例项目A · T2';
  }), '派发确认展开行缺少项目 · 任务号');
});
