# 插件前端

本文件只记录思源插件前端细节。前端与 Python Bridge、Worker 后端的架构关系写在 `ARCHITECTURE.md`。

## 当前入口

- 思源实际加载：`siyuan-plugin/index.js`。
- 源码参考：`siyuan-plugin/src/index.js`。
- 样式：`siyuan-plugin/index.css`。
- 插件清单：`siyuan-plugin/plugin.json`。

根 `index.js` 必须保持 CommonJS：`require("siyuan")`、`module.exports`。不要改成 `import` / `export default`，否则思源桌面端会报 `Cannot use import statement outside a module`，插件加载失败，设置齿轮消失。

思源通过 `/api/petal/loadPetals` 只下发 `index.js` 字符串，再用自定义 `require` 执行。根入口只能 `require("siyuan")`，不能 `require("./xxx.js")`；本地拆出去的模块在运行时不存在，同样会导致插件加载失败、设置齿轮消失。编号算法的 Node 测试文件 `block-index.js` 不能被 `index.js` 引用，必须把运行时代码内联进根入口。

## UI 结构

插件设置入口打开 Home Dialog，包含：

- 通知：GET Worker `/api/notifications`。
- MCP 配置：展示 Python 命令、Bridge 路径、MCP JSON 和 profiles。
- 反馈：POST Worker `/api/feedback`。
- 用户体验改进：通过 `Plugin.loadData/saveData` 读写插件数据区 `telemetry.json` 中的 `telemetry`。
- 读取图片：「读文档时默认返回图片」开关，写入插件数据区 `config.local.json` 的 `read_inline_images`，安装默认关闭，保存后提示重新连接 MCP 生效。
- 系统笔记本维护：插件每次激活时按文档名发现、创建和维护四篇系统文档，并在发现重复文档时弹窗提示用户手动检查删除。

## 插件数据

以下文件通过 `Plugin.loadData/saveData` 保存在工作空间 `data/storage/petal/siyuan-bridge/`，不会随集市更新替换插件程序目录而丢失：

- `config.local.json`：profiles、Token、内部语言配置、`read_inline_images` 图片内联开关。Token 不写入 MCP JSON。
- `telemetry.json`：匿名 ID、遥测开关、本地副本开关、端点、代理。
- `block-index.json`：块序号显示开关。

`system_state.json` 已弃用（v1.11.2 取消系统文档登记表）：插件和 Python Bridge 都不再写入或读取；残留文件静默保留在插件数据区，不主动清理。

首次启用插件时，前端从思源 `/api/system/getConf` 读取当前工作空间 Token，并在缺失配置时自动创建插件数据区 `config.local.json`。旧版首次升级时，如果插件数据区对应文件不存在，前端会从 `bridge/` 或 `bridge/knowledge_base/` 只复制迁移旧文件；已有持久数据优先，旧文件不删除。`telemetry.json` 没有 `anonymous_id` 时，会先读取 petal 或旧插件目录里的 `stats/telemetry_id`，没有旧值才新建。

同一次插件激活还会维护系统笔记本：先按 `lsNotebooks` 名称匹配（当前名 + 历史名）定位系统笔记本，缺失时创建；再在该笔记本内按文档名（当前名 + 历史名，大小写不敏感）维护四篇系统文档。Privacy Rules 最先维护，之后其余三篇各自独立维护；单篇文档维护失败不影响其他文档。全程不写任何状态文件；更新已启用插件、启动思源或重新启用插件都会触发；打开设置页不会触发维护。

发现同类型多篇文档时全部继续使用，不自动删除或合并正文。插件在布局就绪后弹出一次 Dialog，列出重复类型和数量，提示用户手动删除；插件按文档名继续合并使用全部同名文档。

Home Dialog 保持紧凑：外层 `padding: 16px`、卡片间距 `gap: 12px`、卡片 `padding: 14px 16px`、标题 `margin-bottom: 8px`，卡片内最后一个元素不留底部外边距。Dialog 不写死 `height`，由内容决定高度；内容过长时由 `.siyuan-bridge-home` 的 `max-height` + `overflow-y` 滚动。通知区最多显示两条通知的高度（`max-height: 100px`），超出部分在同区域内纵向滚动，不能撑高 Dialog；通知少时不保留空位，单条通知最多两行（`max-height: 48px`）。

工作空间绝对路径不写入配置文件。每次打开 MCP 配置页或点击“刷新 JSON”时，前端调用 `/api/system/getWorkspaces`，选择 `closed=false` 的当前工作空间，重新生成本机插件目录、Bridge 目录、`run_mcp.py` 绝对路径和 MCP JSON。这样插件整体同步到另一台电脑后，设置页仍会显示另一台电脑自己的路径。

插件只维护 `bridge/templates/system-docs/` 中的四类系统文档模板（About、Privacy Rules、User Preferences、Workspace Index 占位）。MCP 使用指南和索引创建指南自 v1.11.2 起属于代码资产（`templates/guides/`，随集市更新覆盖），不进系统笔记本、不提供设置页重置；插件不再读取 manifest，也不做指南哈希校验。

## 块序号显示

设置页开关“显示思源桥块序号”和命令面板“显示/隐藏思源桥块序号”控制同一状态，默认开启，保存在插件 `saveData("block-index.json")`，不写入笔记。插件启用且序号开启时，以及用户每次打开开关时，都会提示：正文左侧数字由思源桥插件显示，与 AI 所说的「第 N 块」一致。

编号规则与 `siyuan_read(include_block_ids=true)` 相同。Node 测试实现是 `siyuan-plugin/block-index.js`，插件运行时必须内联在 `index.js`。顺序只来自 `/api/block/getChildBlocks`。角标画在编辑器覆盖层上，不进入 `contenteditable`，不修改块 DOM 或块属性。序号贴在思源块标按钮同一套位置：短块垂直居中，多行块贴顶部；超级块和列表用内部第一行作为锚点，避免容器 padding 把 9/10/11 错位。嵌套超级块从左到右为外层→内层→叶子。

打开或切换文档、以及 `ws-main` 中的 insert/delete/move/append 会重算完整 `ID → 序号`。动态加载只把已有映射补到新出现的块上。失败时清空角标并 `showMessage("块序号暂不可用")`。关闭开关、销毁编辑器或卸载插件时移除覆盖层和监听器。

修改展示块规则时，必须同时更新 `tests/fixtures/display_block_index_cases.json`、Python `build_display_blocks()` 和 `siyuan-plugin/block-index.js`。

## 验证

不要直接改测试工作空间里的插件代码。先改仓库 `siyuan-plugin/`，再导入：

```bat
python scripts\import_siyuan_plugin.py --workspace %SIYUAN_TEST_WORKSPACE%
```

模拟首次安装：

```bat
python scripts\import_siyuan_plugin.py --workspace %SIYUAN_TEST_WORKSPACE% --fresh
```

最低检查：

- 根 `index.js` 只有 `require("siyuan")`，没有 `import`，也没有 `require("./xxx.js")`。
- 插件能启用，设置齿轮存在。
- 首次启用能在 `data/storage/petal/siyuan-bridge/` 生成 `config.local.json`。
- 旧版文件存在且插件数据区为空时只复制迁移；目标已有数据时不覆盖旧值；`system_state.json` 不再迁移。
- 首次启用能按文档名创建四篇系统文档；About 模板失败时 Privacy Rules 仍已就位。
- 已有当前名称和历史名称匹配的全部文档都会继续使用；只要还有一篇就不新建。
- 重复文档全部继续使用并弹窗；用户手动删除后，剩余文档继续按名称正常工作，`siyuan_start` 正常读取。
- MCP JSON 不包含 Token。
- MCP JSON 中的 `run_mcp.py` 是当前设备、当前工作空间的绝对路径；切换电脑后重新打开配置页应自动变化。
- Home Dialog 的通知、反馈、遥测、块序号、读取图片开关不会阻塞 MCP 配置；不再出现系统指南区域。
- 旧版本留下的 `MCP 使用指南` / `工作空间索引创建指南` 文档按普通文档处理，插件激活不报错、不覆盖、不改名。
