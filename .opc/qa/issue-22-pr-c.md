# Issue #22 / PR-C 多任务前端验收

## 范围

本阶段把现有单任务页面接到 PR-B 已合并的 Summary、Batch、批量动作和多文件上传 API，形成可独立使用的“任务列表 + 任务详情”主从工作台。后端队列、分页和输出占用语义不得在本 PR 中重新实现。

PR-D 继续负责跨重启端到端恢复、100 Job 压力基线、最终安全审计和 Epic 全链路验收；PR-C 仍必须完成与本次 UI 变更相称的真实浏览器核心流程。

## 状态与网络契约

| 验收项 | 合同 |
|---|---|
| Summary 请求 | 整个页面只有一个 Job Summary 轮询器；禁止为列表中的每个 Job 建立计时器 |
| 首轮加载 | 使用签名 cursor 读完所有分页，再一次性替换列表 Store；不得只显示第一页 |
| 增量刷新 | 以上一轮 `server_time` 作为 `updated_after`，读完本轮所有分页后原子合并；cursor 失效时执行一次完整重载 |
| 任务详情 | 只读取 `selectedJobId` 的完整 `JobView`；仅选中的活动任务轮询详情 |
| 切换任务 | 切换选中项不影响后台 Job；过期详情响应不得覆盖新选中任务 |
| 删除任务 | 成功删除后立即从 Summary Store、多选集合和当前详情移除；其余逐项失败必须保留并展示 |
| 运行时密钥 | DeepSeek Key 只随创建并启动、运行或重试请求发送；不进入 Summary、Batch、localStorage、日志或持久化文件 |
| Mutation | busy/error 以 Job ID 或一次批量动作隔离；一个任务运行时仍能添加文件、选择其他任务和编辑其他非活动任务 |

## 多文件与批次

| 验收项 | 合同 |
|---|---|
| 桌面选择 | Tauri 选择器启用 `multiple: true`，一次返回多个本机路径 |
| 浏览器选择 | `<input type=file multiple>` 通过 `/api/uploads/bulk` 上传；逐文件成功和失败均可见 |
| 文件暂存 | 支持移除单项；规范化去重由后端预检裁决，前端不得静默丢弃服务端 issue |
| 预检 | 创建前展示每项名称、大小、输出路径、有效性及 issue；存在无效项时仍可提交其余有效项 |
| 创建 | 使用 Batch 公共配置快照；每个有效文件仍创建独立 Job；可选择“仅创建”或“创建并启动” |
| 部分失败 | `BatchCreateResult` 与 `BulkUploadResponse` 按项呈现，成功项进入列表，失败项保留可修正信息 |
| 输出冲突 | 预检返回的 `output_conflict`、`output_exists`、`invalid_output` 必须在创建前可见，不能靠覆盖按钮绕过 |

## 列表与交互

| 验收项 | 合同 |
|---|---|
| 主从布局 | 左侧按 Batch 分组的轻量任务列表，右侧复用当前 Pipeline、Attempt、Artifact 和日志详情 |
| 列表信息 | 文件名、状态、步骤、进度、队列位置、错误摘要、输出、耗时与 Token 摘要 |
| 查找筛选 | 文件名搜索、状态筛选、批次折叠/展开和可见项全选；100 项时不触发每行网络请求 |
| 批量动作 | 对选中 Job 支持启动、取消、重试失败和删除；逐项成功/失败结果可见 |
| 空状态 | 无任务、无筛选结果、详情加载中、详情已删除、后端断开均有明确中文状态 |
| 可访问性 | 列表选中、checkbox、进度、错误和加载状态具有可读标签；键盘可操作 |
| 响应式 | 桌面显示主从双栏，小屏改为列表在上、详情在下，不产生横向溢出 |

## 合并门禁

- 前端 lint、build 通过，且没有 TypeScript/ESLint 警告。
- Python 全量、Tooling、Ruff、Desktop fmt/check 不回归。
- 真实 Microsoft Edge 验证：多文件上传/预检/创建、Summary 分页与增量、任务切换、批量部分失败、一个任务活动时继续添加文件。
- 浏览器 console error/pageerror 为 0；预期 4xx 场景需通过页面错误状态验证，不得以未处理请求制造噪音。
- 独立 Reviewer 锁定精确 HEAD，给出 `VERDICT=PASS` 后才可合并。

## 实施证据（2026-07-16）

| 门禁 | 结果 |
|---|---|
| Python | `uv run --project apps/sidecar --extra asr --extra dev pytest -q`：227 passed；仅有依赖包的 Starlette 弃用提示 |
| 静态检查 | Sidecar Ruff、Tooling Ruff、Web ESLint、`git diff --check` 全部通过且无项目告警 |
| 构建 | Web TypeScript/Vite build 通过（1608 modules）；Desktop `cargo fmt --check` 与 `cargo check` 通过 |
| Edge 全流程 | Microsoft Edge 150：3 文件批量上传与预检、批次创建、详情切换、单项启动、批量部分失败、输出冲突、DeepSeek Key 不持久化均通过；console warning/error 与 pageerror 均为 0 |
| Summary 规模 | 浏览器真实 API 创建 205 个附加任务，总计 208 行；观察到 `limit=200` 的首/次页 cursor 请求及 `updated_after` 增量请求；搜索单项约 19 ms，列表未产生逐行详情请求 |
| 并发可用性 | 一个任务启动并进入活动/失败流程时，“添加文件”和其他任务选择仍可用；失败批量重试返回 1 成功、1 失败并保留失败选项 |
| 最终补丁回归 | Microsoft Edge 150.0.4078.65：配置变化后“仅创建”立即禁用，第二次预检成功后恢复；2 个任务显示命名批次且未误标“独立任务” |
| 慢网络竞态 | Edge 路由人为延迟单项删除与批量删除 800 ms；请求期间切换到其他任务后选择未被旧响应覆盖，批量请求期间新勾选项在结果返回后仍保留；console/pageerror 为 0 |
| 响应式 | 820 px 与 390 px 均切为单栏；最终 390 px 验证 `scrollWidth === clientWidth === 390` |
| 隔离与清理 | 浏览器测试使用独立临时数据目录和假模型夹具；服务停止后确认端口无监听，并删除全部临时数据 |

独立 Reviewer 的精确 HEAD 结论记录在 PR Review 中，避免在审查通过后修改本文件并使结论失效。
