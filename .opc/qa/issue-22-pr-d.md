# Issue #22 / PR-D 最终恢复、压力与安全验收

## 范围

本阶段不再扩展产品模型；它把 PR-A 至 PR-C 已合并的 Batch、持久 Scheduler、Summary API 与多任务 UI 放到真实进程边界和规模基线下验证，只修复验收发现的缺口，并完成 Epic 收口。

## 硬契约

| 验收项 | 合同 |
|---|---|
| 真实进程终止 | 使用独立 Uvicorn/Sidecar 进程，在一个 Translation Attempt 运行且三个任务排队时强制结束进程，不依赖对象重建模拟 |
| running 恢复 | 重启后原 running Job/Step/Attempt 为 `interrupted`，已经成功的 Media/Transcription Artifact 保留，不静默自动重跑 |
| queued 恢复 | 非密钥任务按持久 `queue_position` FIFO 恢复；同一 Job 不重复 claim，worker 并发上限仍生效 |
| 运行时密钥 | 排队的 DeepSeek Job 在重启后进入 `waiting_for_input`；旧 Key 不出现在响应、日志、Job/Batch JSON；补交新 Key 后可继续 |
| 100 Job 基线 | 真实 Batch API 一次创建 100 个独立 draft Job；分页读完恰好 100 个轻量 Summary，ID 无重无漏，搜索可定位单项 |
| 性能护栏 | 测试环境中 100 Job 创建低于 30 秒，三页 Summary 总读取低于 5 秒，单页响应小于 250 KB；阈值用于发现数量级回归，不作为产品营销指标 |
| 兼容与隔离 | 既有单文件 API、旧 Job、输出占用、删除语义、单 Job 失败隔离与批量逐项结果继续由 PR-A/B/C 全量回归覆盖 |
| 最终 UI | 系统 Edge 验证 100+ Job 搜索/筛选/切换、多文件创建、活动任务期间继续操作、批量部分失败、响应式与无障碍选中态 |

## 真实进程测试边界

仓库级测试启动真实 FastAPI lifespan、JobManager、JobStore、BatchStore 与 JobScheduler，仅把媒体/模型 Provider 替换为确定性测试 Pipeline，以避免 CI 下载模型或调用外部服务。进程终止、磁盘状态、重启加载、API 和调度行为均走生产实现。

## 2026-07-17 本地验收证据

| 验收 | 结果 |
|---|---|
| 真实进程恢复 | PASS；`test_issue22_process_acceptance.py` 启动独立 Sidecar，强制终止实际服务 PID，并确认父进程退出、监听端口释放；最终复跑 `1 passed in 8.16s` |
| running / queued | PASS；运行中的 Translation Job/Step/Attempt 重启后均为 `interrupted`，Media/Transcription Artifact 保留；两个普通 queued Job 在新进程按原 FIFO 顺序执行且无重复 claim |
| DeepSeek 运行时 Key | PASS；排队任务重启后为 `waiting_for_input`，无 Key 请求返回 400，补交替换 Key 后继续完成；两个哨兵 Key 均未出现在响应、Sidecar 日志或全部持久化 JSON |
| 100 Job API 基线 | PASS；一次 Batch 创建 100/100 个 draft Job，按 37 条分页读回恰好 100 个唯一 Summary，`stress-099` 搜索精确命中；创建、分页耗时和响应体大小均在硬契约阈值内 |
| Edge 恢复与规模 UI | PASS；Microsoft Edge `150.0.4078.65` 加载 106 个真实持久化任务，搜索命中 1 项、等待输入 1 项、已中断 2 项，任务切换后恰好一个 `aria-current=true`，运行时 Key 不在页面中 |
| Edge 响应式与运行质量 | PASS；390×844 下 document/body 的 `scrollWidth=clientWidth=390`，无横向溢出；console warning/error 与 pageerror 均为 0 |
| PR-C 交互回归继承 | [PR-C 验收](issue-22-pr-c.md) 已在同一系统 Edge 覆盖三文件多文件上传/预检/创建、活动任务期间继续添加、批量部分失败、208 行 Summary 分页与慢网络竞态；PR-D 不重复伪造第二套产品逻辑 |
| 安全源码审计 | PASS；分页签名密钥由进程内 `secrets.token_bytes(32)` 生成且不持久化，cursor 仅携带签名结果并校验篡改；桌面会话 Token 仅通过子进程环境和 WebView 初始化桥传递，Sidecar 输出被丢弃且不持久化 |
| 全量回归 | PASS；Python `228 passed`（13.64 秒，仅第三方 Starlette/httpx2 弃用提示）、Sidecar/Tooling Ruff、Web lint/build、Desktop fmt/check 与 `git diff --check` 均通过 |
| 清理 | PASS；Edge、Vite、两个 Sidecar 进程均已停止，端口 5175/8765 无监听，浏览器验收临时目录已删除 |

## 合并门禁

- 新增真实进程验收在 Windows 与 CI 环境稳定通过，且不残留监听端口或子进程。
- Sidecar/Tooling 全量测试与 Ruff 通过。
- Web lint/build、Desktop fmt/check 与 `git diff --check` 通过。
- 最终 Edge console warning/error/pageerror 为 0；所有临时目录与进程清理完成。
- 安全审计确认运行时 Key、会话 Token、分页签名材料未进入响应、日志或持久化文件。
- 独立只读 Reviewer 锁定精确 HEAD，给出 `VERDICT=PASS` 后才可合并并关闭 Issue #22。
