# `siyuan_edit(insert_assets)` 附件插入设计与实现

> 当前契约于2026-11-16按思源v3.8.10原生行为对齐，验证结果另行记录。第5节和第9节保留2026-07-28的调查与实测，不代表本轮multipart、部分成功或Linux/Docker已验收；后续修改仍须核对思源当前源码/API。

## 1. 目标与产品边界

为 SiYuan Bridge 增加“把本地文件或文件夹插入现有思源文档”的能力。

本功能不新增独立 MCP 工具，而是在现有 `siyuan_edit` 中增加：

```text
action="insert_assets"
```

用户可在一次调用中把多个本地项目插入同一位置；多位置分次调用。结果按原生逐项列表区分`success/partial/failed`；失败项不会撤销其他已成功附件或已插入内容。

独立插入附件仍通过`insert_assets`。已有Markdown文件的引用导入由create/edit的`markdown_file`处理，共用资源通道与`upload_large_files`。新建后再独立插入附件的调用链为：

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

图片与普通文件由Bridge打开，向`/api/asset/upload`发送multipart内容，携带目标文档`id`和`file[]`；不要求内核能读取Bridge宿主路径。目录显式调用`insertLocalAssets(isUpload=false)`，直接使用原生返回链接。两种入口复用内核资源逻辑，见[官方上传模型](https://github.com/siyuan-note/siyuan/blob/v3.8.10/kernel/model/upload.go)。展示Markdown按下文扩展名规则生成。

### 2.4 一次调用只支持一个位置

调用级参数使用编辑前引用阅读得到的一组双重锚点：

- `start_index`
- `start_id`

本批次所有资产按 `assets` 数组顺序插在该锚点块之后，不增加 `before/after` 参数。需要插入多个位置时，由 AI 在每次写入后重新引用阅读，再分多次调用；不在一次调用中维护多组会漂移的块坐标。

### 2.5 原生256MiB大文件提示

- 按[原生阈值](https://github.com/siyuan-note/siyuan/blob/v3.8.10/app/src/constants.ts#L115)，普通文件256MiB及以上先提示，阈值为268435456字节；取消旧20MiB规则，提示不作为硬限制。
- 目录只插入本地链接，不统计大小、不递归扫描。
- `insert_assets`未获同意时先暂停，不上传、不插入、不创建快照；返回`requires_confirmation: true`、文件实际大小和提示阈值。
- 用户明确同意后用`upload_large_files=true`承接同意；它同样用于create/edit的`markdown_file`。后者正文已写入时保留原引用并报告待处理附件，不要求重建整篇文档。
- `confirmed=true`仍表示用户同意写入，`upload_large_files`默认false，专门承接大文件提示后的同意。

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

优先按原生`succFiles.index`建立输入与结果映射，同批同名可保留。旧内核只有以基础文件名为键的`succMap`时，存在同名风险则逐文件调用，避免错配，不因同名整批拒绝。完整envelope和非零code中的成功项必须保留，见[原生上传结果](https://github.com/siyuan-note/siyuan/blob/v3.8.10/app/src/protyle/upload/uploadResult.ts#L58-L85)。

## 3. MCP参数契约

`siyuan_edit` 已增加：

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

字段契约：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `start_index` | integer | 是 | 写入前引用阅读中的块序号；本批次全部插在该块之后 |
| `start_id` | string | 是 | 与 `start_index` 配对的块 ID |
| `assets` | array | 是 | 本次要插入的本地文件/文件夹；至少一项 |
| `assets[].local_path` | string | 是 | Bridge可访问的本地路径；文件读取内容后上传，目录原路径交给原生链接分支，不映射操作系统路径 |
| `assets[].name` | string | 否 | 图片 alt 或文件/文件夹锚文本；缺省时按思源官方文件名逻辑生成 |
| `assets[].title` | string | 否 | Markdown 附加标题；图片中显示为下方标题，文件/文件夹通常用于悬停提示 |
| `upload_large_files` | boolean | 否 | 承接用户同意上传256MiB及以上普通文件，默认false；也用于create/edit的`markdown_file` |
| `confirmed` | boolean | 是 | 是否确认写入思源，必须 true |

当前决定不增加显式 `type=image/file/directory`：路径类型可在预检阶段判断，图片和普通文件又共用思源上传通道。若后续实测发现 MCP 进程看不到调用端本地路径，则该问题属于部署边界，需要重新设计，不能靠调用者声明类型解决。

## 4. 执行与安全流程

遵循原生逐项成功语义，报告资源处理、块写入和验证各阶段的实际结果。

### 阶段 A：只读预检

1. 校验 `confirmed=true`、action、document/document_id、assets 数组和字段类型。
2. 解析目标可见文档；若按路径定位，校验思源 live hpath 未变化。
3. 检查目标文档最终权限为 `read_write`，并经过 Privacy Rules 过滤。
4. 重新读取目标文档展示块列表。
5. 校验调用级 `start_index/start_id` 双重锚点。
6. 检查每个 `local_path` 是否存在，并识别普通文件或目录；其他类型整批拒绝。
7. 保留原始顺序，准备`succFiles.index`映射及旧`succMap`同名逐文件兼容，不整批拒绝同名。
8. 获取普通文件大小；256MiB及以上且未获`upload_large_files=true`同意时，先返回原生大文件提示。
9. 使用普通文件multipart、目录`isUpload=false`的原生分支；思源版本变化时重新核对参数与逐项返回。

阶段 A 任何失败均不得创建快照、上传资源或插入链接。

### 阶段 B：快照与执行

1. 创建思源工作空间快照；快照失败则停止。
2. 文件/图片上传multipart内容，目录调用`insertLocalAssets(isUpload=false)`取得原生链接；执行不自动重试。
3. 保留完整原生envelope，按逐项列表记录源路径、返回路径与失败原因，非零code也不丢弃成功项。
4. 按输入顺序为成功项生成Markdown，插入同一锚点后。
5. 继续读回并分别报告成功和失败项目，不因单项失败撤销已成功附件。

### 阶段C读回与部分失败报告

1. 重新读取目标文档，确认成功项已形成对应链接/引用，保留未成功项的原生原因。
2. 普通文件检查返回路径；目录只验证链接，不要求跨设备可访问。
3. 按原生逐项结果报告`success/partial/failed`，全部完成才报告全部成功。
4. 块插入或读回失败时报告实际已写内容、失败阶段和可能残留资源，不自动删除资源、不撤销其他成功项、不整批补偿。
5. 需要恢复时提示用户手动使用思源快照，不调用自动恢复/checkout接口。

## 5. 2026-07-28历史API调研与探针

本节保留当时依据。2026-11-16的当前HTTP协议见[思源API的本地资源插入](<思源API.md#本地资源插入>)；普通文件不再使用内核读取宿主路径，失败不再触发整批补偿。

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

实现已确认：上传成功但块插入或读回验证失败时，Bridge 可删除本批精确匹配的文档块；资源是否新建无法可靠判定，因此不自动清理可能被其他文档复用的附件。

## 6. 返回结果

成功时返回每个项目的可核验结果：

```json
{
  "ok": true,
  "action": "insert_assets",
  "status": "success",
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
  "threshold_bytes": 268435456,
  "large_files": [
    {"local_path": "...", "size_bytes": 268435456}
  ],
  "message": "整批尚未写入。如需上传这些大文件，请以相同参数增加 upload_large_files=true 重试。"
}
```

结果须区分预检失败（无写入）、快照失败（无写入）、资源逐项失败、块插入失败和验证失败，保留各项原生原因与文档ID。部分成功在开头明确说明已写内容及未完成附件；块写入失败可能残留资源，不做自动整批补偿。

## 7. 实际代码与文档改动范围

正式实现前按项目必读流程完整阅读 `AGENTS.md`、`docs/ARCHITECTURE.md`、`docs/DEVELOPMENT_GUIDE.md`。

已涉及：

- `source_code/client.py`：封装原生multipart与本地目录链接接口，保留完整envelope。
- `source_code/mcp_server.py`：`siyuan_edit` schema、参数校验、预检、事务协调、插入和验证。
- `tests/test_client.py`：API 请求和返回解析测试。
- `tests/test_mcp_server.py`：schema、权限、单组锚点、同位置多项目、name/title、官方图片清单、同名index映射与旧版逐文件、256MiB提示与同意、目录原生链接、部分成功保留及残留报告测试。
- `docs/ARCHITECTURE.md`：功能定案并实现后，把真实工具契约并入 `siyuan_edit` 章节。
- `docs/DEVELOPMENT_GUIDE.md`：补充附件写入必须验证的安全清单。
- `docs/思源API.md`：记录经源码和探针确认的 API。
- `plugins/siyuan-bridge/skills/siyuan-bridge/SKILL.md`、`README.md` 和对应同步副本：更新用户工作流和参数说明。

不新增第10个MCP工具；`siyuan_create`仅在已有`markdown_file`流程中共用资源处理与`upload_large_files`同意开关。

## 8. 验证要求

该功能涉及 MCP 工具行为，必须完成项目规定的三层验证：

### 第一层：测试代码与真实 MCP 探针

- 单元测试覆盖multipart真实字节与Unicode、Content-Length、鉴权、同名index、旧`succMap`逐文件、code与逐项结果不一致、256MiB边界及用户同意。
- 通过当前开发版JSON-RPC或能力库验证`insert_assets`及create/edit的`markdown_file`，不使用用户版。
- 场景包含单文件、原生图片扩展名、目录`isUpload=false`、同位置多项、name/title缺省、无效锚点无写入、部分成功保留和插入失败的残留报告。
- 本轮测试数字另行记录，不能把第9节历史结果当成本轮验收。没有Linux/Docker实机时保留未验证项。

### 第二层：能力库开发版 MCP

- 通过能力库已注册的 `siyuan-bridge-dev` 加载当前开发源码。档位切换见 `docs/DEVELOPMENT_GUIDE.md` 的「DSH 能力库中的三个思源桥」，不要新建注册项。
- 公司电脑执行时使用 Python 3.13，并通过 `env -u PYTHONPATH` 避免 Hermes venv 的 Python 3.11 环境污染。
- 每次源码变化后重新 load，再真实调用受影响 action。
- 禁止使用当前环境中的 `mcp__siyuan_bridge__*` 用户版/生产版工具验证开发代码。

### 第三层：子代理调用

- 子代理实际调用已确认指向开发版的 MCP；如果没有开发版内置工具，则让子代理通过能力库调用已注册的 `siyuan-bridge-dev`。
- 验证 AI 能只凭 tool schema 和 Skill 正确区分 `name` 与 `title`，完成文件、图片、文件夹插入及大文件上传开关处理。
- 禁止用用户版思源桥冒充开发版验收。

## 9. 2026-07-28历史现场状态

截至 2026-07-28：

- `source_code/client.py` 已封装 `/api/asset/insertLocalAssets`；`source_code/mcp_server.py` 已实现预检、快照、上传、Markdown 生成、锚点后插入、读回验证和保守补偿。
- 已完成官方 MCP、Sisyphus MCP 和思源上传接口的初步调研及小文件实测。
- 三层行为验证均已通过：直接 JSON-RPC 探针、能力库临时开发版 MCP、子代理调用均完成图片、普通文件和文件夹的顺序插入、`name/title` 展示及读回验证。
- 三层验证创建的文档块均已删除；临时 MCP 注册、参数文件、本地附件副本以及唯一未引用的测试附件均已清理。测试文档恢复为验证前内容，不能作为实现依赖。
- 开发前基线为 287 passed；实现后附件相关测试 187 passed，完整测试 299 passed。
- Python Bridge 直接调用 `/api/asset/insertLocalAssets`，不依赖官方 MCP。
