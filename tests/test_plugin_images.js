"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const {webcrypto, createHash} = require("node:crypto");

const payload = Buffer.from("pinned binary package");
const hash = createHash("sha256").update(payload).digest("hex");
const wheel = {filename: "pillow-13.0.0-cp313-cp313-win_amd64.whl",
  url: "https://files.pythonhosted.org/packages/example.whl", sha256: hash, size: payload.length};
const runtime = {tag: "cp313-win-amd64", version: "13.0.0", filename: wheel.filename};

function load(entry, {state = runtime, installed = false, cached = false, failSources = 0} = {}) {
  const requests = [], messages = [], downloads = [], saved = [];
  const sandbox = {
    module: {exports: {}},
    require(name) {
      assert.equal(name, "siyuan");
      return {Plugin: class {}, showMessage: (...args) => messages.push(args), getAllEditor() { return []; }};
    },
    navigator: {platform: "Win32"},
    crypto: webcrypto, URL, Blob, FormData, Uint8Array, atob,
    console: {warn() {}},
    async fetch(url, options) {
      if (url === "/api/file/getFile") {
        const {path: file} = JSON.parse(options.body);
        requests.push(file);
        if (file.endsWith(".json")) return {ok: true, async json() { return {version: "13.0.0", wheels: [wheel]}; }};
        if (file.endsWith(".ready")) return {ok: installed, async text() { return hash; }};
        return {ok: cached, async arrayBuffer() { return Uint8Array.from(payload).buffer; }};
      }
      if (url === "/api/network/forwardProxy") {
        const request = JSON.parse(options.body);
        downloads.push(request);
        const data = downloads.length <= failSources
          ? {status: 200, body: Buffer.from("wrong hash").toString("base64")}
          : {status: 200, body: payload.toString("base64")};
        return {ok: true, async json() { return {code: 0, data}; }};
      }
      if (url === "/api/file/putFile") {
        saved.push({path: options.body.get("path"), file: options.body.get("file")});
        return {ok: true, async json() { return {code: 0}; }};
      }
      throw new Error(`unexpected request ${url}`);
    },
  };
  let source = fs.readFileSync(path.join(__dirname, "..", "siyuan-plugin", entry), "utf8");
  if (entry.startsWith("src/")) source = source.replace(/^import .*;\r?\n/, 'const {Plugin, showMessage, getAllEditor} = require("siyuan");\n')
    .replace("export default class SiyuanBridgePlugin", "class SiyuanBridgePlugin");
  source += "\nmodule.exports = {prepareImageDependency};\n";
  vm.runInNewContext(source, sandbox);
  return {requests, messages, downloads, saved,
    run: () => sandbox.module.exports.prepareImageDependency({async loadData() { return state; }})};
}

for (const entry of ["index.js", "src/index.js"]) {
  test(`${entry}: 组件已安装时无网络和提示`, async () => {
    const app = load(entry, {installed: true});
    await app.run();
    assert.equal(app.downloads.length, 0);
    assert.equal(app.messages.length, 0);
  });
  test(`${entry}: 已下载但未解包时复用缓存`, async () => {
    const app = load(entry, {cached: true});
    await app.run();
    assert.equal(app.downloads.length, 0);
    assert.equal(app.messages.length, 0);
  });
  test(`${entry}: 首次安装提示自动准备且不猜 Python 版本`, async () => {
    const app = load(entry, {state: null});
    await app.run();
    assert.equal(app.requests.length, 0);
    assert.match(app.messages[0][0], /首次连接 AI 时自动准备/);
  });
  test(`${entry}: 两个来源校验失败后从第三个下载并只写私有目录`, async () => {
    const app = load(entry, {failSources: 2});
    await app.run();
    assert.equal(app.downloads.length, 3);
    assert.ok(app.downloads[1].url.startsWith("https://pypi.tuna.tsinghua.edu.cn/"));
    assert.ok(app.downloads[2].url.startsWith("https://mirrors.aliyun.com/"));
    assert.equal(app.saved.length, 1);
    assert.equal(app.saved[0].path, `/data/storage/petal/siyuan-bridge/deps/downloads/${wheel.filename}`);
    assert.deepEqual(Buffer.from(await app.saved[0].file.arrayBuffer()), payload);
  });
  test(`${entry}: 全部来源失败时不保存错误组件`, async () => {
    const app = load(entry, {failSources: 3});
    await assert.rejects(app.run(), /下载失败/);
    assert.equal(app.saved.length, 0);
  });
}
