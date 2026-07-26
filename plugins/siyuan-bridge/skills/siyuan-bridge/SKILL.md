---
name: siyuan-bridge
description: Use when the user wants to read, search, or write their private SiYuan notes (思源笔记). Triggers on mentions of 思源, 知识库, or when the agent needs personal context from the user's notes.
---

# 思源桥

通过 MCP 工具访问用户的思源笔记。不要扫描本地文件系统寻找笔记内容。

## 启动流程

1. 调用 `siyuan_start` —— 刷新安全索引，确保系统笔记本及其六篇文档就绪，返回启动包（运行状态、MCP 使用指南、用户个性化要求、笔记本概览、工作空间索引）。
2. 阅读返回的启动包。
3. 遵循启动包中的 MCP 使用指南和用户个性化要求。
4. **以工作空间索引为导航主入口。** 快速导航表将用户意图映射到笔记本，笔记本详情是 AI 扫描后浓缩的结构摘要和判断——信任它来定位相关笔记本。
5. 若启动包提示用户尚未创建或长期未更新工作空间索引，先询问用户是否需要创建或更新。具体方法见系统笔记本中的《工作空间索引创建指南》。
6. 用 `siyuan_list`（带 `notebook_id`）查看单个笔记本的文档树，含有效权限、字数和更新时间。
7. 用 `siyuan_read` 按需深读。始终按展示块窗口返回，不截断字符。始终返回大纲（标题→block 位置映射）。长文档用 `block_start=N` 翻页继续阅读，用 `block_limit` 和 `token_budget` 控制窗口大小。需要精确跨文档块引用或编辑定位时，开启 `include_block_ids=true`（引用阅读模式）。
8. 系统笔记本 `思源桥` / `SiYuan Bridge` 和普通笔记本一样可读写；只有 Privacy Rules 文档本身被硬隔离。

若 MCP 工具不可用，告知用户思源桥 MCP 未注册或不可达。不要回退到扫描文件。

## 隐私规则

- 隐私规则完全由用户在思源中维护，通过系统笔记本中的 `隐私规则` / `Privacy Rules` 文档的 Markdown 表格控制。
- `siyuan_privacy` 和 `siyuan_temporary_allow` 工具已被移除。AI 无法修改隐私规则。
- AI 不能读取、搜索、总结或编辑隐私规则文档。该文档被系统硬编码隔离。
- 如需临时开放隐藏或只读内容，用户在思源中手动将权限改为 读写 即可；交流完毕后再改回原权限。
- 隐私规则文档修改后，告诉 AI"刷新一下"或在下次 `siyuan_start` / `siyuan_operate(action="refresh")` 时自动生效。
- 如果隐私规则解析失败，AI 会收到可定位的错误信息（表格名、行号、字段名和错误类型），但不会包含具体隐藏的笔记本名称、文档 ID 或标题。

## Tool Use Hints

- `siyuan_start` —— 始终最先调用。返回运行状态、MCP Usage Guide、User Preferences、笔记本概览和 Workspace Index。
- `siyuan_find` —— 搜索知识库，通过思源 API 实时搜索后经隐私规则过滤返回结果。
- `siyuan_read` —— 只读取可见文档；隐藏文档和隐私规则文档即使已知 ID 也不会被读取。
- `siyuan_read` / `siyuan_edit` / `siyuan_doc_manage` 使用路径定位时会校验思源当前真实路径。若提示路径已过期，先调用 `siyuan_operate(action="refresh")`，再用当前真实路径重试；或改用 `document_id`。
- `siyuan_list` —— 无参数或 `path="/"` 时列出可见笔记本；其他路径列出直接子文档及有效权限。`read_write` 可写，`read_only` 只能读取、复制或导出；隐私规则文档和隐藏内容不会出现在列表中。
- `siyuan_create`、`siyuan_edit` —— 写入工具。始终 `confirmed=true`。写入前自动创建思源工作空间快照。默认不写入，除非用户明确要求。
- `siyuan_create` 优先传完整可读路径 `path=/Notebook/Folder/Doc`；只有笔记本名称重名或使用内部路径时才补充 `notebook_id`。目标已存在时默认 `if_exists=reject`，可显式用 `overwrite` 清空块后重写并保留文档 ID，或用 `create_new` 新增同名文档。
- `siyuan_create` 成功后会等待思源路径同步并自动刷新安全索引；正常情况下可直接使用返回路径继续读取或管理。
- 编辑已有文档前，先用 `siyuan_read(include_block_ids=true)` 进行引用阅读，并把返回的块序号和块 ID 作为 `siyuan_edit` 定位参数。
- 不必为了块 ID 本身改变编辑方案；只有被其他块引用的 ID 消失时才会影响用户。
- `delete`、`multi_block_replace`、`siyuan_create(if_exists=overwrite)` 和整棵文档树删除都会检查即将消失的 ID 是否被外部引用，默认 `reference_policy=reject`。若返回引用冲突，按错误结果附带的语义判断说明重新规划编辑；只有用户明确允许破坏本次报告的引用后，才能用相同参数加 `reference_policy=break` 重试。不得自行使用 `break`。
- `siyuan_doc_manage` —— 管理文档树。`rename/move/delete/copy` 需要用户明确要求和 `confirmed=true`；`rename/move/delete` 还需要可写权限。`delete` 会删除整棵子树，子孙文档也必须全部可写；`move` 保留子树权限，但如果源文档来自只读/隐藏祖先路径则拒绝移动。`copy` 必须传完整 `target_path`，只复制源文档本身，不复制子文档。`export` 只导出可读文档到 `ai_workspace/exports/`。
- `siyuan_doc_manage` 的 rename/move/copy/delete 成功后会等待路径同步并自动刷新安全索引；如果返回提示路径同步超时，临时改用 `document_id` 或显式调用 `siyuan_operate(action="refresh")`。
- 编辑普通 Markdown 表格时，使用引用阅读返回的网格坐标：`row=0` 是表头，`row>=1` 是数据行，`column_index` 从 1 开始。表格不是数据库，不要把表头、字段或多维表语义混在一起。
- `siyuan_operate` —— `action=refresh` 会话中途刷新安全索引，不清理 `ai_workspace/`；`action=sync` 调用思源内置默认同步，相当于点击思源同步按钮，并返回当前同步状态。同步默认等待 10 秒；慢同步可设置 `timeout_seconds` 到 5-120 秒，该参数只改变 MCP 等待时间，不改变思源同步行为。超时说明同步尚未在等待窗口内完成，不等同于思源未启动。只有 `siyuan_start` 会在新会话启动时清理 workspace。
- `siyuan_bridge_feedback` —— 通过对话提交对思源桥 MCP 的反馈。type 为 bug/feature/idea，title 和 description 必填，contact 可选。不修改思源内容，不需要 confirmed=true，即使思源未启动也可使用（只要配置了遥测端点）。
- 系统笔记本 `思源桥` / `SiYuan Bridge` 及其六篇固定文档会被自动创建和维护。MCP 使用指南和工作空间索引创建指南允许用户修改，并可在插件设置中重置。

## Safety Rules

- 不要修改思源笔记中的隐私规则文档。
- 不要尝试读取、搜索或总结隐私规则文档。
- 不要读取 `config.local.json`、`siyuan.ignore.local.json`、`siyuan.allow.local.json`，除非用户明确要求。
- 不要暴露被隐藏的笔记本或文档名称，除非用户明确要求。
- 不要全量扫描 `knowledge_base/tree.md` —— 使用 `siyuan_start` 返回的概览表。
- 长文档不要一次性塞进回复 —— 使用 `siyuan_read` 的 `block_start` 参数分段翻页读取。
- 派生分析和草稿放在 `ai_workspace/`。
- 注意区分系统文档的用途：User Preferences 是用户写给 AI 的要求，Workspace Index 是导航，About 和两篇指南是工具说明。

## Cross-References

- **导航索引创建**：触发 `siyuan-index-builder` skill。
- **项目开发**：见仓库 `AGENTS.md`（面向维护者）。
