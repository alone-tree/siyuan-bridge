# SiYuan Bridge — Agent Navigation

Python 项目，MCP + Skill 架构。产品界面是 MCP 工具和 Skill，CLI 仅供开发诊断。


## 文档定位

AGENTS.md文件是入口导航，不替代架构文档和开发指南。它告诉 AI：项目有哪些部分、先读什么、不同任务该接到哪些文档和代码位置。开发、测试和发布的详细规则放在 `docs/DEVELOPMENT_GUIDE.md`；完整真实架构和工具契约放在 `docs/ARCHITECTURE.md`。

代码修改后必须要在相应文档记录，但禁止将所有相关信息不加选择写入AGENTS.md文档。本文档务必保持精简。专门、详细的内容需要转移到对应文档，并提供精确到标题的指引。

## 必读流程

任何时候（查看代码、讨论方案、排障、改代码），先同步远端（`git fetch`，必要时 `git pull`），确保基于最新代码工作；拿旧代码讨论没有意义。

不要猜测代码可能的样子和实现方式，不管是本项目还是思源笔记的接口、思源插件注册规则都不要自己猜。如果有任何不清楚不确定的地方，直接阅读代码以及相应说明文档。必要时先搜索网络资料。

开始改代码前必须完整阅读：

1. `AGENTS.md`：入口导航、硬性安全规则、任务路由。
2. `docs/ARCHITECTURE.md`：当前真实架构、数据流、MCP 工具契约、已知债务。是理解最核心的说明文件。
3. `docs/DEVELOPMENT_GUIDE.md`：开发流程、同步清单、默认两层验证规则和大型修改的第三层验证。

不允许只读开头，不允许只 grep 局部，不允许跳过中间或后半段。没有完整阅读这三份文档，不准开始修改代码。

读完三份必读文档后，按任务类型追加阅读下面的对应入口。修改 MCP 工具描述、参数说明、enum 顺序或面向 AI 的报错文案前，还必须完整阅读 `docs/DEVELOPMENT_GUIDE.md` 的「编写 MCP 工具描述」；没读完不准改 `tool_specs()`。

## CodeGraph 使用要求

如果当前 Agent 环境提供 CodeGraph MCP 工具，分析代码结构、查找符号、追踪调用链、评估改动影响时必须优先使用 CodeGraph。调用subagent也需要明确告知优先使用codegraph。

CodeGraph 的退回顺序固定为：

1. 优先使用当前 Agent 环境直接暴露的 `codegraph_*` 工具。
2. 直接工具不可用、连接失败或未注册时，通过能力库入口加载当前设备对应的 CodeGraph MCP（家里电脑使用 `home-codegraph`）。
3. 只有直接 CodeGraph 和能力库 CodeGraph 都无法提供有效结果时，才退回 `rg` 和源码读取。

能力库 CodeGraph 调用前先读取 `D:\HermesSync\capability-library\capability-entry\SKILL.md`，不得凭印象猜测 MCP 名称或参数。使用 CodeGraph 能够显著加快代码结构探索并减少重复读取。


## 任务路由

| 任务类型 | 先读文档 | 再看代码/材料 |
|---|---|---|
| MCP 工具名称、schema、参数、返回格式、权限边界 | `docs/ARCHITECTURE.md` 的 “MCP 工具总览” 和各工具章节；`docs/DEVELOPMENT_GUIDE.md` 的 “修改工具面时必须同步” | `source_code/mcp_server.py` 的实现和 `tool_specs()`；`plugins/siyuan-bridge/skills/siyuan-bridge/SKILL.md`；`README.md`；相关测试 |
| MCP 工具描述、参数说明、enum 顺序、报错文案 | `docs/DEVELOPMENT_GUIDE.md` 的 “编写 MCP 工具描述”（必读全文）；`docs/ARCHITECTURE.md` 对应工具章节 | `tool_specs()`；Skill；相关测试 |
| `siyuan_create`、`siyuan_edit`、`siyuan_doc_manage` 写入行为 | `docs/ARCHITECTURE.md` 的 “写入模型”、对应工具章节；`docs/DEVELOPMENT_GUIDE.md` 的 “修改写入模型时必须验证” 和 “修改文档管理时必须验证” | `source_code/mcp_server.py`；`source_code/client.py`；`tests/test_mcp_server.py`；`tests/test_client.py` |
| 附件/图片/文件夹插入与 Markdown 引用附件导入 | `docs/ASSET_INSERTION_PLAN.md`；`docs/Markdown附件导入方案-2026-08-17.md`；实现前重新核对思源当前源码/API | `source_code/mcp_server.py`；`source_code/client.py`；相关测试 |
| 隐私、权限、系统笔记本、Privacy Rules | `docs/ARCHITECTURE.md` 的 “系统笔记本””隐私与权限模型”；`docs/DEVELOPMENT_GUIDE.md` 的 “修改隐私模型时必须验证” | `source_code/ignore.py`；`source_code/agent_notebook.py`；`source_code/indexer.py`；相关测试 |
| 索引、列表、搜索、读取、附件、块窗口 | `docs/ARCHITECTURE.md` 的 “索引模型””搜索模型””阅读模型”；`docs/DEVELOPMENT_GUIDE.md` 的 “修改读取模型时必须验证” | `source_code/indexer.py`；`source_code/mcp_server.py`；`source_code/client.py`；相关测试 |
| 思源底层 API 封装 | `docs/思源API.md`；`docs/ARCHITECTURE.md` 的 “底层 API 封装策略” | `source_code/client.py`；`tests/test_client.py` |
| Workspace Index 工作流 | `docs/ARCHITECTURE.md` 的 “siyuan-index-builder Skill”；`plugins/siyuan-bridge/skills/siyuan-index-builder/SKILL.md` | `plugins/siyuan-bridge/skills/siyuan-bridge/SKILL.md`；相关 MCP 工具实现 |
| 安装、打包、发布材料 | `docs/DEVELOPMENT_GUIDE.md` 的发布/验证部分 | `mcp_configs/`；`README.md`；`siyuan-plugin/README*.md` |
| 历史问题、排障、阶段性结论 | `docs/devlog.md`，优先读最新记录；不要把旧计划当当前事实 | 必要时同步回 `ARCHITECTURE.md` 或 `DEVELOPMENT_GUIDE.md` |
| 遥测、统计、用户体验改善 | `docs/ARCHITECTURE.md` 的”遥测数据流”；`docs/feedback-telemetry-backend.md` | `source_code/telemetry.py`；`source_code/mcp_server.py`；`worker/` |
| 查看用户反馈/未处理 issue | 无（直接运行脚本即可） | `scripts/check_feedback.py`：运行 `python scripts/check_feedback.py`，输出遥测反馈中 status != done 的条目 + GitHub open issues（仓库 alone-tree/siyuan-bridge）；遥测接口无需认证，GitHub 需 gh 已登录；若 403 需带 User-Agent（脚本已内置） |
| 插件前端 UI、消息通知、用户反馈 | `docs/FRONTEND.md`（根 `index.js` 只能 `require("siyuan")`，禁止 ESM 和 `require("./xxx.js")`） | `siyuan-plugin/index.js`；`siyuan-plugin/src/index.js`；`siyuan-plugin/index.css`；`siyuan-plugin/plugin.json` |
| 未决定的想法、待评估 idea | `docs/IDEAS.md` | 定案后再迁移到 `ARCHITECTURE.md` 或 `DEVELOPMENT_GUIDE.md` |
| 思源页面显示与 AI 一致的实时块序号 | `docs/ARCHITECTURE.md` 的“阅读模型”；`docs/FRONTEND.md` 的“块序号显示” | 运行时内联在 `siyuan-plugin/index.js`；Node 测试用 `siyuan-plugin/block-index.js`；`tests/fixtures/display_block_index_cases.json` |

涉及设计决策、工具契约、开发流程或排障结论时，不要只更新代码。必须同步更新对应文档。

## 项目地图

```text
source_code/         Python 适配层
  client.py          思源 HTTP API 封装
  indexer.py         扫描笔记本，生成 tree.md / docs.jsonl / notebooks.json
  mcp_server.py      MCP stdio server，9 个工具的 schema 和实现
  ignore.py          Privacy Rules Markdown 表格解析与过滤
  i18n.py            多语言名称、系统文档名、默认模板
  agent_notebook.py  系统笔记本只读加载与多文档合并
  config.py          配置加载和 profile 探测
  cli.py             开发诊断 CLI

plugins/
  siyuan-bridge/
    skills/          给外部 AI 的 Skill 指令副本。
    scripts/         run_mcp.py，MCP stdio 启动脚本

knowledge_base/      运行时缓存，Git 忽略，每次 refresh 可能覆盖
  tree.md            程序生成的客观文档树
  docs.jsonl         结构化文档元数据
  notebooks.json     可见笔记本索引
  privacy_rules.json Privacy Rules 解析缓存
  system_state.json  系统笔记本/文档 ID 与模板状态的本地注册表

思源笔记工作空间       用户启动的工作空间（用MCP看到的内容）
  思源桥/SiYuan Bridge   思源桥MCP系统笔记本，跟随思源工作空间切换
    MCP Usage Guide     工具搭配和关键注意事项；用户可改、可重置
    Workspace Index Guide  创建和更新导航索引的指南；用户可改、可重置
    User Preferences    用户写给 AI 的个性化要求，确保存在但不覆盖
    Workspace Index     AI 维护的语义导航索引，缺失时只创建占位内容
    About SiYuan Bridge 给人看的说明，按开发者模板覆盖
    Privacy Rules       人类维护的隐私规则，只有本文档对 AI 硬隔离
  其他笔记本
    其他文档
      其他子文档

ai_workspace/        AI 临时工作区，Git 忽略
dist/                构建产物
tests/               单元测试
docs/                架构、开发指南、前端、API、idea、devlog
  architecture-map.html 人类可读产品架构图，整体架构大改时同步更新
```

## 开发核心约束

- MCP-first：用户功能通过 MCP 工具暴露，CLI没有专门优化，只作早期开发诊断使用。
- 确认后可写：写入工具必须要求用户明确写入意图和 `confirmed=true`，写入前创建思源快照。
- 恢复要求：项目不提供 AI 自动回滚/checkout。写入后如需恢复，只能提示用户通过思源快照手动恢复；不要让 AI 调用高风险恢复接口。
- 不自动启动思源：连接失败只提示用户手动打开思源，不鼓励AI查找程序路径。在开发时，务必保留错误返回信息中的相关说明，不要省略“让用户启动”等关键表述。
- Privacy Rules 硬隔离：任何操作都需要在执行前经隐私规则过滤。源码写死隐藏Privacy Rules文档，AI 不可读取、搜索或编辑 Privacy Rules 文档。
- 系统笔记本六篇固定文档按各自生命周期维护；旧 AI Guide 按原 ID 更名为 User Preferences；身份和模板状态记录在本地 `system_state.json`。
- 关闭笔记本透明打开/关闭：索引、搜索和写入前可临时打开关闭的笔记本，完成后必须恢复。
- 工作区可能有用户改动：不要回滚、删除或重置非本任务改动。
- README 单一来源：根目录 `README.md`（中文）是唯一客观来源，`README.en-US.md` 根据它翻译；`siyuan-plugin/README*.md` 只能由 `scripts/build_package.py` 自动同步，禁止手动维护。
- 插件根入口 `siyuan-plugin/index.js` 必须是单文件 CommonJS：只能 `require("siyuan")`，禁止 `import` 和 `require("./xxx.js")`。违者插件加载失败、设置齿轮消失。细节见 `docs/FRONTEND.md`。

## 协作规则

除非用户明确要求修复、实现、改代码、跑测试、提交或执行其他具体操作，否则只查看相关文档和代码，做只读分析说明，不要擅自行动。

回复必须精简、明确、直接。不要绕圈子，不要输出无关铺垫。尽可能节省输出token，只提供影响决策的必要信息。

## Windows 命令

Windows 上读取中文、输出中文、处理复杂引号或避免 PowerShell 编码问题时，优先使用 CMD UTF-8 包装：

```bat
cmd /d /s /c "chcp 65001 >nul && <command>"
```

示例：

```bat
cmd /d /s /c "chcp 65001 >nul && type AGENTS.md"
cmd /d /s /c "chcp 65001 >nul && rg -n ""关键词"" AGENTS.md"
cmd /d /s /c "chcp 65001 >nul && python -m pytest tests -q"
```

不要使用默认 `Get-Content AGENTS.md` 读取中文，不要把终端乱码误判为文件损坏。

复杂命令优先拆成简单命令，避免多层 shell 引号。Windows CMD 不支持 Bash heredoc，不能用 `python - <<PY`。PowerShell 字符串中变量后紧跟冒号时要写成 `${p}:...`，不要写 `$p:...`。

## 常用入口

```bash
# 导入到本地思源测试
python scripts/import_siyuan_plugin.py --workspace <思源工作空间路径>
python scripts/import_siyuan_plugin.py --workspace <路径> --fresh  # 首次安装/清空重装

# 打包集市发布 zip
python scripts/build_package.py

# 诊断
python -m source_code doctor
python -m source_code notebooks

# 索引
python -m source_code refresh
python -m source_code start

# 搜索/阅读
python -m source_code find <keyword>
python -m source_code tree
python -m source_code read <doc-id>

# 测试
python -m pytest tests -q
```

## 分层验证

涉及 MCP 工具行为时，默认完成两层验证：第一层运行测试代码，包括单元测试和直接启动当前源码的真实 MCP 探针；第二层在能力库临时注册并调用开发版 MCP。只有大型修改或用户明确要求时，才增加第三层子代理独立调用验证。大型修改包括跨多个工具或核心数据流的改造、权限/隐私模型变更、复杂写入流程和发布前需要独立验收的高风险改动。禁止使用用户版思源桥做开发验证。只验证代码能运行或只看 `tools/list` 不算行为验证。详细要求见 `docs/DEVELOPMENT_GUIDE.md` 的“分层验证流程”。

## 更新到本地思源

### 用户版（生产环境，不覆盖配置）

```powershell
# 1. 关思源
# 2. 杀残留 MCP 进程
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'run_mcp' } | Stop-Process -Force

# 3. 导入（不加 --fresh，保留 config.local.json 和 telemetry.json）
python scripts/import_siyuan_plugin.py --workspace D:/siyuan2

# 4. 开思源
```

### 测试版（干净重装）

```powershell
python scripts/import_siyuan_plugin.py --workspace D:/Siyuan2test --fresh
```

> 测试版 workspace 路径、Hermes MCP 注册更新、文件锁处理等详细流程见 `D:\HermesSync\capability-library\skills\siyuan-bridge-ops\SKILL.md`。

## 发布 Release

### 1. Bump 版本号

两个文件：`source_code/__init__.py`（`__version__`）和 `siyuan-plugin/plugin.json`（`"version"`）。

semver：`MAJOR.MINOR.PATCH`。功能新增升 MINOR（y+1，PATCH 归零）；缺陷修复、兼容别名、报错文案、参数名纠偏升 PATCH（z+1）。不要把小补丁升成 MINOR。详细规则见 `docs/DEVELOPMENT_GUIDE.md` 的“版本号管理”。

### 2. 构建 + 提交 + 打 tag

```powershell
# 构建 package.zip（自动调用 sync_siyuan_plugin_bridge）
python scripts/build_package.py

# 提交版本号变更
git add source_code/__init__.py siyuan-plugin/plugin.json
git commit -m "bump version to X.Y.Z"

# 打 tag
git tag -a vX.Y.Z -m "vX.Y.Z: <简短描述>"

# 推送（含 tag）
git push origin main --follow-tags
```

### 3. 创建 GitHub Release

```powershell
gh release create vX.Y.Z --title "vX.Y.Z — <标题>" --notes "<Markdown 内容>" dist/package.zip
```

> 推送前确保 `docs/devlog.md` 已记录所有改动。
