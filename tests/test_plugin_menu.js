"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

// 加载实际入口；菜单替身仅提供思源菜单构造与 DOM 节点移动接口。
function loadEntry(entry) {
  const root = {children: []};
  const copy = {children: []};
  root.children.push(copy);
  function node(id, parent) {
    const element = {
      id, parent,
      remove() {
        this.parent.children.splice(this.parent.children.indexOf(this), 1);
      },
      after(item) {
        item.remove();
        item.parent = this.parent;
        this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, item);
      },
    };
    parent.children.push(element);
    return element;
  }
  const anchor = node("copyHPath", copy);
  const copyId = node("copyID", copy);
  const menu = {
    element: {
      querySelector(selector) {
        return [...root.children, ...copy.children].find(item =>
          selector === `[data-id="${item.id}"]`) || null;
      },
    },
    addItem(options) {
      const item = node(options.id, root);
      item.options = options;
      return item;
    },
  };
  const requests = [], writes = [], messages = [];
  const listeners = new Map();
  const state = {response: {code: 0, data: "商业实践/父文档/当前文档"}};
  class Plugin {
    constructor() {
      this.eventBus = {
        on(name, fn) { listeners.set(name, fn); },
        off(name, fn) {
          assert.equal(listeners.get(name), fn);
          listeners.delete(name);
        },
      };
    }
    addCommand() {}
  }
  const sandbox = {
    module: {exports: {}},
    require(name) {
      assert.equal(name, "siyuan");
      return {Plugin, showMessage: (...args) => messages.push(args)};
    },
    window: {siyuan: {menus: {menu}}},
    navigator: {clipboard: {async writeText(text) {
      if (state.clipboardError) throw new Error(state.clipboardError);
      writes.push(text);
    }}},
    async fetch(url, options) {
      requests.push({url, body: JSON.parse(options.body)});
      if (state.networkError) throw new Error(state.networkError);
      return {async json() { return state.response; }};
    },
  };
  const filename = path.join(__dirname, "..", "siyuan-plugin", entry);
  let source = fs.readFileSync(filename, "utf8");
  if (entry.startsWith("src/")) {
    source = source.replace(/^import .*;\r?\n/, 'const {Plugin, showMessage} = require("siyuan");\n')
      .replace("export default class SiyuanBridgePlugin", "class SiyuanBridgePlugin");
  }
  source += `
    module.exports = {SiyuanBridgePlugin, addFullPathCopyMenu};
    createBlockIndexController = () => ({bind() {}, unbind() {}});
    ensureDefaultBridgeConfig = async () => {};
    ensureTelemetryConfig = async () => {};
    ensureSystemNotebook = async () => null;
  `;
  vm.runInNewContext(source, sandbox, {filename});
  const plugin = new sandbox.module.exports.SiyuanBridgePlugin();
  plugin.onload();
  return {
    menu, anchor, copyId, root, copy, requests, writes, messages, state, plugin, listeners,
    open(detail = {type: "doc", items: [{id: "target-doc"}]}) {
      listeners.get("open-menu-doctree")({detail});
    },
    item() { return copy.children.find(item => item.id === "siyuanBridgeCopyFullPath"); },
  };
}

for (const entry of ["index.js", "src/index.js"]) {
  test(`${entry}: 菜单相邻、重复事件不重复插入、点击实时读取目标路径`, async () => {
    const app = loadEntry(entry);
    app.open();
    app.open();
    assert.deepEqual(app.copy.children.map(item => item.id),
      ["copyHPath", "siyuanBridgeCopyFullPath", "copyID"]);
    assert.equal(app.copy.children[0], app.anchor);
    assert.equal(app.copy.children[2], app.copyId);
    assert.equal(app.root.children.length, 1);
    assert.equal(app.item().options.label, "复制完整路径(思源桥)");
    assert.equal(app.requests.length, 0);
    await app.item().options.click();
    app.state.response.data = "/商业实践/移动后的父文档/改名后的文档";
    await app.item().options.click();
    assert.deepEqual(app.writes,
      ["/商业实践/父文档/当前文档", "/商业实践/移动后的父文档/改名后的文档"]);
    assert.deepEqual(app.requests, [
      {url: "/api/filetree/getFullHPathByID", body: {id: "target-doc"}},
      {url: "/api/filetree/getFullHPathByID", body: {id: "target-doc"}},
    ]);
    assert.deepEqual(app.messages.map(item => item[0]), ["已复制完整路径", "已复制完整路径"]);
  });

  test(`${entry}: 不扩展笔记本、多选及无目标的菜单`, () => {
    const app = loadEntry(entry);
    for (const type of ["notebook", "notebooks", "docs", "items"]) {
      app.open({type, items: [{id: "target-doc"}]});
    }
    app.open({type: "doc"});
    app.open(undefined);
    assert.equal(app.item(), undefined);
    app.anchor.remove();
    app.open();
    assert.equal(app.item(), undefined);
    assert.equal(app.requests.length, 0);
  });

  test(`${entry}: 从旧版事件元素取得右键文档 ID`, async () => {
    const app = loadEntry(entry);
    app.open({type: "doc", elements: [{getAttribute(name) {
      assert.equal(name, "data-node-id");
      return "old-event-doc";
    }}]});
    await app.item().options.click();
    assert.equal(app.requests[0].body.id, "old-event-doc");
  });

  test(`${entry}: 接口异常和无效路径不覆盖剪贴板`, async () => {
    const app = loadEntry(entry);
    app.open();
    for (const response of [{code: -1, msg: "文档不存在"},
      {code: 0, data: null}, {code: 0, data: ""}, {code: 0, data: {path: "错误格式"}}]) {
      app.state.response = response;
      await app.item().options.click();
    }
    app.state.networkError = "网络不可达";
    await app.item().options.click();
    assert.deepEqual(app.writes, []);
    assert.equal(app.messages.length, 5);
    assert.ok(app.messages.every(message => message[2] === "error"));
    assert.ok(app.messages[0][0].includes("文档不存在"));
    assert.ok(app.messages[4][0].includes("网络不可达"));
  });

  test(`${entry}: 剪贴板失败只报告失败`, async () => {
    const app = loadEntry(entry);
    app.open();
    app.state.clipboardError = "剪贴板被拒绝";
    await app.item().options.click();
    assert.deepEqual(app.writes, []);
    assert.equal(app.messages.length, 1);
    assert.ok(app.messages[0][0].includes("剪贴板被拒绝"));
    assert.equal(app.messages[0][2], "error");
  });

  for (const lifecycle of ["onunload", "uninstall"]) {
    test(`${entry}: ${lifecycle} 移除菜单及事件监听`, () => {
      const app = loadEntry(entry);
      app.open();
      app.plugin[lifecycle]();
      assert.equal(app.listeners.has("open-menu-doctree"), false);
      assert.deepEqual(app.copy.children, [app.anchor, app.copyId]);
    });
  }
}
