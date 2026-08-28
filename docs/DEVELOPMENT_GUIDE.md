# SiYuan Bridge 开发指南

## 修改前必须完整阅读

任何 AI 或开发者在修改代码前，必须完整阅读以下文档。不能只读开头，不能只 grep 局部，不能跳过中间或后半段。

必读：

1. 根目录 `AGENTS.md`
2. `docs/ARCHITECTURE.md`
3. 本文档 `docs/DEVELOPMENT_GUIDE.md`

按任务追加阅读：

| 修改范围 | 还必须阅读 |
|---|---|
| MCP 工具 schema、参数、返回格式 | `plugins/siyuan-bridge/skills/siyuan-bridge/SKILL.md`、`README.md` |
| Workspace Index 工作流 | `plugins/siyuan-bridge/skills/siyuan-index-builder/SKILL.md` |
| 思源 API 封装 | `docs/思源API.md`、`source_code/client.py` |
| 隐私和权限 | `source_code/ignore.py`、`source_code/agent_notebook.py`、相关测试 |
| 阅读、编辑、表格、文档管理 | `source_code/mcp_server.py`、相关测试 |
| 插件前端 | `docs/FRONTEND.md`、`siyuan-plugin/` |
| 发布和安装 | `mcp_configs/` |
| 历史问题排查 | `docs/devlog.md`，优先读最近日期；不要把旧计划当当前事实 |

如果没有完整阅读这些材料，不准开始改代码。

## 文档职责

| 文档 | 职责 |
|---|---|
| `AGENTS.md` | AI 入口规则、协作约束、常用命令、强制阅读指引 |
| `docs/ARCHITECTURE.md` | 当前真实架构、工具契约、数据流、设计取舍、已知债务、未来计划 |
| `docs/architecture-map.html` | 面向人类的产品架构图；整体架构大改时必须和 `ARCHITECTURE.md` 同步 |
| `docs/DEVELOPMENT_GUIDE.md` | 开发流程、同步清单、验证清单、已知真实风险 |
| `docs/FRONTEND.md` | 思源插件前端实现细节、加载方式、配置写入、踩坑和验证 |
| `docs/IDEAS.md` | 未承诺实施的粗略想法；不作为路线图或当前债务 |
| `docs/devlog.md` | 工程日志、排障记录、阶段性结果；新记录应放最前 |
| `docs/思源API.md` | 思源底层 API 能力地图和本项目封装策略 |

文档同步规则：

- 架构结论写入 `ARCHITECTURE.md`。
- 开发流程或验证规则写入 `DEVELOPMENT_GUIDE.md`。
- 工程过程和排障写入 `devlog.md`。
- 不要把长期架构塞进 devlog。
- 不要让 README、Skill、Architecture、tool schema 互相矛盾。

## 文档新增与修改规则

默认不新建文档。只有内容有独立生命周期、篇幅会明显拖累主文档，或是短期草案时才允许新建。

修改归属：

- 当前架构、工具契约、数据流、设计取舍、已确认债务：`ARCHITECTURE.md`；整体架构大改时同步 `architecture-map.html`。
- 开发流程、验证规则、文档维护规则、MCP 工具描述写法：`DEVELOPMENT_GUIDE.md`。
- 插件前端细节和踩坑：`FRONTEND.md`。
- 未承诺 idea：`IDEAS.md`，每条尽量 1-5 行。
- 工程过程、排障记录、验证结果：`devlog.md`，新记录放最前。
- 用户说明和常见 QA：`README.md`。

禁止：

- 不要为一次临时计划创建永久文档。
- 不要在 devlog 写长期架构事实。
- 不要让同一工具契约在多个文档重复维护。
- 设计草案定案后，迁移结论并删除草案。

## 修改工具面时必须同步

只要改 MCP 工具名称、参数、默认值、返回格式、权限边界或行为语义，必须同步检查：

- `source_code/mcp_server.py` 的工具实现
- `tool_specs()`
- `tests/`
- `plugins/siyuan-bridge/skills/siyuan-bridge/SKILL.md`
- `plugins/siyuan-bridge/skills/siyuan-index-builder/SKILL.md`，如果影响索引工作流
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/思源API.md`，如果涉及底层 API 封装
- `docs/devlog.md`，记录实现过程和验证结果

不能只改实现，不改 schema。AI 客户端看到的是 `tool_specs()`，Skill 和 README 决定 AI 怎么调用。

修改 `tool_specs()` 的 description、参数说明、enum 顺序或面向 AI 的报错文案前，必须完整阅读下一节「编写 MCP 工具描述」。

## 编写 MCP 工具描述

工具描述是 AI 的第一调用界面。写错会导致错误的第一次调用；报错再纠正也浪费一轮。改任何工具描述前先读本节全文，再对照 `docs/ARCHITECTURE.md` 该工具的完整契约，不要只补最近踩到的那一句。

### 分层

| 层 | 写什么 | 不写什么 |
|---|---|---|
| 工具总描述 | 总体概括工具的核心功能 | 单个参数的长说明、兼容旧名、完整语法教程 |
| 参数描述 | 只解释这一个参数：默认选项、何时用、何时不用 | 把别的参数或整个工具再讲一遍 |
| Skill | 调用决策（什么意图用什么参数） | 重复 schema 全文 |
| 报错 | 指出无效点，并给出完整正确示例 | 暴露兼容旧参数名 |

同一事实只出现在最合适的一层。总描述、`action`、`markdown` 不要各写一遍「default vs single」。

整个工具的描述应当使用同一种语言，不要中英夹杂。

### 原则

1. **默认路径写在最前。** enum 第一项应是默认动作。特例写成「仅当…才用」。
2. **不要把最近的修补堆进总描述。** 改一处问题就先看总描述是否还覆盖该工具的全部主路径。
3. **不对等的能力不要写成对等二选一。** 例如新写正文默认 `markdown`；`markdown_file` 只导入已有本地文件。
4. **兼容别名对 AI 不可见。** 公开 schema、工具描述、Skill、返回值和报错都不出现旧名；后端静默接受即可。
5. **不发明上游没有的语法。** 思源/FTS5 已有规则就遵循；需要时把合法输入自动规范化，而不是另造一套。
6. **用示例固定易错点。** 对照示例比抽象规则有效，但每个参数最多一两个。
7. **报错要能直接改调用。** 写明「xx 参数无效」或「应改用哪个 action」，并给完整正确示例。不要只说失败。

### 正误示例

示例是真实会出现在 schema 或返回里的完整原文。解释单独写，说明好在哪、坏在哪。

#### 1. 总描述只概括，细则放参数

正例（`siyuan_edit` 总描述）：

```
Edit a visible SiYuan document using block coordinates from siyuan_read(include_block_ids=true). Requires confirmed=true and creates a workspace snapshot before writing. Choose the edit method with action; pass new body text with markdown.
```

正例（`assets` 参数）：

```
For action=insert_assets only: each item must be an object {local_path, name?, title?}; items are inserted in order after the same anchor. Do not pass asset_paths or path.
```

误例（`siyuan_edit` 总描述）：

```
Edit a visible SiYuan document using block coordinates from siyuan_read(include_block_ids=true); requires confirmed=true. For action=insert_assets, pass assets as an array of objects with required local_path (optional name/title), not asset_paths or path. For body edits, pass markdown. Use markdown_file only to import an already existing local .md file; do not create a temporary markdown file just to import it. For replacements, default to default_block_replace. Use single_block_replace only when one existing block must stay one block and default_block_replace would break references to that block ID.
```

解释：正例总描述只覆盖工具是什么、写入前提、用哪个参数选动作。`insert_assets` 的字段约束放在 `assets` 参数。误例把最近修过的三件事都堆进总描述，八个 action 几乎看不见。

#### 2. 默认动作在前，特例收窄

正例（`action`）：

```
default_block_replace: delete the target block(s) and create new block(s) with new IDs, without preserving formatting. single_block_replace: replace the text of exactly one block and keep its ID and formatting (color, font size); only when that block is referenced by other blocks and those references must be kept. insert_after / insert_before: insert markdown next to an anchor. append: add to the end; no start_index/start_id. delete: remove one block or an inclusive range. table_edit: edit a normal Markdown table via table_edit. insert_assets: insert local files/folders after one anchor using assets.
```

误例（`action`）：

```
Choose single_block_replace only when replacing exactly one block with exactly one block and preserving its block ID matters. Choose multi_block_replace when replacing a range or when the new markdown may create multiple blocks; old block IDs and references will be invalidated. Choose insert_assets to insert local files/folders after one existing anchor.
```

解释：正例 enum 以默认替换开头，并列出全部动作。误例把 `single_block_replace` 放在最前，还说 preserving block ID matters，AI 会尽量选 single；`insert_after` / `append` / `delete` / `table_edit` 完全没写。

#### 3. 不对等参数不要写成二选一

正例（`markdown`）：

```
Default. Markdown body to write. Mutually exclusive with markdown_file. Do not write a local .md file first and then import it.
```

正例（`markdown_file`）：

```
Pass a local absolute path instead of markdown. Use only when importing or inserting an existing local .md file into SiYuan. After insertion, images and attachments in that file are uploaded to SiYuan, and relative references inside the file are rewritten to siyuan:// block links when possible. Do not create a temporary .md just to insert it; prefer the markdown parameter when editing.
```

误例（`markdown`）：

```
Markdown content to write. Mutually exclusive with markdown_file: provide exactly one.
```

误例（`markdown_file`）：

```
Absolute path to a local Markdown file whose content is imported as the document body. Mutually exclusive with markdown: provide exactly one. After writing, local standard Markdown image/file/directory links are uploaded via SiYuan insertLocalAssets and rewritten; unique heading anchors become siyuan:// block links. Direct markdown text is not scanned for local files.
```

解释：正例标明 markdown 是默认，markdown_file 只用于已有本地文件。误例写成对等二选一，且 markdown_file 能力更长，AI 会先写临时 md 再导入。

#### 4. 兼容旧名对 AI 隐藏

正例（报错）：

```
single_block_replace 的 markdown 必须只生成一个展示块，因为它会复用原块 ID 和块属性。当前 markdown 会被思源拆成多个块；请改用 default_block_replace。仅当目标是一个块、新 markdown 也只生成一个展示块、且 default_block_replace 会破坏该块引用时，才使用 single_block_replace。
```

误例（报错）：

```
single_block_replace 的 markdown 必须只生成一个展示块，因为它会复用原块 ID 和块属性。当前 markdown 会被思源拆成多个块；请改用 multi_block_replace。注意 multi_block_replace 会重建块，旧块 ID 和指向旧块的引用会失效。
```

解释：旧名 `multi_block_replace` 可以留在后端别名里，但不能出现在 schema、Skill、返回或报错中。误例会让新 AI 继续使用旧名。

#### 5. 搜索语法分层

正例（`siyuan_find` 总描述）：

```
Search visible SiYuan notes using SiYuan's native query syntax, regex, or SQL, then apply privacy filtering. In query mode, whitespace means AND; use explicit OR for exploratory searches or related concepts.
```

正例（`query` 参数）：

```
Search expression. In query mode, SiYuan treats whitespace as AND: 'GPU optical' = 'GPU AND optical'. Use explicit OR for related concepts: 'GPU OR optical OR NVLink'. Supports AND, OR, NOT, parentheses, quoted phrases, and prefix*. Hyphenated or other special-character terms are auto-quoted for FTS5, e.g. Scale-out becomes "Scale-out". Regex mode accepts Go RE2; SQL mode accepts a raw SQL statement.
```

正例（简单多词查询的返回末尾）：

```
提示：query 模式中，空格表示 AND；`GPU 光模块` 等价于 `GPU AND 光模块`。如需匹配任意一个词，请显式使用 OR，例如：`word1 OR word2`。
```

误例（`siyuan_find` 总描述）：

```
Search the SiYuan knowledge base through SiYuan search APIs, then apply privacy rules before returning results. Temporarily opens closed notebooks while searching and restores them afterwards. Supports 3 modes: query (space-separated terms use AND logic by default; also supports explicit AND/OR/NOT, phrases, and prefix*), regex, and sql (direct SQL, requires admin). Scope: headings (document titles + headings, default) or full (all block text). The search text parameter is query.
```

误例（空结果）：

```
# 搜索："GPU 光模块 NVLink Scale-out"（full，query）

未找到匹配的可见文档。
```

解释：正例总描述只留最容易用错的一句（空格是 AND）。对照示例和完整语法放在 `query` 参数；简单多词无论是否命中都在结果末尾提示。误例把 mode/scope/语法全塞进总描述，空结果又不解释本次查询的实际逻辑。

#### 6. 无效参数报错带完整示例

正例（`insert_assets` 收到 `asset_paths`）：

```
asset_paths 参数无效。action=insert_assets 的正确格式示例：{"action":"insert_assets","start_index":59,"start_id":"块ID","assets":[{"local_path":"C:\\path\\image.png","name":"可选显示名","title":"可选标题"}],"confirmed":true}。assets 数组项必须是对象，必须使用 local_path；不要使用 asset_paths 或 path。
```

误例：

```
Error: invalid parameter
```

解释：正例指出哪个参数无效，并给出可直接复制的完整调用。误例只有失败，AI 会接着猜 `path`、`asset_paths`。不要为猜错的名字加兼容别名。

### 改描述时的检查

1. 对照 `ARCHITECTURE.md` 该工具的完整 action/参数表，主路径是否都还在总描述或 `action` 说明里。
2. 默认路径是否在 enum 和说明的最前面。
3. 总描述、参数、Skill 是否重复同一段话。
4. 公开文案是否出现兼容旧名。
5. 同步 `tool_specs()`、测试断言、Skill、`ARCHITECTURE.md`。

## 修改隐私模型时必须验证

隐私相关改动包括：

- Privacy Rules 表格解析。
- `hidden/read_only/read_write` 权限。
- 系统笔记本保护。
- 搜索、读取、创建、编辑、文档管理的权限检查。
- 本地索引生成和过滤。

必须验证：

- 隐藏笔记本不会进入索引、列表、搜索、读取和写入。
- 隐藏文档及其子树不会进入索引、列表、搜索、读取和写入。
- `read_only` 可读、可 copy/export，但不可 create/edit/rename/move/delete。
- `read_write` 仍要求 `confirmed=true` 才能写。
- Privacy Rules 文档不能被 AI 读取、搜索、创建或编辑。
- 系统笔记本本身和除 Privacy Rules 外的系统文档进入正常索引，可以按普通权限 list/find/read/write。
- 其他笔记本中恰好名为 `隐私规则` / `Privacy Rules` 的普通文档不能被误挡。
- 搜索 `sql` 模式也必须经过隐私过滤。
- 写入后的自动 refresh 不得把 Privacy Rules 写入 AI 可见缓存。

系统笔记本不享受 Privacy Rules 特权；不要重新加入“系统笔记本不可隐藏”的特殊分支。

## 修改系统笔记本或启动包时必须验证

涉及插件系统笔记本维护、`load_agent_notebook()`、系统模板、`system_state.json` 或 `siyuan_start` 时，必须验证：

- 新安装会创建六篇固定文档。
- `AI 使用指南` / `AI Guide` 按原文档 ID 更名为 `用户个性化要求` / `User Preferences`，不删除重建。
- 旧正文由用户修改时完整保留；只有和已知历史默认模板完全一致时才替换成新空模板。
- 插件激活时合并 JSON 有效 ID、当前名称和历史名称匹配的全部文档；只有结果为空才创建。
- `system_state.json` 每类记录多个文档条目；失效 ID 由插件激活清理，Python MCP 不写状态。
- 两篇托管指南只有在当前正文仍等于上次记录的实际正文时才自动升级；用户修改后不得覆盖。
- 设置页重置指南必须保留文档 ID，并重写模板版本和导入后实际正文哈希。
- About 用户修改标题或正文后仍按 JSON 记录的原 ID 恢复标准标题和开发者模板，不得创建重复文档。
- Workspace Index 缺失时只创建一句占位内容，已有真实索引绝不覆盖。
- 多篇 User Preferences、Workspace Index 和 Privacy Rules 在 MCP 运行时合并使用；全部 Privacy Rules ID 都硬隔离。
- 非隐私系统文档全部失效时 `siyuan_start` warning 后继续；Privacy Rules 全部失效时失败关闭并提示禁用后重新启用插件。
- 29/30 天不提示过期，超过 30 天才在 MCP 返回中临时提示；不能写回思源文档或改变更新时间。
- 启动包顺序固定为运行状态、MCP Usage Guide、User Preferences、笔记本概览、Workspace Index；不再返回语言偏好和 About 入口。

## 修改读取模型时必须验证

涉及 `siyuan_read`、展示块、附件、数据库、超级块、列表、表格的改动，必须验证：

- `document` 路径命中缓存但 live hpath 已变化时，必须停止读取并要求 refresh 后用新路径重试，或改用 `document_id`。
- 普通阅读不显示块 ID。
- 引用阅读显示 `[index] id=... type=...`。
- 大纲始终返回，并标注标题块位置。
- `block_start`、`block_limit`、`token_budget` 不从块中间截断。
- 长文档有正确下一窗口提示。
- 普通 Markdown 表格在普通阅读中保留原始 Markdown。
- 普通 Markdown 表格在引用阅读中显示坐标视图。
- 数据库/属性视图只读渲染，不允许当普通表格编辑。
- 附件提取到 `ai_workspace/attachments/<doc-id>/assets/`。
- 返回正文中的 `assets/...` 链接改为本机绝对路径。
- 超级块普通阅读不重复渲染子块内容。
- 引用阅读下超级块自身占号，并继续给内部块编号。
- 插件块序号与 `include_block_ids=true` 的 `index/id/type` 一致；共享样例在 `tests/fixtures/display_block_index_cases.json`，Python 与 `siyuan-plugin/block-index.js` 都必须通过。

已知真实问题：

- SQL `sort` 不能可靠恢复块顺序，主路径必须继续使用 `getChildBlocks`。
- updateBlock 多块 Markdown 会截断，不要用它做多块替换。

## 修改写入模型时必须验证

涉及 `siyuan_create`、`siyuan_edit`、`siyuan_doc_manage` 的改动，必须验证：

- 写入必须要求 `confirmed=true`。
- `siyuan_edit` 使用路径定位时，必须先校验 live hpath；路径已变化时拒绝写入，且不得创建快照。
- 写入前必须创建思源快照。
- 快照失败必须拒绝写入。
- 数据仓库密钥未初始化时错误提示清晰。
- 隐藏文档不可写。
- 只读文档不可 edit/rename/move/delete。
- copy/export 对只读文档的行为符合工具契约。
- 写入后 pushMsg 失败不应影响主操作。
- 写入后需要刷新的工具必须带系统上下文刷新索引，且不得把 Privacy Rules 写入 AI 可见缓存。
- create/rename/move/copy/delete 后必须等待思源路径接口同步，返回路径同步状态。
- `siyuan_create(if_exists=reject)` 必须检查思源当前 live 文档列表，不能只依赖可能过期的 `docs.jsonl`。
- 任何会删除现有文档/块 ID 的操作必须在快照和写入前检查统一引用关系；标准块引用、可识别嵌入块和 `siyuan://` 块链接任一存在外部关系时默认拒绝且不得创建快照。
- 可见引用可以返回来源文档和块内容；隐藏或未知来源只能返回聚合数量，不得泄露路径、标题、块 ID 或内容。
- 冲突结果必须包含针对本次被引用块的判断说明：语义继续时保留 ID 并重规划编辑，语义撤销或替代且保留会误导引用时才考虑破坏；多块范围需要逐个判断。
- `reference_policy=break` 只能在用户已看到冲突报告并明确允许破坏引用后使用；同一删除集合内部的引用不应阻止操作。
- 返回信息必须让 AI 确认改了什么。
- `markdown_file` 与 `markdown` 互斥：需要 markdown 的 action 同时传入或都不传，必须在快照前报错；文件按 UTF-8 → GBK → GB18030 解码并统一换行，读取失败或内容为空在快照前拒绝且不写思源、不创建快照。
- `markdown_file` 写入成功后必须处理本次受影响块中的标准 Markdown 图片/链接：本地文件和目录走 `insertLocalAssets`，只替换链接目标；网络地址保持不变；唯一标题锚点转为 `siyuan://blocks/<ID>`；超过 20 MB、缺失或上传失败时保留原引用并报告，正文仍成功。直接传入 `markdown` 不得扫描调用端文件系统。

`siyuan_edit` 特别要求：

- 编辑前必须先引用阅读。
- index/id 不匹配时拒绝写入。
- `single_block_replace` 只允许一块变一块；仅当 `default_block_replace` 会破坏该块引用时使用。
- 可能产生多块的内容必须用 `default_block_replace`。
- `single_block_replace` 和 `table_edit` 必须保留块属性。
- `default_block_replace` 必须明确旧块 ID 会失效。
- `delete` / `default_block_replace` 必须检查目标块及其随同删除的子孙块 ID。
- `default_block_replace` 的返回摘要不得把本轮已删除的旧块列入“新内容”，即使思源写后读取短暂返回旧块。
- 公开 schema 不暴露旧名 `multi_block_replace`；后端别名仍可执行，但错误信息和工具描述不得再出现该旧名。
- 表格编辑必须使用 `row` 和 `column_index` 坐标。
- `insert_assets` 必须在快照和上传前完成路径存在性、普通文件/目录识别、同批基础文件名重名、锚点和大文件阈值检查。
- 图片判断必须直接使用思源前端扩展名清单并做大小写不敏感比较；未知图片格式按普通文件处理，不做 MIME 推断。
- `name` / `title` 缺省和显示语义必须分别测试；空 title 不得生成 `""`。
- 一个调用只允许一个 `start_index/start_id` 锚点，但允许同位置按顺序插入多个项目。
- 超过 20 MB 的普通文件在 `upload_large_files=false` 时整批暂停，不得创建快照或调用资源 API；文件夹不递归统计大小。
- 上传成功后的插入/读回失败必须测试文档块补偿。不能证明附件是本批新建且无人引用时不得自动删除，必须报告可能残留和手动快照恢复方式。

已知真实问题：

- `single_block_replace` 误传多块 Markdown 会导致内容丢失，所以当前代码已拒绝。
- `updateBlock` 会清空块样式属性，所以必须保留 IAL custom attrs。
- `siyuan_edit` 成功后当前不会自动刷新字数/块数索引。

## 修改主动引用检测时必须验证

涉及 `siyuan_operate(action=check_references)` 或底层 `list_block_references()` 的改动，必须验证：

- 文档检测集合来自 live `blocks` 表，包含文档 ID 和全部 `root_id` 属于该文档的正文块 ID；不得依赖 `siyuan_read` 的展示块 ID 推断。
- `refs` 与 `spans` 两路关系合并后按 `(目标 ID, 来源块 ID)` 去重；同一来源块对同一目标的不同引用形式仍只计一次。
- 标准块引用、简单查询嵌入块、块超链接和 Markdown 块链接均有覆盖。
- 当前文档按来源文档汇总；每篇最多展示 3 个唯一来源块，单块原始 Markdown 最多 2000 字符。
- 当前文档总数、子文档总数、隐藏来源总数不受 `limit` 影响；整数 `limit` 最小 1、无最大值，`"none"` 展示全部。
- 子文档递归统计；可见子文档只展示引用次数，隐藏子文档计入总数但不得暴露名称、路径、ID、数量或单篇次数。
- 隐藏来源计入总数，但只返回总引用次数，不得返回隐藏来源文档数、路径、标题、块 ID 或内容。
- 空值、`/`、笔记本名称/ID、正文块 ID 必须拒绝；文档路径/ID 解析继续复用公共定位器。
- action 只读，不要求 `confirmed`，不创建快照。
- 删除保护仍复用同一底层关系查询，并继续排除同一删除集合内部的关系；不得因主动查询格式改造而改变 `reference_policy` 上层逻辑。

## 修改文档管理时必须验证

涉及 `siyuan_doc_manage` 的改动，必须验证：

- create_notebook 需要 `notebook_name`、`confirmed=true` 和写前快照；同名笔记本必须在写入前拒绝，成功后刷新安全索引。
- create_notebook 不要求 `document` / `document_id`，不同时创建文档；delete 仍只删除文档子树，不得扩展为删除笔记本。
- `siyuan_create` 定位不到目标笔记本时，必须在快照和写入前拒绝，并明确提示先调用 create_notebook。
- rename/move/delete 需要 `read_write` 和 `confirmed=true`。
- 使用路径定位源文档时，必须先校验 live hpath；路径已变化时拒绝操作，且不得创建快照。
- copy 源文档可以是 `read_only`，但目标路径必须 `read_write`。
- delete 会影响整棵子树，必须验证子孙文档中存在 `read_only` 或 `hidden` 时拒绝操作，且错误信息不能泄露隐藏文档名称、数量或权限分布。
- delete 的反链检查必须覆盖整棵子树中的文档 ID 和正文块 ID。
- move 会移动整棵子树但不要求子孙全部可写；必须验证源文档祖先链和目标父路径都是 `read_write`。
- copy 必须使用 `target_path`，通过 `duplicateDoc` 复制源文档本身；不应退回 export + create 作为主路径。
- export 不创建快照、不写思源，只写 `ai_workspace/exports/`。
- delete 返回中提示可通过思源快照恢复。
- rename/move/copy/delete 后路径同步状态和索引状态正确。
- 连续操作同一文档时，路径和 document_id 解析一致。

已知真实问题：

- 思源文件树路径更新可能有短暂延迟。当前实现用 `getHPathByID` 短轮询后再刷新索引；若等待超时，仍应在返回中提示同步状态。

## MCP 工具契约清单

| 工具 | 是否写思源 | 是否需要 confirmed | 是否快照 | 主要权限 |
|---|---:|---:|---:|---|
| `siyuan_start` | 否 | 否 | 否 | 只读系统状态；Privacy Rules 缺失时失败关闭 |
| `siyuan_operate:refresh` | 否 | 否 | 否 | 只读系统状态并刷新安全索引 |
| `siyuan_operate:sync` | 否，触发思源内置同步 | 否 | 否 | 思源同步配置 |
| `siyuan_operate:check_references` | 否 | 否 | 否 | 目标可见；来源和子文档经隐私过滤 |
| `siyuan_list` | 否 | 否 | 否 | 只返回可见索引 |
| `siyuan_find` | 否 | 否 | 否 | 返回前隐私过滤 |
| `siyuan_read` | 否 | 否 | 否 | hidden 不可读 |
| `siyuan_create` | 是 | 是 | 是 | 目标路径 read_write |
| `siyuan_edit` | 是 | 是 | 是 | 文档 read_write |
| `siyuan_doc_manage:create_notebook` | 是 | 是 | 是 | 新笔记本名称 read_write |
| `siyuan_doc_manage:rename` | 是 | 是 | 是 | 文档 read_write |
| `siyuan_doc_manage:move` | 是 | 是 | 是 | 文档 read_write |
| `siyuan_doc_manage:delete` | 是 | 是 | 是 | 文档 read_write |
| `siyuan_doc_manage:copy` | 是 | 是 | 是 | 源可读，目标 read_write |
| `siyuan_doc_manage:export` | 否，只写本地导出 | 否 | 否 | 源可读 |

## 测试与验证清单

每次代码修改后至少运行：

```bash
python -m pytest tests -q
```

> **⚠️ 遥测污染红线**：测试代码中任何 `telemetry.json` 写入都必须带 `"telemetry_endpoint": "http://127.0.0.1:1"`，严禁使用默认端点。`_with_telemetry` 在 `telemetry: "upload"` 模式下会发起真实 HTTP POST，一旦漏配 endpoint 就会将测试数据打入生产 D1（`siyuanbridgetelemetry.zingerplayground.top`），污染遥测统计。新增测试时必须在 code review 中检查此项。

涉及 MCP 工具面、schema、Skill、安装配置或跨 Agent 行为时，还必须做 MCP 工具列表验证：

```text
JSON-RPC initialize
JSON-RPC tools/list
确认工具数量和名称符合预期
```

涉及 MCP 工具行为时，还必须直接启动当前源码，通过 JSON-RPC `tools/call` 调用受影响工具，并检查真实返回内容是否符合预期。只确认进程启动、测试通过或 schema 正确，不等于行为验证。

涉及发布或安装材料时运行：

```bash
python scripts/sync_siyuan_plugin_bridge.py
```

不再有 `pack_skill.py` 和 `pack_release.py` —— 项目已从 CC Switch 独立分发转为思源集市插件发布。

同步脚本只生成 `siyuan-plugin/bridge/`，该目录是开发/安装运行产物，不提交 Git。验证时必须确认：

- `siyuan-plugin/bridge/source_code/mcp_server.py` 存在。
- `siyuan-plugin/bridge/scripts/run_mcp.py` 存在。
- `siyuan-plugin/bridge/templates/system-docs/manifest.json` 和四个指南 Markdown 模板存在。
- `siyuan-plugin/bridge/config.local.json` 不会被同步脚本覆盖。

## 插件导入测试流程

测试思源工作空间中的插件目录只能作为”用户安装后的落盘结果”。不要直接修改测试工作空间里的插件代码。所有修复必须先改仓库工程文件，再把整个 `siyuan-plugin/` 重新导入测试工作空间。

测试工作空间路径不要硬编码。使用 `SIYUAN_TEST_WORKSPACE` 环境变量，或在命令中传 `--workspace`。脚本接受两种常见路径：

- 工作空间根目录，例如 `D:\siyuan2\workspace`
- 包含 `workspace/` 的父目录，例如 `D:\siyuan2`

当前家用测试机示例：

```bat
set SIYUAN_TEST_WORKSPACE=D:\siyuan2
```

导入脚本默认保留测试工作空间已有的 `bridge/config.local.json` 和 `bridge/telemetry.json`。模拟新用户首次安装时加 `--fresh`，不会保留这些本地配置。

> 遥测与反馈的 Worker API、D1 表结构、运维操作详见 [反馈与遥测后端参考](./feedback-telemetry-backend.md)。用户常见问题写在 README。

### 首次安装（模拟新用户）

模拟用户第一次从零安装插件的场景。预期：导入后没有 `config.local.json`，启用插件后自动创建。

```bat
python scripts\import_siyuan_plugin.py --workspace %SIYUAN_TEST_WORKSPACE% --fresh
```

普通开发导入（保留本地配置）：

```bat
python scripts\import_siyuan_plugin.py --workspace %SIYUAN_TEST_WORKSPACE%
```

验证清单：
- [x] `bridge/source_code/mcp_server.py` 存在
- [x] `bridge/scripts/run_mcp.py` 存在
- [x] `bridge/config.local.json` **不存在**
- [x] 思源 UI 启用插件后自动创建 `config.local.json`
- [x] 用户没有点开设置页、没有点击保存的情况下，外部 MCP 客户端能正常启动并调用工具

首次安装/启用插件的真实用户流程必须额外验证：删除测试插件目录中的 `bridge/config.local.json`，整体导入仓库 `siyuan-plugin/` 后，由用户在思源 UI 启用插件。插件启用后应自动创建 `bridge/config.local.json`，写入当前工作空间名称和 Token；在用户没有点开设置页、没有点击”保存配置”的情况下，外部 MCP 客户端也应能正常启动并调用工具。

跨设备 Token 合并改动还必须验证：准备一个已有其他设备 Token 的 `config.local.json`，启用插件后当前设备 Token 会追加到 profiles，原有 profile 的名称、Token 和顺序保持不变；重复启用不产生重复项；点击“刷新 JSON”也会合并并保存当前 Token。MCP 会话连接改动必须验证：未调用 `siyuan_start` 时普通工具明确要求先 start；一次成功 start 后多个工具不重复探测 profiles；连接或 401/403 鉴权失效后缓存被清空并要求重新 start。

### 第一层：测试代码（单元测试 + 真实 MCP 探针）

这是 MCP 工具改动的默认验证方式，不依赖 AI，也不要求重启已有 AI 会话。测试端作为普通 MCP 客户端直接启动当前源码，每次运行都会加载最新代码：

1. 向 stdio server 发送 `initialize`。
2. 调用 `tools/list`，检查工具数量、description 和 schema。
3. 调用受影响工具的 `tools/call`，使用能够区分正确与错误实现的真实输入。
4. 不只检查“调用成功”，还要断言返回结果的模式、数量、关键内容、路径、块 ID 或错误码。

例如搜索模式改动不能只检查 `mode.default == query`，还必须实际搜索包含空格的多词输入，确认返回模式为 query 且命中预期文档。此类验证能够发现单元测试中的 Fake Client 与思源真实 API 语义不一致。

写入工具只能操作明确的临时测试文档，并在测试后清理。读取、搜索场景优先使用测试工作空间中的固定夹具；如果使用用户工作空间，只执行只读调用，不把随时变化的结果数量硬编码为长期断言。

需要把固定回归场景自动化时，增加轻量 MCP probe 脚本：脚本负责启动 server、发送 JSON-RPC、解析结果和执行语义断言。第一层必须验证代码逻辑和“当前源码 + 真实思源 API + MCP 协议 + 返回结果”。

### 第二层：能力库开发版 MCP

把当前仓库源码临时注册为能力库中的开发版 MCP。每次修改后重新 `load`，再用 `use` 实际调用受影响工具并检查结果。能力库承担通用 MCP 客户端和进程加载职责，不依赖任何 AI 会话重载。

开发版注册必须明确指向当前仓库源码和测试配置，名称中应包含 `dev` 或 `test`。验证结束后按能力库维护规则禁用或移除临时注册，避免与用户版混淆。

### 第三层：子代理调用验证（大型修改时）

第三层不是每次修改的默认要求。第一层和第二层通过后，只有大型修改或用户明确要求时，才让子代理像正常用户一样独立调用受影响工具并评价返回结果。大型修改包括跨多个工具或核心数据流的改造、权限/隐私模型变更、复杂写入流程，以及发布前需要独立验收的高风险改动。普通的单工具缺陷修复、描述调整、报错文案和局部返回格式修改默认不执行第三层。

子代理的 MCP 路由顺序固定为：

1. 优先使用当前环境直接暴露、且已确认指向当前开发版源码和测试配置的内置 MCP。
2. 如果没有可用的开发版内置 MCP，读取能力库入口，通过能力库调用第二层临时注册的开发版 MCP。
3. 禁止使用用户版、生产版或无法确认代码来源的思源桥 MCP 做开发验证；无法确认时应报告阻塞，不得猜测。

写入类验证只能操作明确的临时测试文档；测试结束后清理。如果调用 `siyuan_start`，注意它会清理 `ai_workspace/` 中除 README 外的内容。

不同修改范围的最低验证：

| 修改范围 | 第一层：测试代码 | 第二层：能力库开发版 | 第三层：子代理调用 |
|---|---|---|---|
| 工具名称、schema、description | 单元测试 + `initialize/tools/list` | `load` 后检查开发版工具面 | 默认不执行 |
| 单个读取/搜索工具行为 | `tools/call` 使用真实输入并断言结果 | `use` 调用同一真实场景 | 默认不执行 |
| 局部报错文案或返回格式 | 单元测试 + 真实返回断言 | `use` 调用受影响场景 | 默认不执行 |
| 跨多个工具或核心数据流 | 覆盖完整调用链 | `use` 验证完整真实流程 | 必须独立验证 |
| 复杂写入流程 | 创建临时文档并完成写入、读回、清理 | `use` 完成同一真实流程 | 必须独立验证 |
| 隐私/权限模型 | 验证 hidden/read_only/read_write，不读取 Privacy Rules 正文 | 调用开发版检查拒绝或过滤结果 | 必须独立验证 |
| Skill/安装配置 | 检查配置、工具面和固定流程 | `load/use` 确认注册可用 | 仅大型改造或用户明确要求时执行 |

当前已经验证过的基线：

- `python -m pytest tests -q`：241 passed。
- 本地 JSON-RPC `tools/list`：9 个工具，server version 见 `source_code/__init__.py`。
- `python scripts/sync_siyuan_plugin_bridge.py` 可同步 bridge 到插件开发目录。

## 自动化验证计划

后续应新增统一验证入口，例如：

```bash
python scripts/verify.py
```

目标覆盖：

1. 单元测试。
2. MCP JSON-RPC `initialize + tools/list`。
3. 打包清单检查。
4. 能力库开发版 MCP 调用。
5. 子代理实际调用验证。

在该脚本落地前，不要声称已经有全自动验证；仍按上面的命令手动运行。

## 已知真实错误模式

这里只记录已从当前代码、测试或 devlog 中确认过的问题，不写泛泛的假想风险。

1. 旧文档仍含旧工具名和旧 exact text anchor 方案，AI 只读 devlog 开头会被误导。
2. `mcp_server.py` 体积过大，局部修改容易漏同步 `tool_specs()`、Skill 或测试。
3. Privacy Rules 文档硬隔离必须始终使用系统笔记本/文档 ID，不能退回全局同名拦截。
4. 写入后自动 refresh 必须保留 Privacy Rules 文档排除参数，避免污染本地索引缓存。
5. `siyuan_operate(action=refresh)` 不清理 `ai_workspace` 是当前设计；旧文档中“refresh 会清理 workspace”的表述需要迁移时删除。
6. `siyuan_doc_manage` rename/move 后路径索引可能延迟，当前通过短轮询和安全刷新处理。
7. `updateBlock` 多块 Markdown 会截断。
8. `updateBlock` 会清空块样式属性，必须恢复 IAL custom attrs。
9. 旧 `siyuan_create` 路径语义曾导致 AI 把完整路径误当内部路径。
10. Windows keep-alive 曾触发 `WinError 10054`，HTTP client 必须保留 `Connection: close`。
11. 插件和安装文档存在版本/链接漂移。
12. 思源插件第一版的 `bridge/` 目录由同步脚本生成，不是发布 ZIP；不要把旧 ZIP 流程误当成当前插件实现路径。
13. 测试空间里的思源插件目录不是源码，不得直接编辑。正确流程是修改仓库 `siyuan-plugin/`，再整体导入测试空间。
14. 思源只把 `siyuan-plugin/index.js` 当单文件执行。根入口改成 ESM，或 `require("./xxx.js")` 拆本地模块，都会让插件加载失败、设置齿轮消失。根入口只能 `require("siyuan")`，运行时逻辑必须内联；`block-index.js` 这类文件只给 Node 测试用。

## 版本号管理

版本号遵循单一事实源原则。Python 端统一从 `source_code/__init__.py` 的 `__version__` 读取，其他模块不得重复定义。

**版本号位置：**

| 位置 | 角色 | 管理方式 |
|------|------|----------|
| `source_code/__init__.py` | **唯一事实源** | 手动编辑 `__version__ = "x.y.z"` |
| `source_code/telemetry.py` | 引用 | `from source_code import __version__ as MCP_VERSION` |
| `source_code/mcp_server.py` | 引用 | `from . import __version__`（`serverInfo.version`） |
| `siyuan-plugin/plugin.json` | 独立维护 | 手动同步，与 `__version__` 保持一致 |

**升级版本号时需修改的文件：**

1. `source_code/__init__.py` — `__version__`
2. `siyuan-plugin/plugin.json` — `"version"`

`plugin.json` 是 JSON 文件，无法 import Python 模块，只能手动同步。两者必须保持一致。

**何时升哪一位：**

| 变更 | 升哪一位 | 例子 |
|---|---|---|
| 不兼容的用户面契约，且无法靠后端别名保住旧调用 | MAJOR | 删除工具、必填参数改语义且不兼容 |
| 对用户可见的新能力 | MINOR | 新增工具、新增写入能力、新的公开 mode |
| 缺陷修复、兼容别名、报错文案、参数名纠偏 | PATCH | `siyuan_find` 公开参数改为 `query` 并兼容旧名 |

默认从 PATCH 起算。只有明确新增了用户可感知能力才升 MINOR；不要把契约纠偏或小修复升成 MINOR。

**不需要改的地方：**

- 文档中的 API 示例数据（`mcp_ver` 字段）仅作示意，不需要逐版更新。
- `docs/devlog.md` 中的历史版本号是工程记录，不应修改。
- `tests/` 不硬编码版本号。

## Windows 命令与编码

在 Windows 上读取中文、搜索中文或运行复杂命令时，优先使用 CMD UTF-8 包装：

```bash
cmd /d /s /c "chcp 65001 >nul && <command>"
```

不要把终端乱码误判为文件损坏。

命令写法经验：

- Windows CMD 不支持 Bash heredoc，不能使用 `python - <<PY ... PY`。需要临时 Python 片段时，优先改成 `python -c "..."`，或用 PowerShell 循环直接完成读取、切片、格式化输出。
- 多层 `cmd /d /s /c`、`python -c`、正则和中文混在一起时，引号很容易被提前消费。优先拆成多条简单命令，或使用 PowerShell 原生命令处理局部文件读取。
- PowerShell 双引号字符串里，变量后紧跟冒号会被解析成作用域前缀。路径标签这类场景应写 `${p}:$line`，不要写 `$p:$line`。
- 只读局部文本时可以用 `Get-Content -Encoding UTF8`，不要用默认编码的 `Get-Content` 读取中文文件。

## Git 与工作区

工作区可能有用户未提交变更。修改前先查看：

```bash
git status --short
```

规则：

- 不要回滚用户变更。
- 不要使用 `git reset --hard` 或 `git checkout --`，除非用户明确要求。
- 与任务无关的未跟踪文件不要擅自删除。
- 生成文件、缓存、导出文件要注意 `.gitignore`。

## 构建与发布

### 导入测试：`scripts/import_siyuan_plugin.py`

把插件导入到本地思源数据目录，用于开发测试。自动执行 bridge 同步。

```bash
# 导入到思源工作空间（写完后在思源集市 → 已下载启用插件）
python scripts/import_siyuan_plugin.py --workspace "D:\SiYuan"

# 首次导入 / 清空重装（删除旧插件目录，不留旧配置）
python scripts/import_siyuan_plugin.py --workspace "D:\SiYuan" --fresh

# 直接用插件目录路径
python scripts/import_siyuan_plugin.py --plugin-dir "D:\SiYuan\data\plugins\siyuan-bridge"
```

数据流：`sync` 生成 `bridge/` → 把 `siyuan-plugin/` 整个复制到 `{workspace}/data/plugins/siyuan-bridge/`。

`--fresh` 会先删除目标目录再复制。不带 `--fresh` 时保留已有 `config.local.json` 和 `telemetry.json` 不动。

### 打包发布：`scripts/build_package.py`

生成思源集市上架的 `package.zip`。自动执行 bridge 同步。

```bash
python scripts/build_package.py
```

输出：`dist/package.zip`。

zip 包含：`plugin.json`、`icon.png`、`preview.png`、`index.js`、`index.css`、英文默认说明 `README.md`、中文说明 `README.zh-CN.md`、README 图片目录 `image/README/`、`bridge/`、`dist/`、`src/`。`bridge/` 由 sync 脚本生成，包含完整 Python 运行文件和系统文档模板；`knowledge_base/`、`ai_workspace/`、`stats/`、`config.local.json`、`telemetry.json` 等运行时数据必须从发布包排除。

根目录 `README.md` 是中文内容基准，根目录 `README.en-US.md` 是对应英文版。发布前将两者分别同步到 `siyuan-plugin/README.zh-CN.md` 和 `siyuan-plugin/README.md`。Package 内 README 的图片路径统一使用 `image/README/...`；构建脚本必须把仓库根目录同名图片目录映射到 Package 根目录，确保在线集市和安装后的本地详情页都能显示图片。

### 文件去向

```
source_code/          ─┐
plugins/siyuan-bridge/  ┤  手写源文件（你改的）
siyuan-plugin/*         ┤  (plugin.json, index.js, 图标等)
                        ─┘
         ↓  sync_siyuan_plugin_bridge.py
siyuan-plugin/bridge/  ←  自动生成（不提交 Git，不要手动改）
         ↓  import_siyuan_plugin.py               ↓  build_package.py
{workspace}/data/plugins/siyuan-bridge/       dist/package.zip
      (本地测试用)                               (集市发布用)
```

### 版本发布流程

集市发布走 GitHub Release，bazaar 每 1-3 小时自动拉取最新 release。

**首次发布（仅一次）**：

1. Fork `siyuan-note/bazaar`
2. 在 `plugins.txt` 加一行 `alone-tree/siyuan-bridge`
3. 提 PR 到 bazaar 主仓库
4. 合并后，集市索引自动更新

**后续更新**，每次只需：

```bash
# 1. 修改 siyuan-plugin/plugin.json 里的 version 号（遵循 semver）

# 2. 打包
python scripts/build_package.py

# 3. 提交、打 tag、推送
git add -A
git commit -m "release: vX.Y.Z — <简述>"
git push origin main
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z

# 4. 创建 GitHub Release，上传 dist/package.zip
gh release create vX.Y.Z dist/package.zip --title "vX.Y.Z" --notes "<更新说明>"
```

之后 bazaar 会在 1-3 小时内自动拉取新版本，**无需再提 PR**。用户重启思源可看到更新。

如果 Stage 工作流长时间未更新，检查 <https://github.com/siyuan-note/bazaar/actions/workflows/stage.yml> 的日志。
