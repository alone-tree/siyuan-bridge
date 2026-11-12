"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

// 加载实际入口；菜单替身仅提供思源菜单构造与 DOM 节点移动接口。
async function loadEntry(entry, storage = {}) {
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
    async loadData(name) { return storage[name]; }
    async saveData(name, data) {
      if (state.saveError) throw new Error(state.saveError);
      if (state.saveResult) return state.saveResult;
      storage[name] = {...data};
      return {code: 0};
    }
  }
  const sandbox = {
    module: {exports: {}},
    require(name) {
      assert.equal(name, "siyuan");
      return {Plugin, showMessage: (...args) => messages.push(args), getAllEditor() { return []; }};
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
    source = source.replace(/^import .*;\r?\n/, 'const {Plugin, showMessage, getAllEditor} = require("siyuan");\n')
      .replace("export default class SiyuanBridgePlugin", "class SiyuanBridgePlugin");
  }
  source += `
    module.exports = {SiyuanBridgePlugin, addFullPathCopyMenu, bindFullPathCopyToggle, renderHome};
    createBlockIndexController = () => ({bind() {}, unbind() {}});
    ensureDefaultBridgeConfig = async () => {};
    ensureTelemetryConfig = async () => {};
    ensureSystemNotebook = async () => null;
    prepareImageDependency = async () => {};
  `;
  vm.runInNewContext(source, sandbox, {filename});
  const plugin = new sandbox.module.exports.SiyuanBridgePlugin();
  plugin.onload();
  await plugin.fullPathCopyReady;
  return {
    menu, anchor, copyId, root, copy, requests, writes, messages, state, plugin, listeners, storage,
    renderHome: sandbox.module.exports.renderHome,
    async bindToggle() {
      const handlers = {};
      const checkbox = {checked: false, disabled: false,
        addEventListener(name, handler) { handlers[name] = handler; },
      };
      await sandbox.module.exports.bindFullPathCopyToggle({querySelector() { return checkbox; }}, plugin);
      return {checkbox, async change(enabled) {
        checkbox.checked = enabled;
        await handlers.change();
      }};
    },
    open(detail = {type: "doc", items: [{id: "target-doc"}]}) {
      listeners.get("open-menu-doctree")({detail});
    },
    item() { return copy.children.find(item => item.id === "siyuanBridgeCopyFullPath"); },
  };
}

for (const entry of ["index.js", "src/index.js"]) {
  test(`${entry}: 菜单相邻、重复事件不重复插入、点击实时读取目标路径`, async () => {
    const app = await loadEntry(entry);
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

  test(`${entry}: 不扩展笔记本、多选及无目标的菜单`, async () => {
    const app = await loadEntry(entry);
    for (const type of ["notebook", "notebooks", "docs", "items"]) {
      app.open({type, items: [{id: "target-doc"}]});
    }
    app.open({type: "doc"});
    app.open(null);
    assert.equal(app.item(), undefined);
    app.anchor.remove();
    app.open();
    assert.equal(app.item(), undefined);
    assert.equal(app.requests.length, 0);
  });

  test(`${entry}: 从旧版事件元素取得右键文档 ID`, async () => {
    const app = await loadEntry(entry);
    app.open({type: "doc", elements: [{getAttribute(name) {
      assert.equal(name, "data-node-id");
      return "old-event-doc";
    }}]});
    await app.item().options.click();
    assert.equal(app.requests[0].body.id, "old-event-doc");
  });

  test(`${entry}: 接口异常和无效路径不覆盖剪贴板`, async () => {
    const app = await loadEntry(entry);
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
    const app = await loadEntry(entry);
    app.open();
    app.state.clipboardError = "剪贴板被拒绝";
    await app.item().options.click();
    assert.deepEqual(app.writes, []);
    assert.equal(app.messages.length, 1);
    assert.ok(app.messages[0][0].includes("剪贴板被拒绝"));
    assert.equal(app.messages[0][2], "error");
  });

  for (const lifecycle of ["onunload", "uninstall"]) {
    test(`${entry}: ${lifecycle} 移除菜单及事件监听`, async () => {
      const app = await loadEntry(entry);
      app.open();
      app.plugin[lifecycle]();
      assert.equal(app.listeners.has("open-menu-doctree"), false);
      assert.deepEqual(app.copy.children, [app.anchor, app.copyId]);
    });
  }

  test(`${entry}: 默认开启，面板开关文案和位置正确`, async () => {
    const app = await loadEntry(entry);
    const toggle = await app.bindToggle();
    assert.equal(toggle.checkbox.checked, true);
    assert.equal(toggle.checkbox.disabled, false);
    const html = app.renderHome();
    const blockIndex = html.indexOf('data-block-index="checkbox"');
    const fullPath = html.indexOf('data-full-path-copy="checkbox"');
    const images = html.indexOf('data-inline-images="checkbox"');
    assert.ok(blockIndex < fullPath && fullPath < images);
    assert.ok(html.includes('>复制完整路径</span>'));
    assert.ok(html.includes('复制包含笔记本名的完整可读路径，方便直接发给 AI。'));
  });

  test(`${entry}: 关闭后隐藏，重新加载保持关闭，开启后恢复菜单`, async () => {
    const app = await loadEntry(entry);
    const toggle = await app.bindToggle();
    app.open();
    await toggle.change(false);
    app.open();
    assert.equal(app.item(), undefined);
    assert.deepEqual(app.storage, {"copy-full-path.json": {enabled: false}});
    const reloaded = await loadEntry(entry, app.storage);
    const reloadedToggle = await reloaded.bindToggle();
    reloaded.open();
    assert.equal(reloadedToggle.checkbox.checked, false);
    assert.equal(reloaded.item(), undefined);
    await reloadedToggle.change(true);
    reloaded.open();
    assert.ok(reloaded.item());
    const enabledAgain = await loadEntry(entry, app.storage);
    assert.equal(enabledAgain.plugin.fullPathCopyEnabled, true);
  });

  test(`${entry}: 保存异常或内核错误时恢复开关并保留原状态`, async () => {
    const app = await loadEntry(entry);
    const toggle = await app.bindToggle();
    app.open();
    app.state.saveError = "无法保存";
    await toggle.change(false);
    assert.equal(toggle.checkbox.checked, true);
    assert.equal(toggle.checkbox.disabled, false);
    assert.equal(app.plugin.fullPathCopyEnabled, true);
    assert.ok(app.item());
    delete app.state.saveError;
    app.state.saveResult = {code: -1, msg: "写入被拒绝"};
    await toggle.change(false);
    assert.equal(toggle.checkbox.checked, true);
    assert.equal(app.plugin.fullPathCopyEnabled, true);
    assert.deepEqual(app.storage, {});
    assert.ok(app.messages.every(message => message[2] === "error"));
  });
}
