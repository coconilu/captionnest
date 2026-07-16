# Issue #22 / PR-B 批次与多任务 API 验收

## 范围

本阶段交付轻量 Job Summary 分页/筛选/增量查询、Batch 持久化服务、多源预检与创建、多文件上传、单任务取消、逐 Job 批量动作和 Batch 两种删除模式。前端主从布局、列表状态仓库和统一轮询由 PR-C 接入；因此无查询参数的旧 `GET /api/jobs` 暂时继续返回完整 `JobView[]`，保证已合并主分支始终可运行。

## 硬契约

| 契约 | 结果 |
|---|---|
| 10 个有效源创建 1 个 Batch + 10 个独立 Job | PASS |
| 单项非法不阻断其他有效源 | PASS；预检与创建结果均按输入 index 返回 |
| 同批规范化路径去重 | PASS |
| 统一目录同名 SRT 冲突 | PASS；创建前阻断，可用单项 export 覆盖改目录 |
| Summary 与 Detail 分离 | PASS；分页项不含 logs、steps、attempts |
| 分页与增量刷新 | PASS；不透明 keyset cursor + `updated_after` + `server_time` |
| 旧列表和单文件 API | PASS；无查询参数仍返回完整数组，`POST /api/jobs` 不变 |
| 批量动作部分失败隔离 | PASS；run/cancel/retry_failed/delete/update_config 逐 Job 返回 |
| Batch 中单 Job 失败隔离 | PASS；其余 Job 继续完成 |
| 多文件浏览器上传 | PASS；逐文件成功/错误，不整批回滚 |
| API Key 不落盘 | PASS；Batch 模板、Job JSON、响应和日志均不包含运行时密钥 |
| 删除语义 | PASS；默认仅解除分组；可选删除非运行 Job 与内部产物；导出 SRT 保留 |
| 活跃 Job 删除 Batch | PASS；无法删除的 Job 先解除 `batch_id`，不留下悬空引用 |

## 自动化证据

| 检查 | 结果 |
|---|---:|
| Batch/API 定向测试 | 5 passed |
| Sidecar 全量 | 192 passed，1 个既有 Starlette 弃用警告 |
| Tooling | 25 passed |
| 仓库 Python 总计 | 217 passed |
| Sidecar / Tooling Ruff | passed |
| Web lint/build | passed |
| Desktop fmt/check | passed |
| `git diff --check` | passed |

## 真实 Edge 回归

使用隔离端口、隔离 `.tmp` 数据目录、系统 Edge 与 Vite 代理验证：

| 场景 | 结果 |
|---|---|
| 旧单任务 UI 首次加载 | PASS；标题与工作台正常渲染 |
| 旧 `GET /api/jobs` | PASS；创建前后均返回数组，旧前端无需改动 |
| 浏览器内多源预检 | PASS；2 个源均有效 |
| 浏览器内 Batch 创建 | PASS；创建 1 个 Batch + 2 个 Job |
| Summary keyset 分页 | PASS；`limit=1` 两页 Job 不重复，总数为 2 |
| Summary 轻量负载 | PASS；分页项不含 `logs` 或 `steps` |
| 刷新后旧 UI 恢复任务 | PASS；显示批次中的源文件名 |
| console warning/error/pageerror | 0 |
| 临时进程与数据 | 已停止并删除 |

## 删除与输出边界

- 仅删除 Batch 时，所有仍存在的 Job 原子写回 `batch_id=null`。
- `delete_jobs=true` 只调用既有 Job 删除语义；queued/running Job 删除失败后会解除分组并保留，响应明确标记失败。
- Job Store 目录中的 `job.json` 与内部 Artifact 可删除；源视频旁或统一输出目录中的 SRT 不属于缓存，测试确认不会被删除。
- 已存在输出且 `overwrite_existing=false` 会在预检中报错；同一 Batch 内两个 Job 指向同一输出路径时，无论覆盖设置如何都不允许并发竞争。

## 后续 PR 接口

- PR-C 使用 `GET /api/jobs?limit=...&updated_after=...` 建立唯一 Summary 增量轮询，并仅拉取选中 Job 详情。
- PR-C 使用 `/api/batches/preflight`、`/api/batches`、`/api/uploads/bulk` 和 `/api/jobs/bulk-actions` 构建多文件/批量交互。
- PR-D 再执行 100 Job 性能、真实进程重启、运行时密钥恢复、完整真实浏览器验收与最终 Issue 勾选。

## 独立 Reviewer

实现提交后由固定只读 Reviewer 审查精确 HEAD；任何 P0–P3 finding 均由原 Developer 修复并重新送审，直至 `PASS`。
