const {Dialog, Plugin, showMessage} = require("siyuan");

const PLUGIN_NAME = "siyuan-bridge";
const CONFIG_PATH = `/data/plugins/${PLUGIN_NAME}/bridge/config.local.json`;
const TELEMETRY_PATH = `/data/plugins/${PLUGIN_NAME}/bridge/telemetry.json`;
const SYSTEM_STATE_PATH = `/data/plugins/${PLUGIN_NAME}/bridge/knowledge_base/system_state.json`;
const SYSTEM_TEMPLATE_ROOT = `/data/plugins/${PLUGIN_NAME}/bridge/templates/system-docs`;
const DEFAULT_ENDPOINT = "https://siyuanbridgetelemetry.zingerplayground.top";
const DEFAULT_CONFIG = {
  profiles: [{name: "当前工作空间", token: ""}],
  language: "zh-CN",
};
const SYSTEM_NOTEBOOK_NAMES = {
  "zh-CN": "思源桥",
  en: "SiYuan Bridge",
};
const LEGACY_SYSTEM_NOTEBOOK_NAMES = ["思源代理桥", "SiYuan Agent Bridge"];
const SYSTEM_DOC_NAMES = {
  ai_guide: {"zh-CN": "用户个性化要求", en: "User Preferences"},
  mcp_usage_guide: {"zh-CN": "MCP 使用指南", en: "MCP Usage Guide"},
  workspace_index_guide: {"zh-CN": "工作空间索引创建指南", en: "Workspace Index Guide"},
  workspace_index: {"zh-CN": "工作空间索引", en: "Workspace Index"},
  about: {"zh-CN": "关于思源桥", en: "About SiYuan Bridge"},
  privacy_rules: {"zh-CN": "隐私规则", en: "Privacy Rules"},
};
const LEGACY_SYSTEM_DOC_NAMES = {
  ai_guide: ["AI 使用指南", "AI Guide"],
  mcp_usage_guide: ["MCP使用指南"],
  about: ["关于思源代理桥", "About SiYuan Agent Bridge", "关于Siyuan Agent Bridge"],
};
const SYSTEM_BOOTSTRAP_FILES = {
  ai_guide: {
    "zh-CN": "user-preferences.zh-CN.md",
    en: "user-preferences.en.md",
  },
  workspace_index: {
    "zh-CN": "workspace-index-placeholder.zh-CN.md",
    en: "workspace-index-placeholder.en.md",
  },
  about: {
    "zh-CN": "about.zh-CN.md",
    en: "about.en.md",
  },
  privacy_rules: {
    "zh-CN": "privacy-rules.zh-CN.md",
    en: "privacy-rules.en.md",
  },
};
const LEGACY_AI_GUIDE_HASHES = new Set([
  "39576ef97d8e9d319aa346ddd80265629b8f72d8de9fb08447f08ed2954205df",
  "a3e3c01d4925547b747e5342fafbfff8dab0e77eaa345db6eab49e2aed3412a9",
]);
const SYSTEM_STATE_SCHEMA_VERSION = 2;

class SiyuanBridgePlugin extends Plugin {
  onload() {
    this.setting = {
      open: () => this.openHome(),
    };

    this.addCommand({
      langKey: "openSiyuanBridgeSettings",
      langText: "打开思源桥面板",
      hotkey: "",
      callback: () => this.openHome(),
    });

    ensureDefaultBridgeConfig().catch((error) => {
      console.warn("Siyuan Bridge config init failed", error);
    });
    ensureTelemetryConfig().catch((error) => {
      console.warn("Siyuan Bridge telemetry init failed", error);
    });
    this.systemNotebookMaintenance = ensureSystemNotebook().catch((error) => {
      console.warn("Siyuan Bridge system notebook init failed", error);
      return null;
    });
  }

  onLayoutReady() {
    this.addTopBar({
      icon: "iconSettings",
      title: "思源桥",
      position: "right",
      callback: () => this.openHome(),
    });
    this.systemNotebookMaintenance?.then((documentCache) => {
      if (documentCache) showDuplicateSystemDocuments(documentCache);
    });
  }

  async openHome() {
    const dialog = new Dialog({
      title: "思源桥",
      content: renderHome(),
      width: "680px",
      height: "620px",
    });
    bindHome(dialog.element, this);
  }

  async openMcpSettings() {
    const context = await getPluginContext();
    const config = await loadBridgeConfig(context);
    const dialog = new Dialog({
      title: "MCP 配置",
      content: renderSettings(config, context),
      width: "760px",
      height: "720px",
    });
    bindSettings(dialog.element, config, context);
  }
}

// ---------------------------------------------------------------------------
// Home Dialog
// ---------------------------------------------------------------------------

function renderHome() {
  return `
    <div class="siyuan-bridge-home">
      <div class="siyuan-bridge-home__section">
        <div class="siyuan-bridge-home__section-title">通知</div>
        <div class="siyuan-bridge-home__notifications" data-area="notifications">
          <div class="siyuan-bridge-home__loading">加载中...</div>
        </div>
      </div>

      <div class="siyuan-bridge-home__section">
        <div class="siyuan-bridge-home__section-title">用户体验改进</div>
        <label class="siyuan-bridge-home__checkbox-row">
          <input class="b3-switch" type="checkbox" data-telemetry="checkbox" />
          <span class="siyuan-bridge-home__checkbox-label">加入用户体验改进计划</span>
        </label>
        <p class="siyuan-bridge-home__hint">
          匿名收集工具使用数据（功能调用、成功率等），帮助我们改进思源桥。不包含任何笔记内容或个人身份信息。
        </p>
        <div data-telemetry="local-copy-area" style="display:none">
          <label class="siyuan-bridge-home__checkbox-row">
            <input class="b3-switch" type="checkbox" data-telemetry="local-copy" />
            <span class="siyuan-bridge-home__checkbox-label">遥测数据保留本地副本</span>
          </label>
          <p class="siyuan-bridge-home__hint">
            在 stats/events/ 目录保留每日 JSONL 文件，方便自行查看上传内容，打消隐私顾虑。
          </p>
        </div>
      </div>

      <div class="siyuan-bridge-home__section">
        <div class="siyuan-bridge-home__section-title">MCP 配置</div>
        <p class="siyuan-bridge-home__hint">配置 Python 路径、工作空间 Token 并生成 MCP JSON。</p>
        <button class="b3-button" data-action="open-mcp-settings">打开 MCP 配置</button>
      </div>

      <div class="siyuan-bridge-home__section">
        <div class="siyuan-bridge-home__section-title">系统指南</div>
        <p class="siyuan-bridge-home__hint">指南允许你在思源中修改。重置会保留原文档 ID，并恢复为当前插件内置内容。</p>
        <div data-area="system-guides">
          <div class="siyuan-bridge-home__loading">加载中...</div>
        </div>
      </div>

      <div class="siyuan-bridge-home__section">
        <div class="siyuan-bridge-home__section-title">提交反馈</div>
        <div class="siyuan-bridge-home__feedback">
          <label class="siyuan-bridge-home__field">
            <span class="siyuan-bridge-home__label">类型</span>
            <select class="b3-select" data-feedback-field="type">
              <option value="bug">Bug 报告</option>
              <option value="feature">功能建议</option>
              <option value="idea">想法</option>
            </select>
          </label>
          <label class="siyuan-bridge-home__field">
            <span class="siyuan-bridge-home__label">标题</span>
            <input class="b3-text-field fn__block" data-feedback-field="title"
                   placeholder="简要描述你的反馈" />
          </label>
          <label class="siyuan-bridge-home__field">
            <span class="siyuan-bridge-home__label">描述</span>
            <textarea class="b3-text-field fn__block" data-feedback-field="description"
                      rows="4" placeholder="详细描述..."></textarea>
          </label>
          <label class="siyuan-bridge-home__field">
            <span class="siyuan-bridge-home__label">联系方式（可选）</span>
            <input class="b3-text-field fn__block" data-feedback-field="contact"
                   placeholder="邮箱或社交媒体，方便我们回复你" />
          </label>
          <div class="siyuan-bridge-home__actions">
            <button class="b3-button" data-action="submit-feedback">提交反馈</button>
          </div>
        </div>
      </div>

    </div>
  `;
}

function bindHome(root, plugin) {
  loadAndRenderNotifications(root);
  loadTelemetryConfig(root);
  loadAndRenderSystemGuides(root);

  const telemetryCheckbox = root.querySelector("[data-telemetry='checkbox']");
  const localCopyArea = root.querySelector("[data-telemetry='local-copy-area']");
  const localCopyCheckbox = root.querySelector("[data-telemetry='local-copy']");

  const syncLocalCopyVisibility = () => {
    if (localCopyArea) {
      localCopyArea.style.display = telemetryCheckbox?.checked ? "" : "none";
    }
  };

  if (telemetryCheckbox) {
    telemetryCheckbox.addEventListener("change", async () => {
      const mode = telemetryCheckbox.checked ? "upload" : "off";
      const localCopy = telemetryCheckbox.checked && localCopyCheckbox?.checked;
      const ok = await saveTelemetryConfig(mode, localCopy);
      if (!ok) {
        telemetryCheckbox.checked = !telemetryCheckbox.checked;
      }
      syncLocalCopyVisibility();
    });
  }

  if (localCopyCheckbox) {
    localCopyCheckbox.addEventListener("change", async () => {
      if (!telemetryCheckbox?.checked) return;
      await saveTelemetryConfig("upload", localCopyCheckbox.checked);
    });
  }

  syncLocalCopyVisibility();

  root.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const action = target.getAttribute("data-action");
    if (!action) return;

    if (action === "open-mcp-settings") {
      await plugin.openMcpSettings();
    }
    if (action === "submit-feedback") {
      await handleSubmitFeedback(root);
    }
    if (action === "reset-system-guide") {
      const guideKey = target.getAttribute("data-guide-key") || "";
      await resetSystemGuide(root, guideKey);
    }
  });
}

async function loadAndRenderSystemGuides(root) {
  const area = root.querySelector("[data-area='system-guides']");
  if (!area) return;
  try {
    const {workspace} = await getCurrentSystemWorkspace();
    if (!workspace) {
      area.innerHTML = `<p class="siyuan-bridge-home__hint">系统笔记本尚未初始化，请重新启用插件后重试。</p>`;
      return;
    }
    const documents = workspace.documents || {};
    const rows = [
      ["mcp_usage_guide", "MCP 使用指南"],
      ["workspace_index_guide", "工作空间索引创建指南"],
    ].map(([key, label]) => {
      const entries = registryEntries(documents[key]);
      const modified = entries.filter((entry) => entry.user_modified).length;
      const versions = entries.map((entry) => Number(entry.template_version || 1));
      const status = entries.length === 0
        ? "尚未初始化"
        : modified > 0
          ? `${entries.length} 篇，其中 ${modified} 篇用户已修改`
          : `${entries.length} 篇，系统默认版本 v${Math.max(...versions)}`;
      return `
        <div class="siyuan-bridge-home__guide-row">
          <div>
            <div class="siyuan-bridge-home__guide-name">${label}</div>
            <div class="siyuan-bridge-home__hint">${escapeHtml(status)}</div>
          </div>
          <button class="b3-button b3-button--outline"
                  data-action="reset-system-guide" data-guide-key="${key}"
                  ${entries.length > 0 ? "" : "disabled"}>重置</button>
        </div>`;
    });
    area.innerHTML = rows.join("");
  } catch (_error) {
    area.innerHTML = `<p class="siyuan-bridge-home__hint">无法读取系统指南状态，请重新启用插件后重试。</p>`;
  }
}

async function resetSystemGuide(root, guideKey) {
  const labels = {
    mcp_usage_guide: "MCP 使用指南",
    workspace_index_guide: "工作空间索引创建指南",
  };
  const label = labels[guideKey];
  if (!label) return;
  try {
    const {state, workspace} = await getCurrentSystemWorkspace();
    const entries = registryEntries(workspace?.documents?.[guideKey]);
    if (entries.length === 0) {
      throw new Error("尚未找到系统文档 ID，请重新启用插件后重试");
    }
    if (!window.confirm(
      `确定要把《${label}》的 ${entries.length} 篇已登记文档全部重置为当前默认内容吗？文档 ID 会保留。`
    )) return;
    const bridgeConfig = await readBridgeConfig();
    const language = bridgeConfig.config?.language === "en" ? "en" : "zh-CN";
    const manifest = JSON.parse(await getFile(`${SYSTEM_TEMPLATE_ROOT}/manifest.json`));
    const templateInfo = manifest?.templates?.[guideKey];
    const filename = templateInfo?.files?.[language] || templateInfo?.files?.["zh-CN"];
    if (!filename) throw new Error("内置模板缺失");
    const markdown = await getFile(`${SYSTEM_TEMPLATE_ROOT}/${filename}`);

    for (const entry of entries) {
      await callSiyuanApi("/api/block/updateBlock", {
        id: entry.id,
        dataType: "markdown",
        data: markdown,
      });
      const actualMarkdown = await exportSystemDocument(entry.id);
      entry.template_version = Number(templateInfo.version || 1);
      entry.source_sha256 = String(templateInfo?.source_sha256?.[language] || "");
      entry.rendered_sha256 = await sha256Text(normalizeManagedMarkdown(actualMarkdown));
      entry.current_sha256 = entry.rendered_sha256;
      entry.user_modified = false;
    }
    workspace.documents[guideKey] = entries;
    await putFile(SYSTEM_STATE_PATH, JSON.stringify(state, null, 2) + "\n");
    await loadAndRenderSystemGuides(root);
    showMessage(`《${label}》的 ${entries.length} 篇文档已重置，原文档 ID 保持不变`);
  } catch (error) {
    console.error("Failed to reset system guide:", error);
    showMessage(`重置失败：${error?.message || error}`, -1, "error");
  }
}

async function getCurrentSystemWorkspace() {
  const state = JSON.parse(await getFile(SYSTEM_STATE_PATH));
  const data = await callSiyuanApi("/api/notebook/lsNotebooks", {});
  const notebooks = Array.isArray(data?.notebooks) ? data.notebooks : Array.isArray(data) ? data : [];
  const currentNames = new Set(["思源桥", "SiYuan Bridge"].map((name) => name.toLowerCase()));
  const legacyNames = new Set(["思源代理桥", "SiYuan Agent Bridge"].map((name) => name.toLowerCase()));
  const current = notebooks.find((notebook) => currentNames.has(String(notebook?.name || "").toLowerCase()));
  const active = notebooks.find((notebook) => String(notebook?.id || "") === String(state.active_workspace_key || ""));
  const legacy = notebooks.find((notebook) => legacyNames.has(String(notebook?.name || "").toLowerCase()));
  const notebook = current || active || legacy;
  const key = String(notebook?.id || "");
  const workspace = state?.workspaces?.[key] || null;
  return {state, workspace};
}

function normalizeManagedMarkdown(markdown) {
  const lines = String(markdown || "")
    .replace(/^\uFEFF/, "")
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .split("\n")
    .map((line) => line.replace(/\s+$/, ""));
  return lines.join("\n").trim().replace(/\n{3,}/g, "\n\n");
}

async function sha256Text(text) {
  const bytes = new TextEncoder().encode(String(text));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

// ---------------------------------------------------------------------------
// System notebook lifecycle
// ---------------------------------------------------------------------------

async function ensureSystemNotebook() {
  const bridgeConfig = await readBridgeConfig();
  const language = bridgeConfig.config?.language === "en" ? "en" : "zh-CN";
  const state = await loadSystemState();
  const notebooksData = await callSiyuanApi("/api/notebook/lsNotebooks", {});
  const notebooks = Array.isArray(notebooksData?.notebooks)
    ? notebooksData.notebooks
    : Array.isArray(notebooksData)
      ? notebooksData
      : [];
  const cachedKey = String(state.active_workspace_key || "");
  const cachedNotebookId = String(
    state?.workspaces?.[cachedKey]?.system_notebook?.id || ""
  );
  const currentNames = new Set(
    Object.values(SYSTEM_NOTEBOOK_NAMES).map((name) => name.toLowerCase())
  );
  const currentNotebook = notebooks.find((notebook) =>
    currentNames.has(String(notebook?.name || "").toLowerCase())
  );
  const cachedNotebook = notebooks.find((notebook) =>
    String(notebook?.id || "") === cachedNotebookId
  );
  const legacyNotebook = notebooks.find((notebook) =>
    LEGACY_SYSTEM_NOTEBOOK_NAMES.some((name) =>
      name.toLowerCase() === String(notebook?.name || "").toLowerCase()
    )
  );

  let notebook = cachedNotebook || currentNotebook || legacyNotebook;
  if (!notebook) {
    const created = await callSiyuanApi("/api/notebook/createNotebook", {
      name: SYSTEM_NOTEBOOK_NAMES[language],
    });
    notebook = created?.notebook || created;
  }
  const notebookId = String(notebook?.id || "");
  if (!notebookId) {
    throw new Error("无法创建思源桥系统笔记本");
  }

  const wasClosed = notebook?.closed === true;
  if (wasClosed) {
    await callSiyuanApi("/api/notebook/openNotebook", {notebook: notebookId});
  }
  try {
    const safeNotebookId = notebookId.replaceAll("'", "''");
    const docs = await callSiyuanApi("/api/query/sql", {
      stmt: "SELECT id, box, hpath, updated FROM blocks "
        + `WHERE type='d' AND box='${safeNotebookId}'`,
    });
    const liveDocs = Array.isArray(docs) ? docs : [];
    const workspace = ensureSystemWorkspaceState(
      state,
      notebookId,
      String(notebook?.name || SYSTEM_NOTEBOOK_NAMES[language])
    );
    const documentCache = workspace.documents;

    await ensureAiPreferences(liveDocs, notebookId, language, documentCache);
    await ensureAboutDocument(liveDocs, notebookId, language, documentCache);
    await ensureSimpleSystemDocument(
      liveDocs, notebookId, language, documentCache, "privacy_rules"
    );
    const manifest = JSON.parse(
      await getFile(`${SYSTEM_TEMPLATE_ROOT}/manifest.json`)
    );
    await ensureManagedGuide(
      liveDocs, notebookId, language, documentCache, manifest, "mcp_usage_guide"
    );
    await ensureManagedGuide(
      liveDocs, notebookId, language, documentCache, manifest, "workspace_index_guide"
    );
    await ensureWorkspaceIndex(liveDocs, notebookId, language, documentCache);

    workspace.refreshed_at = new Date().toISOString();
    state.active_workspace_key = notebookId;
    await putFile(SYSTEM_STATE_PATH, JSON.stringify(state, null, 2) + "\n");
    return documentCache;
  } finally {
    if (wasClosed) {
      await callSiyuanApi("/api/notebook/closeNotebook", {notebook: notebookId});
    }
  }
}

async function loadSystemState() {
  try {
    const parsed = JSON.parse(await getFile(SYSTEM_STATE_PATH));
    if (parsed && typeof parsed === "object") {
      return {
        schema_version: SYSTEM_STATE_SCHEMA_VERSION,
        active_workspace_key: String(parsed.active_workspace_key || ""),
        workspaces: parsed.workspaces && typeof parsed.workspaces === "object"
          ? parsed.workspaces
          : {},
      };
    }
  } catch (_error) {
    // Missing state is normal for existing users upgrading to this version.
  }
  return {schema_version: SYSTEM_STATE_SCHEMA_VERSION, active_workspace_key: "", workspaces: {}};
}

function ensureSystemWorkspaceState(state, notebookId, notebookName) {
  if (!state.workspaces || typeof state.workspaces !== "object") {
    state.workspaces = {};
  }
  const workspace = state.workspaces[notebookId]
    && typeof state.workspaces[notebookId] === "object"
    ? state.workspaces[notebookId]
    : {};
  workspace.system_notebook = {id: notebookId, name: notebookName};
  if (!workspace.documents || typeof workspace.documents !== "object") {
    workspace.documents = {};
  }
  state.workspaces[notebookId] = workspace;
  return workspace;
}

function systemDocTitle(doc) {
  const hpath = String(doc?.hpath || "").replace(/^\/+|\/+$/g, "");
  const parts = hpath.split("/");
  return parts[parts.length - 1] || "";
}

function registryEntries(value) {
  if (Array.isArray(value)) {
    return value.filter((entry) => entry && typeof entry === "object" && entry.id);
  }
  return value && typeof value === "object" && value.id ? [value] : [];
}

function findSystemDocs(docs, key, documentCache) {
  const entries = registryEntries(documentCache[key]);
  const names = new Set([
    ...Object.values(SYSTEM_DOC_NAMES[key] || {}),
    ...(LEGACY_SYSTEM_DOC_NAMES[key] || []),
  ].map((name) => String(name).toLowerCase()));
  const result = [];
  const seen = new Set();
  const add = (doc) => {
    const id = String(doc?.id || "");
    if (id && !seen.has(id)) {
      seen.add(id);
      result.push(doc);
    }
  };
  entries.forEach((entry) => add(docs.find((doc) => String(doc?.id || "") === String(entry.id))));
  docs.forEach((doc) => {
    if (names.has(systemDocTitle(doc).toLowerCase())) add(doc);
  });
  return result;
}

async function createSystemDocument(docs, notebookId, title, markdown) {
  const created = await callSiyuanApi("/api/filetree/createDocWithMd", {
    notebook: notebookId,
    path: `/${title}`,
    markdown,
  });
  const docId = typeof created === "string"
    ? created
    : String(created?.id || "");
  if (!docId) throw new Error(`无法创建系统文档：${title}`);
  const doc = {id: docId, box: notebookId, hpath: `/${title}`, updated: ""};
  docs.push(doc);
  return doc;
}

async function exportSystemDocument(docId) {
  const exported = await callSiyuanApi("/api/export/exportMdContent", {
    id: docId,
    refMode: 0,
    embedMode: 0,
  });
  return String(
    exported?.content || exported?.markdown || exported?.md || exported?.kramdown || ""
  );
}

async function updateSystemDocument(docId, markdown) {
  await callSiyuanApi("/api/block/updateBlock", {
    id: docId,
    dataType: "markdown",
    data: markdown,
  });
  return exportSystemDocument(docId);
}

async function loadBootstrapTemplate(key, language) {
  const files = SYSTEM_BOOTSTRAP_FILES[key];
  const filename = files?.[language] || files?.["zh-CN"];
  if (!filename) throw new Error(`系统文档模板不存在：${key}`);
  return getFile(`${SYSTEM_TEMPLATE_ROOT}/${filename}`);
}

function systemDocumentRecord(doc, extra = {}) {
  return {
    id: String(doc?.id || ""),
    name: systemDocTitle(doc),
    ...extra,
  };
}

function cachedRecordsById(documentCache, key) {
  return new Map(registryEntries(documentCache[key]).map((entry) => [String(entry.id), entry]));
}

function recordSystemDocuments(documentCache, key, records) {
  documentCache[key] = records.filter((entry) => entry?.id);
}

async function ensureAiPreferences(docs, notebookId, language, documentCache) {
  const key = "ai_guide";
  const template = await loadBootstrapTemplate(key, language);
  let matches = findSystemDocs(docs, key, documentCache);
  if (matches.length === 0) {
    matches = [await createSystemDocument(
      docs, notebookId, SYSTEM_DOC_NAMES[key][language], template
    )];
  }
  const legacyNames = new Set((LEGACY_SYSTEM_DOC_NAMES[key] || []).map((name) => name.toLowerCase()));
  const records = [];
  for (const doc of matches) {
    if (legacyNames.has(systemDocTitle(doc).toLowerCase())) {
      await callSiyuanApi("/api/filetree/renameDocByID", {
        id: doc.id,
        title: SYSTEM_DOC_NAMES[key][language],
      });
      doc.hpath = `/${SYSTEM_DOC_NAMES[key][language]}`;
    }
    let markdown = await exportSystemDocument(doc.id);
    const currentHash = await sha256Text(normalizeManagedMarkdown(markdown));
    if (LEGACY_AI_GUIDE_HASHES.has(currentHash)) {
      markdown = await updateSystemDocument(doc.id, template);
    }
    records.push(systemDocumentRecord(doc));
  }
  recordSystemDocuments(documentCache, key, records);
}

async function ensureAboutDocument(docs, notebookId, language, documentCache) {
  const key = "about";
  const template = await loadBootstrapTemplate(key, language);
  const sourceHash = await sha256Text(template);
  const templateHash = await sha256Text(normalizeManagedMarkdown(template));
  const cached = cachedRecordsById(documentCache, key);
  let matches = findSystemDocs(docs, key, documentCache);
  if (matches.length === 0) {
    matches = [await createSystemDocument(
      docs, notebookId, SYSTEM_DOC_NAMES[key][language], template
    )];
  }
  const records = [];
  for (const doc of matches) {
    if (systemDocTitle(doc) !== SYSTEM_DOC_NAMES[key][language]) {
      await callSiyuanApi("/api/filetree/renameDocByID", {
        id: doc.id,
        title: SYSTEM_DOC_NAMES[key][language],
      });
      doc.hpath = `/${SYSTEM_DOC_NAMES[key][language]}`;
    }
    let markdown = await exportSystemDocument(doc.id);
    const currentHash = await sha256Text(normalizeManagedMarkdown(markdown));
    const entry = cached.get(String(doc.id)) || {};
    const baselineMatches = String(entry.rendered_sha256 || "") === currentHash
      && String(entry.source_sha256 || "") === sourceHash;
    if (!baselineMatches && currentHash !== templateHash) {
      markdown = await updateSystemDocument(doc.id, template);
    }
    records.push(systemDocumentRecord(doc, {
      source_sha256: sourceHash,
      rendered_sha256: await sha256Text(normalizeManagedMarkdown(markdown)),
      developer_controlled: true,
    }));
  }
  recordSystemDocuments(documentCache, key, records);
}

async function ensureSimpleSystemDocument(
  docs, notebookId, language, documentCache, key
) {
  let matches = findSystemDocs(docs, key, documentCache);
  if (matches.length === 0) {
    const template = await loadBootstrapTemplate(key, language);
    matches = [await createSystemDocument(
      docs, notebookId, SYSTEM_DOC_NAMES[key][language], template
    )];
  }
  recordSystemDocuments(
    documentCache, key, matches.map((doc) => systemDocumentRecord(doc))
  );
}

async function ensureManagedGuide(
  docs, notebookId, language, documentCache, manifest, key
) {
  const templateInfo = manifest?.templates?.[key];
  const filename = templateInfo?.files?.[language]
    || templateInfo?.files?.["zh-CN"];
  if (!filename) throw new Error(`内置指南模板缺失：${key}`);
  const template = await getFile(`${SYSTEM_TEMPLATE_ROOT}/${filename}`);
  const sourceHash = await sha256Text(template);
  const expectedSourceHash = String(
    templateInfo?.source_sha256?.[language]
      || templateInfo?.source_sha256?.["zh-CN"]
      || ""
  );
  if (expectedSourceHash && expectedSourceHash !== sourceHash) {
    throw new Error(`内置指南模板哈希不匹配：${filename}`);
  }

  const cached = cachedRecordsById(documentCache, key);
  let matches = findSystemDocs(docs, key, documentCache);
  if (matches.length === 0) {
    matches = [await createSystemDocument(
      docs, notebookId, SYSTEM_DOC_NAMES[key][language], template
    )];
  }
  const records = [];
  for (const doc of matches) {
    const entry = cached.get(String(doc.id)) || {};
    let markdown = await exportSystemDocument(doc.id);
    const currentHash = await sha256Text(normalizeManagedMarkdown(markdown));
    if (entry.user_modified === true) {
      records.push(await managedGuideRecord(
        doc, templateInfo, sourceHash, markdown, true, String(entry.rendered_sha256 || "")
      ));
      continue;
    }
    const baselineHash = String(entry.rendered_sha256 || "");
    if (baselineHash && currentHash !== baselineHash) {
      records.push(await managedGuideRecord(
        doc, templateInfo, sourceHash, markdown, true, baselineHash
      ));
      continue;
    }
    const templateVersion = Number(templateInfo.version || 1);
    if (baselineHash) {
      const templateChanged = Number(entry.template_version || 0) !== templateVersion
        || String(entry.source_sha256 || "") !== sourceHash;
      if (templateChanged) markdown = await updateSystemDocument(doc.id, template);
      records.push(await managedGuideRecord(doc, templateInfo, sourceHash, markdown));
      continue;
    }
    const templateHash = await sha256Text(normalizeManagedMarkdown(template));
    const knownHashes = new Set([
      templateHash,
      ...(templateInfo?.historical_normalized_sha256?.[language] || []),
    ]);
    if (knownHashes.has(currentHash)) {
      if (currentHash !== templateHash) markdown = await updateSystemDocument(doc.id, template);
      records.push(await managedGuideRecord(doc, templateInfo, sourceHash, markdown));
    } else {
      records.push(await managedGuideRecord(doc, templateInfo, sourceHash, markdown, true, ""));
    }
  }
  recordSystemDocuments(documentCache, key, records);
}

async function managedGuideRecord(
  doc,
  templateInfo,
  sourceHash,
  markdown,
  userModified = false,
  baselineHash = null
) {
  const currentHash = await sha256Text(normalizeManagedMarkdown(markdown));
  return systemDocumentRecord(doc, {
    template_version: Number(templateInfo.version || 1),
    source_sha256: sourceHash,
    rendered_sha256: baselineHash === null ? currentHash : baselineHash,
    current_sha256: currentHash,
    user_modified: userModified,
  });
}

async function ensureWorkspaceIndex(docs, notebookId, language, documentCache) {
  const key = "workspace_index";
  const placeholder = await loadBootstrapTemplate(key, language);
  let matches = findSystemDocs(docs, key, documentCache);
  if (matches.length === 0) {
    matches = [await createSystemDocument(
      docs, notebookId, SYSTEM_DOC_NAMES[key][language], placeholder
    )];
  }
  const placeholderHash = await sha256Text(normalizeManagedMarkdown(placeholder));
  const records = [];
  for (const doc of matches) {
    const markdown = await exportSystemDocument(doc.id);
    const rows = await callSiyuanApi("/api/query/sql", {
      stmt: `SELECT updated FROM blocks WHERE id='${String(doc.id).replaceAll("'", "''")}' LIMIT 1`,
    });
    records.push(systemDocumentRecord(doc, {
      placeholder: await sha256Text(normalizeManagedMarkdown(markdown)) === placeholderHash,
      updated: Array.isArray(rows) ? String(rows[0]?.updated || "") : "",
    }));
  }
  recordSystemDocuments(documentCache, key, records);
}

function showDuplicateSystemDocuments(documentCache) {
  const labels = {
    ai_guide: "用户个性化要求",
    mcp_usage_guide: "MCP 使用指南",
    workspace_index_guide: "工作空间索引创建指南",
    workspace_index: "工作空间索引",
    about: "关于思源桥",
    privacy_rules: "隐私规则",
  };
  const duplicates = Object.entries(labels)
    .map(([key, label]) => ({label, count: registryEntries(documentCache[key]).length}))
    .filter((item) => item.count > 1);
  if (duplicates.length === 0) return;
  const items = duplicates
    .map((item) => `<li>${escapeHtml(item.label)}：${item.count} 篇</li>`)
    .join("");
  new Dialog({
    title: "发现重复的思源桥系统文档",
    content: `<div class="b3-dialog__content"><p>以下系统文档存在多篇：</p><ul>${items}</ul><p>请检查内容后手动删除多余文档。删除后可以继续使用；禁用并重新启用思源桥插件可立即清理内部记录，否则下次插件激活时会自动清理。</p></div>`,
    width: "520px",
  });
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

async function loadAndRenderNotifications(root) {
  const area = root.querySelector("[data-area='notifications']");
  if (!area) return;

  try {
    const endpoint = await getEffectiveEndpoint();
    const response = await fetch(`${endpoint}/api/notifications`, {
      method: "GET",
      headers: {"Content-Type": "application/json"},
    });
    const data = await response.json();
    const notifications = data?.notifications || [];

    if (notifications.length === 0) {
      area.innerHTML = `<div class="siyuan-bridge-home__empty">暂无新消息</div>`;
      return;
    }

    area.innerHTML = notifications.map((n) =>
      n.url
        ? `<a class="siyuan-bridge-home__notification-item"
             href="${escapeAttr(n.url)}" target="_blank" rel="noopener">
             ${escapeHtml(n.title || "")}
           </a>`
        : `<div class="siyuan-bridge-home__notification-item siyuan-bridge-home__notification-text">
             ${escapeHtml(n.title || "")}
           </div>`
    ).join("");
  } catch (_error) {
    area.innerHTML = `<div class="siyuan-bridge-home__empty">暂无新消息</div>`;
  }
}

// ---------------------------------------------------------------------------
// Telemetry config I/O
// ---------------------------------------------------------------------------

async function getEffectiveEndpoint() {
  try {
    const text = await getFile(TELEMETRY_PATH);
    const cfg = JSON.parse(text);
    if (cfg && typeof cfg === "object" && cfg.telemetry_endpoint) {
      return String(cfg.telemetry_endpoint).trim();
    }
  } catch (_error) {
    // Missing or corrupt — use default
  }
  return DEFAULT_ENDPOINT;
}

async function loadTelemetryConfig(root) {
  const telemetryCheckbox = root.querySelector("[data-telemetry='checkbox']");
  const localCopyCheckbox = root.querySelector("[data-telemetry='local-copy']");

  try {
    const text = await getFile(TELEMETRY_PATH);
    const cfg = JSON.parse(text);
    if (telemetryCheckbox) {
      telemetryCheckbox.checked = cfg && cfg.telemetry === "upload";
    }
    if (localCopyCheckbox) {
      localCopyCheckbox.checked = cfg && cfg.local_copy === true;
    }
  } catch (_error) {
    if (telemetryCheckbox) telemetryCheckbox.checked = false;
    if (localCopyCheckbox) localCopyCheckbox.checked = false;
  }
}

async function ensureTelemetryConfig() {
  try {
    const text = await getFile(TELEMETRY_PATH);
    const cfg = JSON.parse(text);
    if (cfg && typeof cfg === "object" && cfg.anonymous_id) {
      return; // already initialized
    }
  } catch (_error) {
    // Missing or corrupt — initialize
  }

  let existing = {};
  try {
    const text = await getFile(TELEMETRY_PATH);
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object") {
      existing = parsed;
    }
  } catch (_error) { /* start fresh */ }

  if (!existing.anonymous_id) {
    existing.anonymous_id = crypto.randomUUID().replace(/-/g, "");
  }

  try {
    await putFile(TELEMETRY_PATH, JSON.stringify(existing, null, 2) + "\n");
  } catch (_error) {
    console.error("Failed to save telemetry config:", _error);
  }
}

async function saveTelemetryConfig(mode, localCopy) {
  let existing = {};
  try {
    const text = await getFile(TELEMETRY_PATH);
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object") {
      existing = parsed;
    }
  } catch (_error) {
    // Missing file — start fresh
  }

  existing.telemetry = mode;
  if (typeof localCopy === "boolean" && mode === "upload") {
    existing.local_copy = localCopy;
  }

  try {
    await putFile(TELEMETRY_PATH, JSON.stringify(existing, null, 2) + "\n");
    return true;
  } catch (_error) {
    console.error("Failed to save telemetry config:", _error);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Feedback submission
// ---------------------------------------------------------------------------

async function handleSubmitFeedback(root) {
  const typeEl = root.querySelector("[data-feedback-field='type']");
  const titleEl = root.querySelector("[data-feedback-field='title']");
  const descEl = root.querySelector("[data-feedback-field='description']");
  const contactEl = root.querySelector("[data-feedback-field='contact']");

  const type = typeEl?.value?.trim();
  const title = titleEl?.value?.trim();
  const description = descEl?.value?.trim();

  if (!title) {
    showMessage("请输入反馈标题", -1, "error");
    return;
  }
  if (!description) {
    showMessage("请输入反馈描述", -1, "error");
    return;
  }

  const payload = {type, title, description};
  const contact = contactEl?.value?.trim();
  if (contact) {
    payload.contact = contact;
  }

  try {
    const endpoint = await getEffectiveEndpoint();
    const response = await fetch(`${endpoint}/api/feedback`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (response.ok) {
      showMessage("反馈已提交，感谢你的反馈！");
      if (titleEl) titleEl.value = "";
      if (descEl) descEl.value = "";
      if (contactEl) contactEl.value = "";
    } else {
      showMessage("反馈提交失败，请稍后重试", -1, "error");
    }
  } catch (_error) {
    console.error("Feedback submission failed:", _error);
    showMessage("反馈提交失败，无法连接到服务器，请稍后重试", -1, "error");
  }
}

// ---------------------------------------------------------------------------
// Context helpers
// ---------------------------------------------------------------------------

async function getPluginContext() {
  const systemConf = await getSystemConf();
  const activeWorkspaceDir = await getActiveWorkspaceDir();
  const workspaceDir = activeWorkspaceDir || systemConf.workspaceDir || "";
  const guessedPluginDir = workspaceDir ? joinPath(workspaceDir, "data", "plugins", PLUGIN_NAME) : "";
  const guessedBridgeDir = guessedPluginDir ? joinPath(guessedPluginDir, "bridge") : "";
  const guessedRunMcp = guessedBridgeDir
    ? joinPath(guessedBridgeDir, "scripts", "run_mcp.py")
    : "";
  return {
    currentWorkspaceName: workspaceDir ? workspaceDir.split(/[\\/]/).filter(Boolean).pop() || "当前工作空间" : "当前工作空间",
    currentToken: systemConf.token || "",
    workspaceDir,
    pluginDir: guessedPluginDir,
    bridgeDir: guessedBridgeDir,
    runMcpPath: guessedRunMcp,
    pythonCommand: "python",
    serverName: "siyuan-bridge",
  };
}

async function loadBridgeConfig(context) {
  const existing = await readBridgeConfig();
  const config = existing.config || JSON.parse(JSON.stringify(DEFAULT_CONFIG));
  return applyCurrentWorkspaceDefaults(config, context);
}

async function readBridgeConfig() {
  try {
    const text = await getFile(CONFIG_PATH);
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && Array.isArray(parsed.profiles)) {
      return {config: normalizeConfig(parsed), exists: true};
    }
  } catch (_error) {
    // Missing config is normal for first-run setup.
  }
  return {config: null, exists: false};
}

function normalizeConfig(config) {
  const profiles = Array.isArray(config.profiles) && config.profiles.length
    ? config.profiles
    : DEFAULT_CONFIG.profiles;
  return {
    profiles: profiles.map((profile, index) => ({
      name: String(profile?.name || (index === 0 ? "当前工作空间" : `工作空间 ${index + 1}`)),
      token: String(profile?.token || ""),
    })),
    language: String(config.language || "zh-CN"),
  };
}

function applyCurrentWorkspaceDefaults(config, context) {
  const normalized = normalizeConfig(config);
  if (!normalized.profiles.length) {
    normalized.profiles.push({name: context.currentWorkspaceName || "当前工作空间", token: context.currentToken || ""});
  }
  if (!normalized.profiles[0].name || normalized.profiles[0].name === "当前工作空间") {
    normalized.profiles[0].name = context.currentWorkspaceName || "当前工作空间";
  }
  if (!normalized.profiles[0].token && context.currentToken) {
    normalized.profiles[0].token = context.currentToken;
  }
  if (
    context.currentToken
    && !normalized.profiles.some((profile) => profile.token === context.currentToken)
  ) {
    normalized.profiles.push({
      name: context.currentWorkspaceName || `工作空间 ${normalized.profiles.length + 1}`,
      token: context.currentToken,
    });
  }
  return normalized;
}

async function ensureDefaultBridgeConfig() {
  const context = await getPluginContext();
  if (!context.currentToken) {
    return;
  }
  const existing = await readBridgeConfig();
  const previous = normalizeConfig(existing.config || DEFAULT_CONFIG);
  const config = applyCurrentWorkspaceDefaults(existing.config || DEFAULT_CONFIG, context);
  if (!existing.exists || JSON.stringify(config) !== JSON.stringify(previous)) {
    await saveBridgeConfig(config);
  }
}

// ---------------------------------------------------------------------------
// MCP Settings Dialog (unchanged)
// ---------------------------------------------------------------------------

function renderSettings(config, context) {
  const escapedConfig = escapeAttr(JSON.stringify(config));
  return `
    <div class="siyuan-bridge" data-config="${escapedConfig}">
      <div class="siyuan-bridge__section">
        <label class="siyuan-bridge__field">
          <span class="siyuan-bridge__label">Python 命令</span>
          <input class="b3-text-field fn__block" data-field="pythonCommand" value="${escapeAttr(context.pythonCommand)}" placeholder="python" />
        </label>
        <label class="siyuan-bridge__field">
          <span class="siyuan-bridge__label">MCP Server 名称</span>
          <input class="b3-text-field fn__block" data-field="serverName" value="${escapeAttr(context.serverName)}" placeholder="siyuan-bridge" />
        </label>
      </div>

      <div class="siyuan-bridge__section">
        <label class="siyuan-bridge__field">
          <span class="siyuan-bridge__label">插件目录</span>
          <input class="b3-text-field fn__block" data-field="pluginDir" value="${escapeAttr(context.pluginDir)}" />
        </label>
        <label class="siyuan-bridge__field">
          <span class="siyuan-bridge__label">Bridge 目录</span>
          <input class="b3-text-field fn__block" data-field="bridgeDir" value="${escapeAttr(context.bridgeDir)}" />
        </label>
        <label class="siyuan-bridge__field">
          <span class="siyuan-bridge__label">MCP 启动脚本</span>
          <input class="b3-text-field fn__block" data-field="runMcpPath" value="${escapeAttr(context.runMcpPath)}" />
        </label>
      </div>

      <div class="siyuan-bridge__section">
        <div class="siyuan-bridge__header">
          <span>工作空间 Profiles</span>
          <button class="b3-button b3-button--outline" data-action="add-profile">添加工作空间</button>
        </div>
        <div data-profiles>${renderProfiles(config.profiles)}</div>
      </div>

      <div class="siyuan-bridge__section">
        <div class="siyuan-bridge__header">
          <span>MCP JSON</span>
          <div class="siyuan-bridge__header-actions">
            <button class="b3-button b3-button--outline" data-action="copy-json">复制 JSON</button>
            <button class="b3-button" data-action="copy-for-ai">复制给 AI</button>
          </div>
        </div>
        <textarea class="b3-text-field siyuan-bridge__json" data-field="mcpJson" readonly></textarea>
      </div>

      <div class="siyuan-bridge__actions">
        <button class="b3-button" data-action="save">保存配置</button>
        <button class="b3-button b3-button--outline" data-action="refresh-json">刷新 JSON</button>
      </div>
    </div>
  `;
}

function renderProfiles(profiles) {
  return profiles.map((profile, index) => `
    <div class="siyuan-bridge__profile" data-profile-index="${index}">
      <input class="b3-text-field" data-profile-field="name" value="${escapeAttr(profile.name)}" placeholder="${index === 0 ? "当前工作空间" : "工作空间名称"}" />
      <input class="b3-text-field" data-profile-field="token" value="${escapeAttr(profile.token)}" placeholder="API Token" type="text" />
      <button class="b3-button b3-button--outline" data-action="remove-profile" ${index === 0 ? "disabled" : ""}>删除</button>
    </div>
  `).join("");
}

function bindSettings(root, config, context) {
  const container = root.querySelector(".siyuan-bridge");
  const state = {
    config: normalizeConfig(config),
    context: {...context},
  };

  const refreshProfiles = () => {
    container.querySelector("[data-profiles]").innerHTML = renderProfiles(state.config.profiles);
    refreshJson();
  };

  const refreshJson = () => {
    readContext(container, state);
    const textarea = container.querySelector("[data-field='mcpJson']");
    textarea.value = JSON.stringify(buildMcpConfig(state.context), null, 2);
  };

  container.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    const profileEl = target.closest("[data-profile-index]");
    if (profileEl) {
      const index = Number(profileEl.getAttribute("data-profile-index"));
      const field = target.getAttribute("data-profile-field");
      if (field === "name" || field === "token") {
        state.config.profiles[index][field] = target.value;
      }
    }
    refreshJson();
  });

  container.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const action = target.getAttribute("data-action");
    if (!action) {
      return;
    }
    if (action === "add-profile") {
      state.config.profiles.push({name: `工作空间 ${state.config.profiles.length + 1}`, token: ""});
      refreshProfiles();
    }
    if (action === "remove-profile") {
      const profileEl = target.closest("[data-profile-index]");
      const index = Number(profileEl?.getAttribute("data-profile-index"));
      if (index > 0) {
        state.config.profiles.splice(index, 1);
        refreshProfiles();
      }
    }
    if (action === "refresh-json") {
      await refreshDetectedPaths(container, state);
      refreshJson();
      showMessage("已按当前电脑刷新 MCP 路径和 Token");
    }
    if (action === "copy-json") {
      refreshJson();
      await navigator.clipboard.writeText(container.querySelector("[data-field='mcpJson']").value);
      showMessage("MCP JSON 已复制");
    }
    if (action === "copy-for-ai") {
      refreshJson();
      const mcpJson = container.querySelector("[data-field='mcpJson']").value;
      const prompt = "注册这个MCP工具，注意这是Claude code的语法，注册时需要使用本平台正确的MCP注册语法";
      await navigator.clipboard.writeText(`${prompt}\n\n${mcpJson}`);
      showMessage("MCP 配置已复制给 AI");
    }
    if (action === "save") {
      readContext(container, state);
      await saveBridgeConfig(state.config);
      refreshJson();
      showMessage("思源桥配置已保存");
    }
  });

  refreshJson();
}

function readContext(container, state) {
  for (const key of ["pythonCommand", "serverName", "pluginDir", "bridgeDir", "runMcpPath"]) {
    const input = container.querySelector(`[data-field='${key}']`);
    if (input instanceof HTMLInputElement) {
      state.context[key] = input.value.trim();
    }
  }
}

async function refreshDetectedPaths(container, state) {
  const detected = await getPluginContext();
  for (const key of ["currentWorkspaceName", "currentToken", "workspaceDir", "pluginDir", "bridgeDir", "runMcpPath"]) {
    state.context[key] = detected[key];
  }
  for (const key of ["pluginDir", "bridgeDir", "runMcpPath"]) {
    const input = container.querySelector(`[data-field='${key}']`);
    if (input instanceof HTMLInputElement) {
      input.value = state.context[key] || "";
    }
  }
  state.config = applyCurrentWorkspaceDefaults(state.config, state.context);
  container.querySelector("[data-profiles]").innerHTML = renderProfiles(state.config.profiles);
  await saveBridgeConfig(state.config);
}

async function saveBridgeConfig(config) {
  const normalized = normalizeConfig(config);
  await putFile(CONFIG_PATH, JSON.stringify(normalized, null, 2) + "\n");
}

function buildMcpConfig(context) {
  return {
    mcpServers: {
      [context.serverName || "siyuan-bridge"]: {
        command: context.pythonCommand || "python",
        args: [context.runMcpPath || ""],
        env: {
          PYTHONUTF8: "1",
        },
      },
    },
  };
}

// ---------------------------------------------------------------------------
// SiYuan API helpers
// ---------------------------------------------------------------------------

async function callSiyuanApi(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {}),
  });
  const envelope = await response.json();
  if (envelope?.code !== 0) {
    throw new Error(envelope?.msg || `思源 API 调用失败：${path}`);
  }
  return envelope.data;
}

async function getFile(path) {
  const response = await fetch("/api/file/getFile", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({path}),
  });
  const text = await response.text();
  let envelope;
  try {
    envelope = JSON.parse(text);
  } catch (_) {
    return text;
  }
  if (envelope && typeof envelope === "object" && envelope.code !== undefined) {
    if (envelope.code !== 0) {
      throw new Error(envelope.msg || "读取配置失败");
    }
    return typeof envelope.data === "string" ? envelope.data : JSON.stringify(envelope.data || {});
  }
  return text;
}

async function getSystemConf() {
  try {
    const response = await fetch("/api/system/getConf", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}",
    });
    const envelope = await response.json();
    const conf = envelope?.data?.conf || {};
    return {
      token: String(conf?.api?.token || ""),
      workspaceDir: String(conf?.system?.workspaceDir || ""),
    };
  } catch (_error) {
    return {token: "", workspaceDir: ""};
  }
}

async function getActiveWorkspaceDir() {
  try {
    const response = await fetch("/api/system/getWorkspaces", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}",
    });
    const envelope = await response.json();
    const workspaces = Array.isArray(envelope?.data) ? envelope.data : [];
    const active = workspaces.find((workspace) => workspace?.closed === false && workspace?.path);
    return String(active?.path || "");
  } catch (_error) {
    return "";
  }
}

async function putFile(path, content) {
  const formData = new FormData();
  formData.append("path", path);
  const filename = String(path).split("/").filter(Boolean).pop() || "data.json";
  formData.append("file", new Blob([content], {type: "application/json"}), filename);
  const response = await fetch("/api/file/putFile", {
    method: "POST",
    body: formData,
  });
  const envelope = await response.json();
  if (envelope.code !== 0) {
    throw new Error(envelope.msg || "写入配置失败");
  }
}

function joinPath(...parts) {
  const separator = navigator.platform.toLowerCase().includes("win") ? "\\" : "/";
  return parts
    .filter(Boolean)
    .join(separator)
    .replace(/[\\/]+/g, separator);
}

function escapeAttr(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

module.exports = SiyuanBridgePlugin;
module.exports.default = SiyuanBridgePlugin;
