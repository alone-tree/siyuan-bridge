# Ideas

这里放未承诺实施的粗略想法。每条尽量 1-5 行；一旦决定实施，迁移到 `ARCHITECTURE.md`、`DEVELOPMENT_GUIDE.md` 或具体 issue，不在这里维护路线图。

## 待评估

- 设计 `siyuan_import`：把外部 Markdown、PDF、图片等导入为思源文档或资源。
- 设计资产写入能力：上传图片/附件并插入到指定块附近。
- 多平台支持：验证 Mac/Linux 的 Python、路径、编码、MCP 注册和思源端口行为。

- 处理历史遗留代码：knowledge_base文件夹、思源插件导入后不git（因为都是同一个代码）、readme维护一份

## 遥测看板（公开页面）

- Worker 加 dashboard API，个人网站前端 JS fetch Worker 直连，不需要 API key
- 时间窗口未定（30天/14天/7天？）
- 想统计：活跃用户数、总调用次数、总体成功率、每日调用量曲线、每日成功率曲线、各工具调用及 action、各工具失败次数及场景
