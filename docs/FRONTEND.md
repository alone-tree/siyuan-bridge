# 插件前端

本文件只记录思源插件前端细节。前端与 Python Bridge、Worker 后端的架构关系写在 `ARCHITECTURE.md`。

## 当前入口

- 思源实际加载：`siyuan-plugin/index.js`。
- 源码参考：`siyuan-plugin/src/index.js`。
- 样式：`siyuan-plugin/index.css`。
- 插件清单：`siyuan-plugin/plugin.json`。

根 `index.js` 必须保持 CommonJS：`require("siyuan")`、`module.exports`。不要改成 `import` / `export default`，否则思源桌面端会报 `Cannot use import statement outside a module`，插件加载失败，设置齿轮消失。

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

发现同类型多篇文档时全部登记并继续使用，不自动删除或合并正文。插件在布局就绪后弹出一次 Dialog，列出重复类型和数量，提示用户手动删除；下次激活清理被删除的 ID。

工作空间绝对路径不写入配置文件。每次打开 MCP 配置页或点击“刷新 JSON”时，前端调用 `/api/system/getWorkspaces`，选择 `closed=false` 的当前工作空间，重新生成本机插件目录、Bridge 目录、`run_mcp.py` 绝对路径和 MCP JSON。这样插件整体同步到另一台电脑后，设置页仍会显示另一台电脑自己的路径。

两篇托管指南的模板来自 `bridge/templates/system-docs/`，与 Python Bridge 使用同一份源文件和 manifest。重置流程：

1. 用户确认。
2. 实时调用 `lsNotebooks` 找到当前系统笔记本，从以该笔记本 ID 分区的 JSON 记录中取得该类型的全部有效文档 ID。
3. 调用 `updateBlock` 覆盖所有已登记文档正文，不删除或重建文档。
4. 立即调用 `exportMdContent` 读回思源实际 Markdown。
5. 重新计算实际正文 SHA-256，写回 `system_state.json`。

当前工作空间没有对应 JSON 记录时禁止重置，提示用户重新启用插件；不得通过 `siyuan_start` 修复，也不得回退到另一个 profile 的最近 ID。

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

- 插件能启用，设置齿轮存在。
- 首次启用能生成 `bridge/config.local.json`。
- 首次启用能创建六类系统文档，并把每类文档记录为数组。
- 已有 JSON ID、当前名称和历史名称匹配结果会取并集；只要还有一篇就不新建。
- 重复文档会全部登记并弹窗；用户删除后不重载插件，`siyuan_start` 仍能跳过失效 ID 正常读取剩余文档。
- MCP JSON 不包含 Token。
- MCP JSON 中的 `run_mcp.py` 是当前设备、当前工作空间的绝对路径；切换电脑后重新打开配置页应自动变化。
- Home Dialog 的通知、反馈、遥测开关不会阻塞 MCP 配置。
- 系统指南状态能区分系统默认和用户已修改；重置后正文恢复且文档 ID 不变。
