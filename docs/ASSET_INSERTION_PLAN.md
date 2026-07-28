# `siyuan_edit(insert_assets)` 附件插入实施计划

> 状态：设计已基本定案，尚未开始实现。本文是 2026-07-28 讨论与实测的完整交接记录；后续开发必须先重新核对思源当前源码/API，再修改项目代码。

## 1. 目标与产品边界

为 SiYuan Bridge 增加“把本地文件或文件夹插入现有思源文档”的能力。

本功能不新增独立 MCP 工具，而是在现有 `siyuan_edit` 中增加：

```text
action="insert_assets"
```

用户可在一次调用中把多个本地项目插入同一位置；需要插入多个位置时分多次调用。一次调用对用户表现为一个完整操作：只有资源处理、文档插入和最终验证全部成功，才报告成功。

不修改 `siyuan_create`。新建含附件文档的推荐调用链固定为：

```text
siyuan_create
→ siyuan_read(include_block_ids=true)
→ siyuan_edit(action="insert_assets")
```

这样不扩大工具面，也不把创建、附件上传、块坐标编辑三套职责混在一个接口中。

## 2. 已确定的设计决策

### 2.1 文件、图片和文件夹属于同一个用户动作

- 图片和普通文件不拆成不同 action。思源的附件上传本身是统一通道，Bridge 不应按扩展名建立两套上传流程。
- 文件和文件夹统一通过 `insert_assets` 接收。
- Bridge 先识别输入路径是普通文件还是目录；后续优先调用思源原生能力。
- 如果思源对文件和文件夹提供统一 API，就走统一 API；如果思源原生 API 本身分开，Bridge 才在底层分开调用，但 MCP 对外仍保持同一个 action 和同一种资产项结构。

### 2.2 文件夹与思源官方 MCP 保持一致

文件夹不复制进工作空间、不递归上传、不自动打 ZIP，只在文档中插入指向原目录的 `file://` 超链接。

文件夹链接的跨设备可用性由用户负责；Bridge 不尝试同步文件夹内容，也不把“跨设备不可用”当作失败。

Bridge 不自行实现 Windows、macOS、Linux 路径转换规则，不自行拼接或编码 `file://` URL。应尽可能把原始本地路径交给思源原生 API，并使用思源返回的最终链接。只有确认思源没有可调用的外部 API 时，才能重新讨论 fallback；不得直接凭经验写跨平台路径处理。

### 2.3 文件由思源处理为附件

普通文件（包括图片）交给思源原生附件接口处理。应复用思源对目标文档资源目录、文件名规范化、哈希去重和返回路径的既有逻辑，不在 Bridge 重复实现这些规则。

图片与其他文件使用同一上传通道；展示 Markdown 可以根据思源返回结果或必要的媒体类型生成图片嵌入或普通附件链接，但不能为此拆分上传事务。

### 2.4 一次调用只支持一个位置

调用级参数使用编辑前引用阅读得到的一组双重锚点：

- `start_index`
- `start_id`

本批次所有资产按 `assets` 数组顺序插在该锚点块之后，不增加 `before/after` 参数。需要插入多个位置时，由 AI 在每次写入后重新引用阅读，再分多次调用；不在一次调用中维护多组会漂移的块坐标。

### 2.5 大文件阈值为 20 MB

- 仅实际要上传的普通文件参与 20 MB 检查。
- 文件夹只插入本地超链接，不统计目录大小、不递归扫描，因此不参与阈值判断。
- 批次内任一普通文件超过 20 MB，整批暂停：不上传任何文件、不插入任何链接、不创建快照。
- 返回 `requires_confirmation: true`，列出超限文件、实际大小和阈值。
- 用户明确要求上传大文件后，使用相同参数并增加 `upload_large_files=true` 重试整个批次。
- `confirmed=true` 表示同意修改思源；`upload_large_files=true` 是是否允许上传超限文件的动作开关，默认 false，不称为第二个确认参数。

### 2.6 图片识别与 Markdown 字段

Bridge 直接复用思源前端的图片扩展名清单，按原始文件名后缀转小写判断：

```text
.apng .ico .cur .jpg .jpe .jpeg .jfif .pjp .pjpeg .png .gif
.webp .bmp .svg .avif .tiff .tif
```

路径先判断是否为目录；目录始终按文件夹链接处理。普通文件后缀在清单中时生成图片 Markdown，其余格式作为普通附件链接，不使用 MIME 猜测或自行扩大格式范围。

每项资产支持两个容易混淆但语义不同的可选字段：

- `name`：正文主要显示名称。图片中是替代文本 `alt`；普通文件和文件夹中是链接锚文本。
- `title`：Markdown 附加标题。图片中会在思源里显示为图片下方标题；普通文件和文件夹中不直接显示在正文，通常用于悬停提示。

工具描述和 schema 必须明确说明这一区别，避免 AI 把图片下方标题误填到 `name`，或把正文锚文本误填到 `title`。未传 `name` 时与思源官方前端一致：图片使用原文件名去掉扩展名，普通文件使用完整文件名，文件夹使用目录名。未传 `title` 时不生成空 title。

同一批次如存在相同基础文件名，整批预检报错并提示分开调用。思源 `succMap` 以基础文件名为键，重名时无法可靠建立输入项与返回路径的对应关系。

## 3. 计划中的 MCP 参数

建议在 `siyuan_edit` 增加：

```json
{
  "action": "insert_assets",
  "document": "/笔记本/父文档/目标文档",
  "start_index": 12,
  "start_id": "20260728120000-abcdefg",
  "assets": [
    {
      "local_path": "D:/materials/chart.png",
      "name": "收入结构",
      "title": "2026 年收入结构图"
    },
    {
      "local_path": "D:/materials/source-files",
      "name": "源文件目录",
      "title": "仅当前电脑可以访问"
    }
  ],
  "upload_large_files": false,
  "confirmed": true
}
```

建议字段契约：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `start_index` | integer | 是 | 写入前引用阅读中的块序号；本批次全部插在该块之后 |
| `start_id` | string | 是 | 与 `start_index` 配对的块 ID |
| `assets` | array | 是 | 本次要插入的本地文件/文件夹；至少一项 |
| `assets[].local_path` | string | 是 | 交给思源处理的本地路径，不由 Bridge 改写为另一操作系统格式 |
| `assets[].name` | string | 否 | 图片 alt 或文件/文件夹锚文本；缺省时按思源官方文件名逻辑生成 |
| `assets[].title` | string | 否 | Markdown 附加标题；图片中显示为下方标题，文件/文件夹通常用于悬停提示 |
| `upload_large_files` | boolean | 否 | 是否允许上传本批次中超过 20 MB 的普通文件，默认 false |
| `confirmed` | boolean | 是 | 是否确认写入思源，必须 true |

当前决定不增加显式 `type=image/file/directory`：路径类型可在预检阶段判断，图片和普通文件又共用思源上传通道。若后续实测发现 MCP 进程看不到调用端本地路径，则该问题属于部署边界，需要重新设计，不能靠调用者声明类型解决。

## 4. 事务与安全流程

目标语义是“整批成功或整批失败”。计划流程：

### 阶段 A：只读预检

1. 校验 `confirmed=true`、action、document/document_id、assets 数组和字段类型。
2. 解析目标可见文档；若按路径定位，校验思源 live hpath 未变化。
3. 检查目标文档最终权限为 `read_write`，并经过 Privacy Rules 过滤。
4. 重新读取目标文档展示块列表。
5. 校验调用级 `start_index/start_id` 双重锚点。
6. 检查每个 `local_path` 是否存在，并识别普通文件或目录；其他类型整批拒绝。
7. 检查同批次基础文件名是否重名；重名则整批拒绝并提示拆分调用。
8. 获取普通文件大小；任一超过 20 MB 且未提供 `upload_large_files=true` 时，整批暂停并返回要求。
9. 使用已经实测确认的 `/api/asset/insertLocalAssets` 契约；思源版本变化时重新验证参数、返回值和回滚能力。

阶段 A 任何失败均不得创建快照、上传资源或插入链接。

### 阶段 B：快照与执行

1. 创建思源工作空间快照；快照失败则停止。
2. 按已确认的思源原生 API 处理所有输入路径：文件进入思源附件通道，目录得到思源生成的 `file://` 链接。
3. 保存每项源路径、思源返回路径、目标锚点和准备插入的 Markdown。
4. 按预先计算的稳定顺序，把所有引用插入指定位置。
5. 不因第一项成功就提前报告；必须继续到整批完成和验证。

### 阶段 C：验证与失败补偿

1. 重新读取目标文档，确认每个资产项均在目标文档中形成对应链接/引用。
2. 普通文件还应确认思源返回的附件路径有效；文件夹只验证链接已插入，不验证目标目录跨设备可访问。
3. 全部通过后才返回 `ok: true`。
4. 任一步失败，应删除本批次已插入的块，并清理本批次新产生且不再被引用的附件，随后验证文档恢复到写入前状态。
5. 如果思源 API 不支持可靠清理，必须如实返回部分失败、残留项和思源快照恢复提示；不得把“已创建快照”假装成程序自动回滚。

注意：当前项目明确不调用高风险自动恢复/checkout 接口。所谓“事务”必须通过可验证的补偿操作实现；快照只是用户手动恢复的最后保险。正式编码前，需要根据思源 API 实测结果确认能否满足严格整批回滚。如果做不到，应先向用户报告并调整契约，不能偷偷降级。

## 5. 思源原生 API 调研结果与待确认点

### 5.1 已确认事实

1. 思源公开附件上传接口为：

```text
POST /api/asset/upload
multipart/form-data:
- assetsDirPath
- file[]
```

返回 `succMap`。该接口能统一处理图片和普通文件，但需要进一步确认它与目标文档资源目录的绑定方式以及失败清理能力。

2. 思源官方 MCP 的 `asset(action="upload")` 内部调用：

```go
model.InsertLocalAssets(id, fileList, true)
```

其中 `id` 是目标文档 ID，`isUpload=true`。思源内核负责选择目标资源目录、重命名/去重和生成返回路径。

3. 官方 MCP 实测传入目录后，没有递归上传或压缩，而是返回本地目录链接，例如：

```text
file://D:\Github\siyuan-agent-bridge\ai_workspace\asset-upload-test
```

4. 将规范文件夹链接插入思源后，导出 Markdown 能保留可点击链接。此前测试示例：

```markdown
[测试文件夹](file:///D:/Github/siyuan-agent-bridge/ai_workspace/asset-upload-test)
```

这只证明思源能保存该链接，不代表 Bridge 应自行转换路径；最终仍应使用思源原生 API 的返回值。

5. Sisyphus MCP 的 `upload_asset` 只接受普通文件，目录会在上传前报：

```text
Local file path must point to a regular file
```

它不能作为本项目文件夹行为的实现参考。

### 5.2 已确认的 HTTP 路由与真实探针

思源 3.7.3 已确认暴露官方 MCP 所用模型函数的等价 HTTP 路由：

```text
POST /api/asset/insertLocalAssets
{
  "id": "目标文档 ID",
  "assetPaths": ["本地绝对路径"],
  "isUpload": true
}
```

返回 `data.succMap`。该接口需要鉴权、管理员角色且工作空间非只读。Bridge 直接调用该 HTTP API，不依赖或中转官方 MCP。

2026-07-28 在主空间“测试思源桥专用 / 附件接口测试-20260728”完成真实探针：

- 图片 `icon.png` 返回 `assets/icon-...png`；
- 普通文件 `README.md` 返回 `assets/README-...md`；
- 目录 `ai_workspace` 返回 `file://D:\...\ai_workspace`，不复制目录；
- 重复上传同一图片返回原有资源路径，未重复复制；
- `insertLocalAssets` 只处理资源并返回路径，不修改文档、不创建块，原有块 ID 保持不变；
- 真正写入文档需另调用块插入 API，根据返回路径生成 Markdown。

仍需在编码阶段验证失败补偿：上传成功但块插入失败时，能否只删除本批次新建且仍未被引用的资源；不能安全清理时必须报告残留，不能自动删除可能被其他文档复用的附件。

## 6. 返回结果建议

成功时返回每个项目的可核验结果：

```json
{
  "ok": true,
  "action": "insert_assets",
  "document": "/笔记本/目标文档",
  "document_id": "...",
  "inserted": [
    {
      "local_path": "...",
      "kind": "file",
      "resolved_path": "assets/...",
      "name": "...",
      "title": "...",
      "verified": true
    },
    {
      "local_path": "...",
      "kind": "directory",
      "resolved_path": "file://...",
      "name": "...",
      "title": "...",
      "verified": true
    }
  ],
  "snapshot_created": true
}
```

大文件暂停时建议返回：

```json
{
  "ok": false,
  "requires_confirmation": true,
  "threshold_bytes": 20971520,
  "large_files": [
    {"local_path": "...", "size_bytes": 26214400}
  ],
  "message": "整批尚未写入。如需上传这些大文件，请以相同参数增加 upload_large_files=true 重试。"
}
```

失败结果必须区分：预检失败（无写入）、快照失败（无写入）、资源处理失败、块插入失败、验证失败、补偿失败。不得只返回模糊异常。

## 7. 预计代码与文档改动范围

正式实现前按项目必读流程完整阅读 `AGENTS.md`、`docs/ARCHITECTURE.md`、`docs/DEVELOPMENT_GUIDE.md`。

预计涉及：

- `source_code/client.py`：仅增加经实测确认的思源原生资源 API 封装。
- `source_code/mcp_server.py`：`siyuan_edit` schema、参数校验、预检、事务协调、插入和验证。
- `tests/test_client.py`：API 请求和返回解析测试。
- `tests/test_mcp_server.py`：schema、权限、单组锚点、同位置多项目、name/title、官方图片清单、重名拒绝、20 MB 开关、文件夹链接、整批失败和补偿测试。
- `docs/ARCHITECTURE.md`：功能定案并实现后，把真实工具契约并入 `siyuan_edit` 章节。
- `docs/DEVELOPMENT_GUIDE.md`：补充附件写入必须验证的安全清单。
- `docs/思源API.md`：记录经源码和探针确认的 API。
- `plugins/siyuan-bridge/skills/siyuan-bridge/SKILL.md`、`README.md` 和对应同步副本：更新用户工作流和参数说明。

不应修改 `siyuan_create`，也不应新增第 10 个 MCP 工具。

## 8. 验证要求

该功能涉及 MCP 工具行为，必须完成项目规定的三层验证：

### 第一层：测试代码与真实 MCP 探针

- 单元测试全量通过。
- 直接启动当前源码，通过 JSON-RPC 调用真实 `siyuan_edit(insert_assets)`。
- 至少覆盖：单文件、官方图片扩展名与普通文件降级、文件夹、同位置多个项目、name/title 各种缺省组合、重名拒绝、超 20 MB 拒绝与开关重试、无效锚点整批不写、处理中途失败的补偿和最终读回验证。

### 第二层：能力库开发版 MCP

- 通过能力库临时注册项 `siyuan-bridge-dev-test` 加载当前开发源码。
- 公司电脑执行时使用 Python 3.13，并通过 `env -u PYTHONPATH` 避免 Hermes venv 的 Python 3.11 环境污染。
- 每次源码变化后重新 load，再真实调用受影响 action。
- 禁止使用当前环境中的 `mcp__siyuan_bridge__*` 用户版/生产版工具验证开发代码。

### 第三层：子代理调用

- 子代理实际调用已确认指向开发版的 MCP；如果没有开发版内置工具，则让子代理通过能力库调用临时注册项。
- 验证 AI 能只凭 tool schema 和 Skill 正确区分 `name` 与 `title`，完成文件、图片、文件夹插入及大文件上传开关处理。
- 禁止用用户版思源桥冒充开发版验收。

## 9. 本轮现场状态

截至 2026-07-28：

- 尚未编写附件插入代码。
- 已完成官方 MCP、Sisyphus MCP 和思源上传接口的初步调研及小文件实测。
- 本地临时测试目录为 `ai_workspace/asset-upload-test/`，Git 忽略/不提交；换电脑后需要重新创建测试文件。
- 调研中创建的测试文档位于测试用途笔记本，不能作为实现依赖。
- 本轮之前完整测试状态为 287 passed；该数字只是附件功能开发前基线。
- 已确认 Python Bridge 可直接调用 `/api/asset/insertLocalAssets`，不依赖官方 MCP。下一步实现从 client API 封装、预检和失败补偿边界开始。
