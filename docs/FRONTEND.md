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
- 用户体验改进：读写 `bridge/telemetry.json` 中的 `telemetry`。
- 系统指南：读取 `bridge/knowledge_base/system_state.json`，显示两篇托管指南是否被用户修改，并提供保留文档 ID 的重置按钮。
- 系统笔记本维护：插件每次激活时发现、创建和维护六类系统文档，并在发现重复文档时弹窗提示用户手动检查删除。

## 配置文件

- `bridge/config.local.json`：profiles、Token、语言。Token 不写入 MCP JSON。
- `bridge/telemetry.json`：匿名 ID、遥测开关、端点、代理。
- `bridge/knowledge_base/system_state.json`：插件维护的工作空间级注册表。schema v2 为每类文档保存多个 ID 和各自模板状态；Python Bridge 只读，不写此文件。

首次启用插件时，前端从思源 `/api/system/getConf` 读取当前工作空间 Token，并在缺失配置时自动创建 `config.local.json`。

同一次插件激活还会维护系统笔记本：校验 JSON ID，合并当前名称和历史名称匹配的全部文档，清理失效 ID，并且只在某类完全不存在时创建。更新已启用插件、启动思源或重新启用插件都会触发；打开设置页不会触发维护。

通知区固定显示两条通知卡片的高度；第三条及后续通知保留在同一区域内，通过纵向滚动查看，不能继续撑高 Home Dialog。单条通知最多显示两行。

发现同类型多篇文档时全部登记并继续使用，不自动删除或合并正文。插件在布局就绪后弹出一次 Dialog，列出重复类型和数量，提示用户手动删除；下次激活清理被删除的 ID。

工作空间绝对路径不写入配置文件。每次打开 MCP 配置页或点击“刷新 JSON”时，前端调用 `/api/system/getWorkspaces`，选择 `closed=false` 的当前工作空间，重新生成本机插件目录、Bridge 目录、`run_mcp.py` 绝对路径和 MCP JSON。这样插件整体同步到另一台电脑后，设置页仍会显示另一台电脑自己的路径。

两篇托管指南的模板来自 `bridge/templates/system-docs/`，与 Python Bridge 使用同一份源文件和 manifest。重置流程：

1. 用户确认。
2. 实时调用 `lsNotebooks` 找到当前系统笔记本，从以该笔记本 ID 分区的 JSON 记录中取得该类型的全部有效文档 ID。
3. 调用 `updateBlock` 覆盖所有已登记文档正文，不删除或重建文档。
4. 立即调用 `exportMdContent` 读回思源实际 Markdown。
5. 重新计算实际正文 SHA-256，写回 `system_state.json`。

当前工作空间没有对应 JSON 记录时禁止重置，提示用户重新启用插件；不得通过 `siyuan_start` 修复，也不得回退到另一个 profile 的最近 ID。

## 块序号显示

设置页开关“显示思源桥块序号”和命令面板“显示/隐藏思源桥块序号”控制同一状态，默认开启，保存在插件 `saveData("block-index.json")`，不写入笔记。插件启用且序号开启时，以及用户每次打开开关时，都会提示：正文左侧数字由思源桥插件显示，与 AI 所说的「第 N 块」一致。

编号规则与 `siyuan_read(include_block_ids=true)` 相同。Node 测试实现是 `siyuan-plugin/block-index.js`，插件运行时必须内联在 `index.js`。顺序只来自 `/api/block/getChildBlocks`。角标画在编辑器覆盖层上，不进入 `contenteditable`，不修改块 DOM 或块属性。序号贴在思源块标按钮同一套位置：短块垂直居中，多行块贴顶部；嵌套超级块从左到右为外层→内层→叶子，与 hover 时 gutter 按钮列一致。

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
- 首次启用能生成 `bridge/config.local.json`。
- 首次启用能创建六类系统文档，并把每类文档记录为数组。
- 已有 JSON ID、当前名称和历史名称匹配结果会取并集；只要还有一篇就不新建。
- 重复文档会全部登记并弹窗；用户删除后不重载插件，`siyuan_start` 仍能跳过失效 ID 正常读取剩余文档。
- MCP JSON 不包含 Token。
- MCP JSON 中的 `run_mcp.py` 是当前设备、当前工作空间的绝对路径；切换电脑后重新打开配置页应自动变化。
- Home Dialog 的通知、反馈、遥测开关不会阻塞 MCP 配置。
- 系统指南状态能区分系统默认和用户已修改；重置后正文恢复且文档 ID 不变。
