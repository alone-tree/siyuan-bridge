# 思源 API 能力地图

本文记录 SiYuan Bridge 后续实现读写能力时可使用的思源 HTTP API。目标不是复刻完整 API 文档，而是说明这些端点在本项目中的用途、风险和 MCP 封装策略。

参考资料：

- SiYuan 官方 API 文档：https://github.com/siyuan-note/siyuan/blob/master/API.md
- Kernel API 社区文档：https://leolee9086.github.io/my-siyuan-dev-guide/kernel-api/

## 总体原则

思源 API 是底层能力集合，MCP 工具是面向 AI 的高层工作流。不要把思源端点逐个暴露给 AI。

本项目的封装原则：

1. AI 操作文档路径、块窗口和结构化编辑动作，不直接操作底层 SQL 或仓库快照。
2. MCP server 负责把引用阅读中的块序号 + 块 ID 校验为具体块操作。
3. 破坏性 API 不直接暴露给 AI。
4. 写入前必须创建思源工作空间快照。
5. 回滚第一阶段由用户手动完成，不提供 AI 自动 checkout 或 rollback 工具。

## 能力分层

| 能力层 | 代表端点 | 项目用途 | MCP 暴露策略 |
|--------|----------|----------|--------------|
| 系统 | `/api/system/version` | 连接检查、启动诊断 | 已通过 `siyuan_start` 间接使用 |
| 笔记本 | `/api/notebook/lsNotebooks`, `/api/notebook/openNotebook`, `/api/notebook/closeNotebook` | 枚举笔记本；搜索/索引前临时打开关闭笔记本 | 内部使用，不单独暴露 |
| 笔记本管理 | `/api/notebook/createNotebook`, rename/remove 类端点 | 创建、重命名、删除笔记本 | 不开放。笔记本管理是人工决策 |
| 文档树读取 | `/api/filetree/listDocsByPath`, `/api/filetree/getHPathByID`, `/api/filetree/getIDsByHPath` | 解析文档路径、结构和 ID | 内部使用或现有只读工具间接使用 |
| 文档创建 | `/api/filetree/createDocWithMd`、`/api/filetree/createDocByParent` | 创建新文档；后者为身份式寻址，自 v3.8.9 起存在 | `createDocWithMd`暴露为`siyuan_create`；收集箱移出时按版本在两者间选择 |
| 收集箱 | `/api/inbox/getShorthands`、`/api/inbox/getShorthand`、`/api/inbox/removeShorthands` | 读取云端收集箱条目、单条正文、删除条目；三者自 v3.0.0 起全部版本存在 | 由`siyuan_list`、`siyuan_read`、`siyuan_find`、`siyuan_start`只读使用；删除由`siyuan_doc_manage(action=delete)`封装 |
| 文档结构变更 | `/api/filetree/renameDocByID`, `/api/filetree/removeDocByID`, `/api/filetree/moveDocsByID`, `/api/filetree/duplicateDoc` | 重命名、删除、移动、复制文档 | 通过 `siyuan_doc_manage` 封装，写前快照 |
| 块读取 | `/api/block/getBlockKramdown`, `/api/block/getChildBlocks` | 读取块内容、构建块窗口、引用阅读定位 | 内部使用 |
| 块编辑 | `/api/block/updateBlock`, `/api/block/appendBlock`, `/api/block/insertBlock`, `/api/block/prependBlock` | 实现文档内增删改 | 只由 `siyuan_edit` 内部调用 |
| 块删除/移动 | `/api/block/deleteBlock`, `/api/block/moveBlock` | 删除或移动块 | 删除由 `siyuan_edit` 的 `delete` 动作封装；移动暂不开放 |
| 块 UI | fold/unfold 类端点 | 折叠、展开 | 不开放 |
| 属性 | `/api/attr/getBlockAttrs`, `/api/attr/setBlockAttrs` | 读取或设置块属性 | `setBlockAttrs` 已由 `siyuan_doc_manage(action=set_tags)` 内部使用（文档块 `tags` 属性，写前快照）；块级属性标记暂不开放 |
| 搜索 | `/api/search/fullTextSearchBlock` | 全文搜索正文、标题和块 | 已作为 `siyuan_find` 的召回源 |
| SQL | `/api/query/sql` | 结构化读取 blocks 表、块搜索和定位 | `siyuan_find(mode=sql)` 使用 `mode=readonly`，保留 `limit/truncated`；只输出真实可见块，不回显任意列或聚合报表 |
| 引用关系 | `/api/query/sql` 查询 `refs`、`spans` 并关联 `blocks` | 正向查询文档引用哪些块，反向查询文档被哪些块引用；写前检查即将消失的 ID 是否仍被引用 | 只读查询通过 `siyuan_operate` 的 `check_forward_references` / `check_backward_references` 暴露；写入保护仍内部执行 |
| 导出 | `/api/export/exportMdContent` 等 | 读取文档 Markdown、导出资源 | 已用于阅读；不作为写入主路径 |
| 资源 | `/api/asset/upload`、`/api/asset/insertLocalAssets` | 普通文件/图片上传multipart内容；目录返回原生本地链接 | 由`insert_assets`和create/edit的`markdown_file`附件后处理内部使用 |
| 仓库快照 | `/api/repo/createSnapshot`, `/api/repo/getRepoSnapshots` | 写入前备份；必要时帮助用户找到恢复点 | `createSnapshot` 内部强制使用；查询可后续做只读诊断 |
| 仓库恢复 | `/api/repo/checkoutRepo` | 恢复整个工作空间到某个快照 | 不暴露给 AI |
| 历史 | history rollback 类端点 | 文档/资源历史恢复 | 第一阶段不使用；只作为人工恢复参考 |
| 通知 | `/api/notification/pushMsg`, `/api/notification/pushErrMsg` | 写入完成或失败时通知用户 | 内部使用 |
| 同步 | `/api/system/getConf`、`/api/sync/getSyncSourceState`、`/api/sync/performSync`、`/api/sync/getSyncInfo` | 配置与同步源预检、原生同步和结果读取 | 仅通过`siyuan_operate(action=sync)`协调，不修改同步配置；`upload`只承接当次原生方向选择 |
| 账号/设置/插件/集市 | account、setting、bazaar、plugin 类端点 | 思源应用状态管理 | 不属于本项目范围 |

## 当前写入 API 组合

当前暴露三个会修改思源内容的 MCP 工具：

| MCP 工具 | 用户语义 | 内部 API 组合 |
|----------|----------|---------------|
| `siyuan_edit` | 在已有可见文档中替换、追加、删除、插入文本、编辑普通 Markdown 表格或插入本地附件 | 隐私检查；引用阅读定位校验；delete/multi 写前查询 `refs`；`repo/createSnapshot`；`asset/upload`或目录用`asset/insertLocalAssets(isUpload=false)`；`block/updateBlock` / `appendBlock` / `insertBlock` / `deleteBlock`；`notification/pushMsg` |
| `siyuan_create` | 通过完整可读路径创建、覆盖或新增同名文档；`markdown_file`导入已有本地`.md`（与`markdown`互斥），写后处理标准本地引用 | 隐私和路径检查；overwrite写前查反链；`repo/createSnapshot`；`filetree/createDocWithMd`或块删除/追加；`asset/upload`或目录用`asset/insertLocalAssets(isUpload=false)`；`block/updateBlock`；通知与路径同步 |
| `siyuan_doc_manage` | 管理文档树：改名、移动、删除、复制、导出；设置文档标签 | 权限检查；`copy/export` 允许只读源文档；`rename/move/delete/set_tags` 需可写；`delete` 写前扫描子孙权限和整棵子树反链；`move` 写前检查源文档祖先链和目标父路径权限；写操作调用 `repo/createSnapshot`；内部使用 `renameDocByID` / `moveDocsByID` / `removeDocByID` / `duplicateDoc` / `exportMdContent` / `attr/setBlockAttrs`（set_tags）；写后用 `getHPathByID` 确认路径同步 |

`/api/inbox/getShorthands`的返回被内核多包一层`code/msg/data`，真正的`pagination`与`shorthands`在第二层`data`里；`getShorthand`与`removeShorthands`没有这层重复包装。

收集箱移出复用思源原生链路：读取`getShorthand`→`createDocByParent`（或旧版`createDocWithMd`）→`removeShorthands`，只在建文档成功后删除云端原件；删除失败保留已建文档并如实上报。

AI 不需要知道这些底层端点。它只提供：

```text
document
action
start_index
start_id
end_index/end_id（可选）
markdown、table_edit 或 assets
confirmed
```

## 本地资源插入

普通文件和图片由Bridge打开，向内核发送文件内容；内核不需要能读取Bridge宿主的绝对路径。

```text
POST /api/asset/upload
Content-Type: multipart/form-data; boundary=...
id=<目标文档ID>
file[]=<文件名与实际字节，可多项>
```

目录仍用原生本地链接分支，不读取目录内容、不递归、不打包，也不自行转换路径。

```json
{
  "id": "<目标文档ID>",
  "assetPaths": ["<目录绝对路径>"],
  "isUpload": false
}
```

该JSON发送至`POST /api/asset/insertLocalAssets`，直接使用内核返回的本地链接。目标assets目录、命名和哈希去重交给思源[上传模型](https://github.com/siyuan-note/siyuan/blob/v3.8.10/kernel/model/upload.go)。两种端点只处理资源，文档插入或链接更新仍由块API完成；Bridge不经官方MCP中转。

内部`upload_assets(document_id, file_paths)`与`insert_local_assets_result(document_id, paths, is_upload=False)`均返回完整原生envelope，保留非零code下的成功项，不自动重试。对照[原生上传结果](https://github.com/siyuan-note/siyuan/blob/v3.8.10/app/src/protyle/upload/uploadResult.ts#L58-L85)，按逐项列表识别`success/partial/failed`，优先用`succFiles.index`映射输入；旧内核只有`succMap`且存在同名风险时逐文件调用，不整批拒绝。

256MiB及以上普通文件遵循[原生提示阈值](https://github.com/siyuan-note/siyuan/blob/v3.8.10/app/src/constants.ts#L115)，高层`upload_large_files`承接用户同意，适用于`insert_assets`和create/edit的`markdown_file`；提示不作为硬限制。已成功附件不因别项失败撤销。块插入/更新失败报告已写内容和可能残留，不自动删资源或整批补偿，恢复由用户手动使用快照。

或：

```text
title
path
markdown
if_exists（可选：reject / overwrite / create_new）
confirmed
```

## 正向与反向引用查询

`siyuan_operate` 用 `check_forward_references` 查询本文档引用哪些块，用 `check_backward_references` 查询哪些块引用本文档，不提供公开 `direction` 参数。查询集合包含文档 ID 和 live `blocks.root_id` 下的全部正文块 ID，不能用引用阅读展示块代替。

关系同时覆盖 `refs` 中的标准块引用、可识别嵌入块，以及 `spans.markdown` 中的 `siyuan://blocks/<ID>` 链接，统一按 `(目标 ID, 来源块 ID)` 去重；本篇内部引用也计入。正向以本文档块为来源筛选，反向以本文档块为目标筛选。

本文档返回详情：正向按目标文档展示目标块，反向按来源文档展示来源块；每篇最多 3 个唯一对端块，每块原始 Markdown 最多 2000 字符，文档按关系数降序、完整路径升序排列。子文档递归查询，只展示次数汇总。`limit` 默认 10，`"none"` 不限，仅限制文档展示，不限制关系总数。隐藏对端只计关系次数，不泄露路径、ID、内容或文档数；隐藏子文档仍遵循既有隐私过滤规则。完整参数与返回契约见 `ARCHITECTURE.md` 的 `siyuan_operate` 章节。

两个查询均只读，不创建快照。写前删除反链保护不变：仍排除同一删除集合内部的关系，并由既有 `reference_policy` 控制拒绝或经确认破坏引用。

## 块树读取与块 ID 诊断视图

思源提供 `/api/block/getChildBlocks`，可按父块 ID 读取直接子块。官方说明中也明确：标题下方的块会被视为标题块的子块。这说明思源文档不是一张仅靠全局 `sort` 就能线性还原的表，而是由 `parent_id + sort` 共同决定阅读顺序的块树。

本项目的 `include_block_ids=true` 诊断视图采用 `/api/block/getChildBlocks` 作为主路径：

```text
POST /api/block/getChildBlocks
{ "id": "<parent_block_id>" }
```

这样做的原因是：实测部分导入长文档中，所有根级段落的 `sort` 都是同一个值（例如全为 `10`），仅靠 SQL 的 `parent_id + sort` 仍无法还原同级阅读顺序；而 `getChildBlocks` 返回值已经按思源内部顺序排列。

SQL 一次性读取仍保留为诊断/备用能力：

```sql
SELECT id, parent_id, root_id, type, subtype, markdown, content, sort
FROM blocks
WHERE root_id = '<doc_id>' AND type != 'd'
ORDER BY parent_id, sort
```

但它不能作为长文档块 ID 诊断视图的主顺序来源。

诊断视图仍然不是思源原生结构的完整还原：

- 文档块 `d` 不作为正文渲染；文档 ID 已在工具头部返回。
- 列表容器块 `l` 不渲染，但会继续遍历其列表项。
- 列表项 `i` 和表格 `t` 的 Markdown 通常已包含子内容，渲染后不再递归子孙，避免重复。
- 超级块 `s` 只显示块 ID 注释，不渲染其完整 Markdown，然后继续遍历子块，避免超级块内容和子块内容重复。
- 空块不渲染。
- 超级块、数据库、属性视图仍不能靠 Markdown 诊断视图完整表达。

## 写前快照与手动回滚

每次编辑或创建文档之前，MCP server 必须先调用：

```text
/api/repo/createSnapshot
```

该 API 当前只解析 `memo` 参数；它创建的是整个工作空间/数据仓库快照，不支持通过 `path` 限定单篇文档或局部路径，也不支持 `tags` 参数。

快照 memo 应至少包含：

```text
siyuan-bridge
operation type
target notebook/document
timestamp
```

如果快照创建失败，写入应失败，不继续修改思源内容。

实测注意：新工作空间如果尚未初始化“数据仓库密钥”，`/api/repo/createSnapshot` 会返回失败。用户需要先在思源 UI 的 `设置 - 关于 - 数据仓库密钥` 中初始化密钥，再由本工具创建快照。本工具不保存、不生成数据仓库密钥。

实测返回行为：`/api/repo/createSnapshot` 成功时可能返回 `data: null`，不提供 snapshot id。初始化后如果工作空间没有新的数据变更，调用成功也可能不会在 `/api/repo/getRepoSnapshots` 中新增一条带 memo 的快照。后续实现写入工具时，需要在真实写入前后再验证“写前快照”是否能稳定形成可恢复点。

第一阶段不实现 `siyuan_rollback`。原因：

- 回滚不是日常操作，只在 AI 编辑造成严重问题时使用。
- 自动回滚需要处理块、资源、文档树、引用关系，复杂度高。
- `checkoutRepo` 会恢复整个工作空间，风险过高，不应由 AI 调用。
- 用户手动从思源快照恢复更符合这种事故级操作的风险边界。

工具执行成功后应返回快照信息，方便用户在需要时定位恢复点。

## 思源内置同步

原生流程依据思源v3.8.10的[同步前端](https://github.com/siyuan-note/siyuan/blob/v3.8.10/app/src/sync/syncGuide.ts#L228-L328)和[同步源模型](https://github.com/siyuan-note/siyuan/blob/v3.8.10/kernel/model/sync_source.go#L75-L151)。

```text
POST /api/system/getConf {}
POST /api/sync/getSyncSourceState {}
POST /api/sync/performSync {} 或原生upload与预览标识
POST /api/sync/getSyncInfo {}
```

`get_conf()`严格解包原生`data.conf`并返回配置字典，依据[系统配置响应](https://github.com/siyuan-note/siyuan/blob/v3.8.10/kernel/api/system.go#L813-L830)及[配置响应结构](https://github.com/siyuan-note/siyuan/blob/v3.8.10/kernel/apicontract/system.go#L64-L66)；`get_sync_source_state()`返回原生state。先检查只读、数据仓库密钥和同步启用状态，返回原生设置指引而不改配置。仅`getSyncSourceState`端点404/405时走已核对旧版流程；其他错误不吞掉。预览可能触发索引，不能当作绝对只读探针。

内部`perform_sync(timeout=10, upload=None, source_state=None)`保留旧调用兼容，执行不自动重试。普通同步为空请求；source非空且cloudID空时原生自动上传并绑定预览；pending或mode=3先返回`requires_choice`而不执行。公开可选`upload:boolean`无默认值，只承接用户当次上传/下载选择。source/localID/cloudID只保存在当前连接，由NativeSync传递，连接重置清空；不开放AI手填state、不自动替换预览、不新增过期秒数。

`timeout_seconds`默认10、范围5-120，只调整等待时间。超时为`api:sync_timeout`，执行中网络失败为`api:sync_connection`；此时同步可能已开始，不自动重发。返回原生结果和状态，不把HTTP成功视为同步完成。同步模式、提供方或云目录配置不向AI开放修改。

## 不直接暴露的高风险 API

以下 API 不应作为 MCP 工具直接开放：

| API 类型 | 原因 |
|----------|------|
| `checkoutRepo` | 恢复整个工作空间，可能覆盖用户后续修改 |
| `removeNotebook` | 删除范围大，笔记本管理是人工决策 |
| `moveBlock` | 块移动会改变正文结构，暂不开放 |
| `deleteBlock` 独立工具 | 删除由 `siyuan_edit` 的 `delete` 动作封装，仍受引用阅读定位、反链检查和快照约束 |
| 任意 SQL 工具 | 容易泄露隐私边界外的数据，也可能诱导 AI 绕过高层工具 |
| 设置、同步、账号、插件、集市类 API | 和笔记编辑目标无关，风险大于收益 |

## 后续可能扩展

后续可以在不增加底层暴露面的前提下增加能力：

| 能力 | 可能方案 |
|------|----------|
| 块 ID 引用 | 已由 `siyuan_read(include_block_ids=true)` 返回块序号和块 ID 定位头；删除 ID 前由 `refs` 反链保护 |
| 资源写入 | 已由 `siyuan_edit(action=insert_assets)` 封装本地资源处理、Markdown 插入和读回验证 |
| 只读快照查询 | 增加诊断工具列出由本项目创建的最近快照 |
| 自动回滚 | 等写入稳定后再评估块级操作日志，不直接使用 repo checkout |
| AI 修改标记 | 用 `setBlockAttrs` 标记由 AI 创建或修改的块 |
