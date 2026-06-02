# Compare Q&A: Bridge vs Sisyphus

本文记录 `siyuan-agent-bridge` 与 `siyuan-plugins-mcp-sisyphus` 的架构比较、当前讨论结论，以及后续取舍问题。写于比较分支 `codex/compare-sisyphus-edit-design`。

## 背景

当前触发点是：Bridge 的 `siyuan_edit_document` 使用 `old_text -> new_text` 文本锚点编辑，实际使用中经常出现 AI 操作失败，提示原文匹配不上。我们因此对比 Sisyphus 的实现，讨论是重做 Bridge、给 Sisyphus 提 PR，还是保留 Bridge 并借鉴 Sisyphus 的工程架构。

## Q1: Bridge 和 Sisyphus 的产品定位有什么不同？

**Bridge 是 AI 工作流产品。**

Bridge 试图把思源笔记压缩成少量 AI 容易理解的工具：启动包、列表、搜索、阅读、创建、编辑。核心目标是减少 AI 的选择负担，把思源作为私有知识库数据层接入 Claude Code / Codex / Cursor 等 agent。

**Sisyphus 是思源 MCP 基础设施。**

Sisyphus 暴露 11 个聚合工具，覆盖大量思源 API action：`fs`、`document`、`block`、`search`、`av`、`file`、`tag`、`system`、`flashcard` 等。它更像一个通用操作面，既服务 MCP，也服务 CLI。

初步判断：两者不是简单替代关系。Bridge 的独特价值在“AI 少犯错的产品收束”，Sisyphus 的强项在“完整 API 覆盖、插件化、测试和工程化”。

## Q2: Sisyphus 的 edit/write 方案比 Bridge 好在哪里？

Sisyphus 有两条编辑路线：

1. `fs.replace`：导出整篇 Markdown，执行 exact old/new 替换，然后删除文档子块并重新 append 整篇正文。
2. `block.update/insert/append/delete`：直接用块 ID 做底层块操作。

`fs.replace` 对 AI 来说更像文件编辑，心智模型简单；`block.*` 对思源来说更自然，因为思源的真实对象是块。

但 `fs.replace` 的代价也明显：它会重建文档正文，容易影响块 ID、块属性、复杂结构、局部样式或数据库/属性视图等非纯 Markdown 语义。它是好用的文件式接口，但不一定适合作为 Bridge 的默认安全编辑模型。

## Q3: Bridge 当前 edit 的真正问题是什么？

Bridge 当前问题不是“Python 写得不好”，而是定位模型不够贴合思源。

`old_text` 被当作主要定位器，服务端在 `blocks.markdown` 里做精确子串匹配。只要 AI 从阅读视图复制的文本和块数据库中的 Markdown 有细小差异，就会失败，例如：

- 空格、换行、前后缀差异；
- 列表、表格、超级块的导出形态差异；
- 普通阅读 Markdown 与块级 `markdown` 字段不完全一致；
- AI 没有开启 `include_block_ids=true`，只拿文本片段编辑；
- 短文本在多个块中同时出现，造成歧义。

更合适的方向是把 `block_id` 从“可选消歧义参数”升级为“主要定位参数”，`old_text` 退到安全校验或并发防护角色。

## Q4: Python 程序可以包装成思源插件吗？

**可以做，但不自然。**

思源插件本身运行在前端/Electron 插件环境里，Sisyphus 的做法是：插件包内包含前端设置面板和打包后的 `mcp-server.cjs`，由插件通过 Node `child_process` 启动 MCP server 子进程。

Python 也可以走类似路线：

- 插件前端仍然用 TypeScript/Svelte/JavaScript；
- 插件启动时用 `child_process` 调用 `python` 或打包后的 Python 可执行文件；
- MCP server 仍由 Python 负责；
- 插件 UI 只负责配置、启动、停止、状态展示和日志。

问题在分发和兼容性：

- 用户机器不一定有合适的 Python；
- 如果内置 Python runtime，插件体积和多平台打包复杂度会上升；
- Windows/macOS/Linux 都要处理可执行文件、权限、路径和杀进程；
- Docker/移动端/只读环境更难支持；
- 思源插件市场更习惯 JS/TS 插件包，纯 Python 作为插件主体不太自然。

所以结论是：Python 可以被插件“托管启动”，但 Bridge 不应该为了插件化而急着全量迁移成 TypeScript。

## Q5: 是否需要迁移编程语言？

当前不建议全量迁移。

理由：

- Bridge 的核心问题是产品接口和编辑语义，不是 Python 本身。
- Python 代码已经完成了隐私过滤、索引、启动包、系统笔记本、快照写入等产品机制。
- 全量迁移到 TypeScript 会带来长时间重写风险，期间真正痛点 edit 仍要重新设计。
- Sisyphus 已经证明 TypeScript 插件架构可行，但也没有天然解决“AI 高层安全编辑”问题。

更稳妥的路线是：

1. Bridge 主体继续用 Python。
2. 先重构 Bridge 的 edit/read/write 产品语义。
3. 借鉴 Sisyphus 的测试结构、工具 action schema、帮助资源、权限模型和插件壳设计。
4. 如果未来需要插件化，做一个薄 TypeScript 插件壳来启动 Python MCP server，而不是立即重写全部业务逻辑。

## Q6: 哪些 Sisyphus 架构值得 Bridge 借鉴？

优先借鉴这些：

- **测试体系**：unit / integration / smoke / AI interface test 分层清楚，尤其是把 AI 误调用和提示词缺口当成测试对象。
- **工具注册与 action schema**：每个工具 action 有 schema、hint、help 文档，方便控制 MCP 上下文。
- **帮助资源 progressive disclosure**：常规描述简短，详细说明放 `siyuan://help/...` 或等价资源。
- **插件设置面板**：配置、启停、日志、权限状态都可以在思源内看见。
- **权限模型**：按笔记本设置 `r/rw/rwd/none` 比 Bridge 现在的隐私规则更像通用权限层。
- **UI refresh**：写入后调用思源 UI 刷新接口，减少用户以为写入失败的错觉。
- **CLI 作为诊断入口**：Sisyphus 的 CLI 更系统，Bridge 当前 CLI 可以继续保持开发诊断定位，但测试和排障体验可以借鉴。

不建议直接照搬这些：

- 上百个 action 的完整 API 覆盖。Bridge 的产品价值恰恰来自收束。
- `fs.replace` 的整文档重建作为默认编辑方案。它适合文件式体验，但不够保护思源块语义。
- 过早把 Bridge 改成完整插件项目。先把核心编辑体验修顺更重要。

## Q7: 是否应该给 Sisyphus 提 PR？

可以，但 PR 应该小而聚焦。

适合给 Sisyphus 的 PR 方向：

- 新增一个更 AI 友好的高层编辑 action，例如 `block.patch` 或 `fs.patch`；
- 输入以 `block_id` 为主，可选 `expected_old` 做并发/安全校验；
- 支持 `replace_block`、`replace_text_in_block`、`insert_after`、`append_to_doc` 这类明确操作；
- 错误返回当前块摘要和下一步建议，减少 AI 重试成本；
- 不改变 Sisyphus 现有大量底层 action。

不适合的 PR：

- 把 Bridge 的启动包、隐私索引、系统笔记本全部塞进 Sisyphus；
- 大幅改变 Sisyphus 的工具模型；
- 要求 Sisyphus 收缩工具数量。

## Q8: Bridge 下一步应该怎么做？

建议路线：

1. 保留 Python 主体。
2. 先做 edit 语义重构：块 ID 优先，文本校验辅助。
3. 调整 Skill：写入前默认用引用阅读模式获取块 ID。
4. 增加针对列表、表格、代码块、超级块、重复文本、短文本歧义的编辑测试。
5. 借鉴 Sisyphus 的 action/help/test 结构，把 Bridge 的 MCP 工具描述和错误提示做得更像产品说明，而不是只像 API 参数说明。
6. 之后再考虑插件壳：TypeScript 插件 UI + Python MCP 子进程。

## 当前结论

不要因为 Sisyphus 更完整就放弃 Bridge。Bridge 的方向仍然成立：它不是“另一个思源 API wrapper”，而是面向 AI agent 的私有知识库产品。

更合理的判断是：

- **Bridge 继续做产品层和 AI 工作流层。**
- **Sisyphus 作为工程架构、测试、插件化和底层操作面的重要参考。**
- **语言暂不迁移；插件化可以用 TypeScript 壳托管 Python。**
- **真正优先级最高的是重构 edit，而不是重写项目。**

## Q9: 为什么本次 Bridge 测试顺利，但日常使用仍可能很难用？

2026-06-02 在 Codex 中接入 `siyuan-bridge-dev` 后，实测 Bridge 的创建、阅读、搜索、编辑都比较顺利。测试流程包括：

- `siyuan_start` 成功返回启动包、Workspace Index、AI Guide 和笔记本概览；
- `siyuan_find_documents` 搜索 Hermes 文章，直接返回文档 ID、命中块 ID 和片段；
- `siyuan_read_document(include_block_ids=true)` 返回大纲、块序号、块 ID 注释和正文；
- `siyuan_create_document` 在测试笔记本中新建文档，自动创建快照、刷新索引，并去掉重复 H1；
- `siyuan_edit_document` 成功完成普通替换、同块内重复文本替换、锚点后插入、末尾追加、`block_id + old_text` 精确消歧义；
- 不存在文本和短文本多块匹配都快速失败，并给出较清楚的错误提示。

但这次顺利不代表日常问题不存在。本次测试有几个“理想条件”：

1. **使用者刻意遵循了 Bridge 的最佳路径**：先 `siyuan_start`，再搜索，随后用 `include_block_ids=true` 引用阅读，最后编辑刚刚读到的完整块文本。
2. **测试文本较简单**：主要是普通标题和段落，没有列表项、表格、超级块、数据库、复杂代码块、图片附件等复杂块结构。
3. **编辑粒度很小**：一次只改一个块或追加一个短段落，符合 AI Guide 中“一次只编辑一个块，只插入一个段落”的规则。
4. **old_text 几乎完全来自刚刚读取的当前块**：没有让 AI 自己概括、改写、截断或跨块拼接 old_text。
5. **没有跨会话或并发修改**：读取后马上编辑，用户没有在思源 UI 中同时改动文档。
6. **测试者理解工具机制**：知道短文本会歧义、知道 block_id 可消歧义、知道 insert 模式需要 `new_text.startswith(old_text)`。

日常使用中，AI 很可能不会自然做到这些：

- 只普通阅读，不开启 `include_block_ids=true`；
- 从搜索片段、摘要、旧上下文或用户转述里拼 old_text，而不是从当前阅读结果复制；
- 一次想改多段、跨块替换、插入长内容；
- old_text 太短，命中多个块；
- 复制的文本包含或缺失 Markdown 标记、列表前缀、空行、HTML 注释；
- 面对列表、表格、数据库、超级块时，阅读视图和 `blocks.markdown` 的内部形态不一致；
- 失败后没有自动重读当前文档窗口，而是反复用同一段旧文本重试。

因此目前更准确的结论是：

**Bridge 的 edit 在“按最佳流程、小粒度、块内文本、刚读即改”的条件下表现良好；难用主要出现在 AI 偏离最佳流程、编辑粒度变大、或块结构复杂时。**

这说明问题不一定是底层 API 完全不可用，而是产品接口还没有足够强地把 AI 拉回正确路径。后续需要专门做“反最佳实践”复现实验，例如：

- 不开 `include_block_ids` 直接编辑；
- 使用搜索 snippet 作为 old_text；
- 对列表项、表格、代码块、超级块做 edit；
- 跨块删除或替换；
- 用很短的 old_text 编辑重复文本；
- 读完后人为改动文档，再让 AI 用旧 old_text 编辑；
- 让模型在不知道机制的情况下自由完成一项写作修改任务。

这些测试比当前手动控制的 happy path 更能解释用户日常感受到的难用。
