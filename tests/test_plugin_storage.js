"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const entryPath = path.join(__dirname, "..", "siyuan-plugin", "index.js");
const source = fs.readFileSync(entryPath, "utf8")
  + "\nmodule.exports.__storageTest = {loadPluginData, normalizeLineEndings, ensureTelemetryConfig};\n";

function loadEntry(fetchImpl) {
  class Plugin {}
  const sandbox = {
    module: {exports: {}},
    exports: {},
    require(name) {
      assert.strictEqual(name, "siyuan");
      return {
        Dialog: class Dialog {},
        Plugin,
        showMessage() {},
        getAllEditor() { return []; },
      };
    },
    fetch: fetchImpl,
    console: {warn() {}, error() {}, log() {}},
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(source, sandbox, {filename: entryPath});
  return sandbox.module.exports.__storageTest;
}

async function testPersistentDataWins() {
  let saves = 0;
  const api = loadEntry(async () => {
    throw new Error("legacy file must not be read");
  });
  const plugin = {
    async loadData() { return {source: "petal"}; },
    async saveData() { saves += 1; },
  };
  const loaded = await api.loadPluginData(plugin, "config.local.json", "/legacy/config.json");
  assert.strictEqual(loaded.source, "petal");
  assert.strictEqual(saves, 0);
}

async function testLegacyDataMigratesOnce() {
  let requestBody = null;
  const api = loadEntry(async (_url, options) => {
    requestBody = JSON.parse(options.body);
    return {async text() { return JSON.stringify({source: "legacy"}); }};
  });
  const saves = [];
  const plugin = {
    async loadData() { return ""; },
    async saveData(name, data) { saves.push([name, data]); },
  };
  const loaded = await api.loadPluginData(plugin, "telemetry.json", "/legacy/telemetry.json");
  assert.strictEqual(loaded.source, "legacy");
  assert.strictEqual(requestBody.path, "/legacy/telemetry.json");
  assert.strictEqual(saves.length, 1);
  assert.strictEqual(saves[0][0], "telemetry.json");
  assert.strictEqual(saves[0][1].source, "legacy");
}

async function testLegacyReadSurvivesMigrationWriteFailure() {
  const api = loadEntry(async () => ({
    async text() { return JSON.stringify({source: "legacy-readonly"}); },
  }));
  const plugin = {
    async loadData() { return null; },
    async saveData() { throw new Error("readonly"); },
  };
  const loaded = await api.loadPluginData(plugin, "system_state.json", "/legacy/state.json");
  assert.strictEqual(loaded.source, "legacy-readonly");
}

function testLineEndingNormalization() {
  const api = loadEntry(async () => { throw new Error("unused"); });
  assert.strictEqual(api.normalizeLineEndings("a\r\nb\r"), "a\nb\n");
}

async function testTelemetryIdFallbackFromStats() {
  const requested = [];
  const api = loadEntry(async (_url, options) => {
    const body = JSON.parse(options.body);
    requested.push(body.path);
    if (String(body.path).endsWith("/stats/telemetry_id")) {
      return {async text() { return "legacyid123"; }};
    }
    throw new Error(`unexpected path ${body.path}`);
  });
  const saves = [];
  const plugin = {
    async loadData() { return {}; },
    async saveData(name, data) { saves.push([name, data]); },
  };
  await api.ensureTelemetryConfig(plugin);
  assert.strictEqual(saves.length, 1);
  assert.strictEqual(saves[0][0], "telemetry.json");
  assert.strictEqual(saves[0][1].anonymous_id, "legacyid123");
  assert.ok(requested[0].includes("/data/storage/petal/siyuan-bridge/stats/telemetry_id"));
}

(async () => {
  await testPersistentDataWins();
  await testLegacyDataMigratesOnce();
  await testLegacyReadSurvivesMigrationWriteFailure();
  testLineEndingNormalization();
  await testTelemetryIdFallbackFromStats();
  process.stdout.write("plugin storage tests passed\n");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
