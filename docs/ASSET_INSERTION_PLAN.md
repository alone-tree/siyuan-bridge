# `siyuan_edit(insert_assets)` 附件插入实施计划

> 状态：设计已基本定案，尚未开始实现。本文是 2026-07-28 讨论与实测的完整交接记录；后续开发必须先重新核对思源当前源码/API，再修改项目代码。

## 1. 目标与产品边界

为 SiYuan Bridge 增加“把本地文件或文件夹插入现有思源文档”的能力。

本功能不新增独立 MCP 工具，而是在现有 `siyuan_edit` 中增加：

```text
action="insert_assets"
```

用户可在一次调用中把多个本地项目插入同一位置，也可以让每个项目插入不同位置。一次调用对用户表现为一个完整操作：只有资源处理、文档插入和最终验证全部成功，才报告成功。

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

### 2.4 一次调用支持多个位置

每个资产项可以独立携带编辑前引用阅读得到的双重锚点：

- `start_index`
- `start_id`
- `position`：`before` 或 `after`

多个资产项可使用同一组锚点，表示在同一位置按调用顺序插入；也可分别使用不同锚点。

锚点必须全部基于同一次写入前的 `siyuan_read(include_block_ids=true)` 结果。实现时先一次性校验所有锚点，再按不会导致后续定位漂移的顺序执行；不得边插入边用已变化的块序号重新解释后续资产项。

### 2.5 大文件阈值为 20 MB

- 仅实际要上传的普通文件参与 20 MB 检查。
- 文件夹只插入本地超链接，不统计目录大小、不递归扫描，因此不参与阈值判断。
- 批次内任一普通文件超过 20 MB，整批暂停：不上传任何文件、不插入任何链接、不创建快照。
- 返回 `requires_confirmation: true`，列出超限文件、实际大小和阈值。
- 用户明确同意后，使用相同参数并增加 `confirm_large_files=true` 重试整个批次。
- `confirmed=true` 表示同意修改思源；`confirm_large_files=true` 只表示额外同意处理超限文件，两个确认不能互相替代。

## 3. 计划中的 MCP 参数

建议在 `siyuan_edit` 增加：

```json
{
  "action": "insert_assets",
  "document": "/笔记本/父文档/目标文档",
  "assets": [
    {
      "local_path": "D:/materials/chart.png",
      "title": "收入结构",
      "start_index": 12,
      "start_id": "20260728120000-abcdefg",
      "position": "after"
    },
    {
      "local_path": "D:/materials/source-files",
      "title": "源文件目录",
      "start_index": 30,
      "start_id": "20260728120100-hijklmn",
      "position": "before"
    }
  ],
  "confirm_large_files": false,
  "confirmed": true
}
```

建议字段契约：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `assets` | array | 是 | 本次要插入的本地文件/文件夹；至少一项 |
| `assets[].local_path` | string | 是 | 交给思源处理的本地路径，不由 Bridge 改写为另一操作系统格式 |
| `assets[].title` | string | 否 | 文档中显示的标题；缺省时使用思源返回结果或源路径名 |
| `assets[].start_index` | integer | 是 | 写入前引用阅读中的块序号 |
| `assets[].start_id` | string | 是 | 与序号配对的块 ID |
| `assets[].position` | enum | 否 | `before` / `after`，建议默认 `after` |
| `confirm_large_files` | boolean | 否 | 是否确认上传本批次中超过 20 MB 的普通文件，默认 false |
| `confirmed` | boolean | 是 | 是否确认写入思源，必须 true |

当前决定不增加显式 `type=image/file/directory`：路径类型可在预检阶段判断，图片和普通文件又共用思源上传通道。若后续实测发现 MCP 进程看不到调用端本地路径，则该问题属于部署边界，需要重新设计，不能靠调用者声明类型解决。

## 4. 事务与安全流程

目标语义是“整批成功或整批失败”。计划流程：

### 阶段 A：只读预检

1. 校验 `confirmed=true`、action、document/document_id、assets 数组和字段类型。
2. 解析目标可见文档；若按路径定位，校验思源 live hpath 未变化。
3. 检查目标文档最终权限为 `read_write`，并经过 Privacy Rules 过滤。
4. 重新读取目标文档展示块列表。
5. 一次性校验每个资产项的 `start_index/start_id` 双重锚点及 position。
6. 检查每个 `local_path` 是否存在，并识别普通文件或目录；其他类型整批拒绝。
7. 获取普通文件大小；任一超过 20 MB 且未提供 `confirm_large_files=true` 时，整批暂停并返回确认要求。
8. 查明思源当前版本实际提供的原生资源 API、参数、返回值和回滚能力；在开始实施前必须完成这项源码/API 探针。

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

### 5.2 尚未确认、下次必须先解决

官方 MCP 调用的是 Go 内部 `model.InsertLocalAssets`。尚未最终确认当前思源版本是否暴露等价 HTTP API（可能为 `/api/asset/insertLocalAssets` 或其他路由）、其参数和权限；不能仅凭函数名猜接口。

下次开发的第一步应是：

1. 查当前思源源码的 `kernel/api/router.go`、`kernel/api/asset.go` 和 `kernel/model/asset.go`。
2. 确认是否存在可从 Python Bridge 调用的统一 HTTP 路由。
3. 对该路由做真实探针：同一调用分别传图片、普通文件和目录，记录原始请求、返回和文档/资源变化。
4. 若统一路由存在，文件和目录全部走该路由。
5. 若只有普通文件上传 API：普通文件走思源附件 API；目录处理必须继续查找思源是否有独立原生路径转换/插入 API。没有确认前不得自行编写 Windows/macOS 转换代码。

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
      "start_id": "...",
      "position": "after",
      "verified": true
    },
    {
      "local_path": "...",
      "kind": "directory",
      "resolved_path": "file://...",
      "start_id": "...",
      "position": "after",
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
  "message": "整批尚未写入。确认后以相同参数增加 confirm_large_files=true 重试。"
}
```

失败结果必须区分：预检失败（无写入）、快照失败（无写入）、资源处理失败、块插入失败、验证失败、补偿失败。不得只返回模糊异常。

## 7. 预计代码与文档改动范围

正式实现前按项目必读流程完整阅读 `AGENTS.md`、`docs/ARCHITECTURE.md`、`docs/DEVELOPMENT_GUIDE.md`。

预计涉及：

- `source_code/client.py`：仅增加经实测确认的思源原生资源 API 封装。
- `source_code/mcp_server.py`：`siyuan_edit` schema、参数校验、预检、事务协调、插入和验证。
- `tests/test_client.py`：API 请求和返回解析测试。
- `tests/test_mcp_server.py`：schema、权限、锚点、多项目、多位置、20 MB 确认、文件夹链接、整批失败和补偿测试。
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
- 至少覆盖：单文件、图片、文件夹、同位置多个项目、多位置插入、超 20 MB 拒绝与确认重试、无效锚点整批不写、处理中途失败的补偿和最终读回验证。

### 第二层：能力库开发版 MCP

- 通过能力库临时注册项 `siyuan-bridge-dev-test` 加载当前开发源码。
- 公司电脑执行时使用 Python 3.13，并通过 `env -u PYTHONPATH` 避免 Hermes venv 的 Python 3.11 环境污染。
- 每次源码变化后重新 load，再真实调用受影响 action。
- 禁止使用当前环境中的 `mcp__siyuan_bridge__*` 用户版/生产版工具验证开发代码。

### 第三层：子代理调用

- 子代理实际调用已确认指向开发版的 MCP；如果没有开发版内置工具，则让子代理通过能力库调用临时注册项。
- 验证 AI 能只凭 tool schema 和 Skill 正确完成文件、图片、文件夹插入及大文件二次确认。
- 禁止用用户版思源桥冒充开发版验收。

## 9. 本轮现场状态

截至 2026-07-28：

- 尚未编写附件插入代码。
- 已完成官方 MCP、Sisyphus MCP 和思源上传接口的初步调研及小文件实测。
- 本地临时测试目录为 `ai_workspace/asset-upload-test/`，Git 忽略/不提交；换电脑后需要重新创建测试文件。
- 调研中创建的测试文档位于测试用途笔记本，不能作为实现依赖。
- 本轮之前完整测试状态为 287 passed；该数字只是附件功能开发前基线。
- 当前核心未决问题只有一个：Python Bridge 可调用的思源原生“插入本地资源”HTTP API 的准确路由、参数、返回和回滚能力。下次从这里开始，不要先写路径转换或上传代码。
