# Issue #22 / PR-A 后端基础验收

## 范围

本阶段只交付 Batch/Job Summary 数据契约、旧任务兼容、Job Repository、Batch Store 和持久化 Job Scheduler。分页/筛选与批量 API、多文件 Batch 创建、前端主从工作台及最终浏览器验收分别留给后续 PR，避免在一个不可审查的大提交中同时改变全部层级。

## 硬契约

| 契约 | 结果 |
|---|---|
| 一个文件仍对应一个 Job | PASS；Batch 仅保存 Job ID 与公共配置模板 |
| 禁止无限 `create_task` | PASS；只有一个 dispatcher 和不超过 `worker_concurrency` 的运行 Task，排队项为持久记录/Future |
| CUDA/auto ASR 默认并发 1 | PASS；独立 Semaphore，CPU、翻译 Provider、IO 分池 |
| 原子 claim / 禁止重复执行 | PASS；pending/running ID 在同一锁内迁移，活跃 Job 重复运行被拒绝 |
| queued 顺序持久化 | PASS；每次入队、claim、取消均重排并原子保存 `queue_position` |
| 重启恢复 queued | PASS；按持久位置恢复 FIFO，不为每个 Job 建轮询器或 Task |
| running 重启语义 | PASS；Job、Step、Attempt 标记 `interrupted`，成功上游 Artifact 保留 |
| DeepSeek 运行时密钥 | PASS；只存在于内存 ScheduleEntry；重启后进入 `waiting_for_input`，JSON 中无密钥 |
| 取消隔离 | PASS；queued 可直接移除，running 只取消对应 Task，不影响其他 Job |
| 阻塞线程取消 | PASS；ASR/IO 的底层线程真正结束前不释放 Scheduler 资源槽，取消后进度回调不再写入 |
| 旧任务兼容 | PASS；缺失新字段时 `batch_id=null`，队列状态由旧 `status` 推导 |
| 轻量 Summary | PASS；`to_summary()` 直接聚合数值，不深拷贝日志、Steps 或 Attempts |

## 自动化证据

| 检查 | 结果 |
|---|---:|
| Scheduler/Batch 定向测试 | 7 passed |
| Sidecar 全量 | 187 passed，1 个 Starlette 弃用警告 |
| 仓库 Python 总计 | 212 passed |
| Sidecar Ruff | passed |
| Tooling | 25 passed |
| Tooling Ruff | passed |
| Web lint/build | passed |
| Desktop fmt/check | passed |
| `git diff --check` | passed |

定向测试覆盖 3 个 Job 同时被 worker claim 时 CUDA 活跃数仍为 1、FIFO 启动顺序、重复 claim 拒绝、queued/running 独立取消、取消 ASR 时底层线程结束前不释放 CUDA 槽且不再回写进度、异常退出后 queued 顺序恢复、running Attempt 中断、Summary 当前步骤推导、DeepSeek 密钥丢失后等待输入，以及 Batch 配置无密钥持久化。

## 真实浏览器回归

使用独立端口、独立 `.tmp` 数据目录和 1 秒本地 MP4，通过系统 Edge/Playwright 验证：

| 场景 | 结果 |
|---|---|
| Web 连接隔离 Sidecar | PASS |
| 浏览器内创建旧版单文件 Job | PASS；响应包含新队列字段且 `batch_id=null` |
| 刷新后恢复任务详情 | PASS |
| 从媒体步骤进入 Scheduler | PASS；媒体 Artifact 与 Attempt 正常持久化 |
| 后续 ASR 模型缺失 | 按预期进入 failed；已完成媒体步骤保留，错误与重试入口正确显示 |
| 页面 console warning/error | 0 |
| 测试进程与临时数据 | 均已停止并清理 |

本机隔离目录未下载 `small` 模型，因此没有执行真实 ASR；该失败是环境前置条件门禁，不是 Scheduler 或页面运行时错误。

## 后续 PR 接口

- PR-B 使用 `BatchRecord`、`BatchStore`、`JobSummaryView` 和 `JobManager.list_summaries()` 增加分页/筛选、批量操作、多文件预检与 Batch API。
- PR-C 使用 `queue_status`、`queue_position`、`current_step` 和 Summary 增量接口重构任务列表/详情。
- PR-D 完成真实进程重启、100 Job、输出冲突、运行时密钥对话框与真实浏览器验收。

## 独立 Reviewer

实现提交后由固定只读 Reviewer 审查精确 HEAD；任何 P0–P3 finding 均由原 Developer 修复并重新送审，直至 `PASS`。
