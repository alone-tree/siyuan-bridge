# MCP 使用指南

这篇文档告诉 AI 如何组合使用思源桥。具体参数以 MCP 工具描述为准。

## 推荐流程

1. 新会话先调用 `siyuan_start`，读取启动包中的用户个性化要求、笔记本概览和工作空间索引。
2. 用 `siyuan_list` 浏览一层文档树，用 `siyuan_find` 搜索内容。
3. 用 `siyuan_read` 阅读文档；长文档通过 `block_start` 继续翻页。
4. 编辑前先用 `include_block_ids=true` 重新读取目标位置。
5. 只有用户明确要求写入时才传 `confirmed=true`。

## 编辑与块 ID

- 一块内容更新为一块内容时，优先使用 `single_block_replace`，保留原块 ID。
- 需要改变结构时，由 AI 根据用户意图选择 `multi_block_replace`，不要按位置机械猜测新旧块是否语义相同。
- `delete`、`multi_block_replace`、覆盖文档和删除文档树都会检查即将消失的块 ID 是否存在外部引用。
- 发现引用时默认拒绝。先向用户说明可见引用来源和隐藏来源数量；只有用户明确允许破坏引用后，才能用相同参数加 `reference_policy=break` 重试。
- 不要自动改写其他文档中的引用。

## 路径、权限与刷新

- 优先使用工具返回的完整文档路径；出现路径过期提示时，先调用 `siyuan_operate(action="refresh")`，或改用 `document_id`。
- `read_only` 内容可以读取、复制和导出，不能编辑、改名、移动或删除。
- Privacy Rules 文档由用户在思源中维护，AI 不得读取或修改。
- `siyuan_start` 会清理 AI 临时工作区；会话中途刷新索引不会清理。

## 工作空间索引

工作空间索引用于帮助新会话快速定位笔记。创建或更新方法见系统笔记本中的《工作空间索引创建指南》。
