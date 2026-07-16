# Issue #17 VAD 动态切片 QA

- 验收时间：2026-07-16T22:15:58+08:00
- 分支：`codex/issue-17-vad-windows`
- 基线 HEAD：`15c8ff648bf6b006eed12b8c0d600c321e552235`
- 实现差异指纹（写入本报告前）：`46ae6edd51a0122d6ec5d3dc9d00b388688d7775`
- 当前结论：`PRELIMINARY PASS`；提交后仍须 Reviewer 对精确 HEAD 复核并等待 CI 全绿

## 验收范围

| 检查项 | 结果 | 独立证据 |
|---|---|---|
| 自然停顿吸附 | PASS | 130 秒夹具在 62–64 秒静音中点生成 63 秒边界 |
| 核心窗口约束 | PASS | 动态成功时所有核心均为 45–75 秒，首尾相接并完整覆盖时间轴 |
| 上下文 | PASS | 始终为核心前后 2 秒，并裁剪到音频范围 |
| 确定性回退 | PASS | 无合适静音、VAD 异常、短音频均回到原固定窗口 |
| 固定模式开关 | PASS | `dynamic_chunking=false` 不导入或调用边界 VAD |
| 旧任务兼容 | PASS | 缺少新字段的历史任务迁移为固定模式；新任务仍默认动态模式 |
| 归属、去重与输出模式 | PASS | 保持中点归属、现有边界去重；两种输出模式参数化通过 |
| `vad_filter` | PASS | 每个窗口仍将用户配置原样传给 Faster-Whisper |
| 延迟导入与隐私 | PASS | VAD 仅在转写路径导入；诊断不保存字幕、媒体路径或原始异常 |
| 有界性 | PASS | Reviewer 随机检查 10,000 组，无空洞、零长度、越界或无界窗口 |

## 真实媒体 A/B

实验使用仓库现有的真实媒体前 120 秒音频夹具；报告只保留不可逆 SHA-256 与数值，不记录媒体路径或字幕正文。

| 指标 | 固定 60 秒 | VAD 动态 | 结论 |
|---|---:|---:|---|
| 配置指纹 | `12b246cedcf48b97b9489af5ddfbcb6e02a64be88eaf425fce08472649f62404` | `efa69ab810093de066756329b8fc5bcfd4b0d68676a570ede72bb376edce4ef4` | 仅 `dynamic_chunking` 不同 |
| 耗时 | 8,657 ms | 7,785 ms | 动态本次少 872 ms；单次顺序运行仅作方向性证据 |
| 字幕数 | 19 | 19 | 一致 |
| 非空白字符数 | 133 | 133 | 文本覆盖一致 |
| 时间轴覆盖 | 33,590 ms | 34,100 ms | 动态多覆盖 510 ms |
| 输出重复数 | 1 | 1 | 未增加重复 |
| 候选片段数 | 19 | 19 | 一致 |
| 跨核心边界片段数 | 0 | 0 | 均无跨界片段 |
| 边界附近候选数（±1 秒） | 0 | 1 | 动态边界靠近片段端点，但未穿过片段 |
| 去重片段数 | 0 | 0 | 一致 |
| 窗口数 | 2 | 2 | 一致 |
| 固定回退窗口数 | 0 | 0 | 动态成功使用自然停顿 |
| 边界吸附总距离 | 0 samples | 81,920 samples（5.12 秒） | 动态边界偏离 60 秒目标 5.12 秒 |

- 夹具 ID：`real-video-first-120s-20260715`
- 媒体指纹：`ef76385e8889457be05319be5221c256e40aae36778a6414df0f0a3f298c62f6`
- 模型/设备：本地 `large-v3`、CUDA FP16、Beam Size 5、`chunk_segments`

## 真实浏览器验证

目标流程：本地首页 → 展开默认配置 → 关闭/开启动态边界 → 打开历史任务识别配置 → 确认旧任务保持固定 → 取消编辑。

| 检查 | 桌面 | 390×844 移动端 |
|---|---|---|
| 页面身份与非空内容 | PASS | PASS |
| Vite/运行时错误覆盖层 | 无 | 无 |
| 控制台 error/warn | 0 | 0 |
| 默认配置显示“边界 · 动态” | PASS | PASS |
| 动态边界开关默认开启 | PASS | PASS |
| 关闭后摘要变为“边界 · 固定” | PASS | 未重复操作 |
| 重新开启并恢复初始状态 | PASS | PASS |
| 历史任务摘要显示“固定边界” | PASS | PASS |
| 历史任务编辑器开关保持关闭 | PASS | DOM 中可见 |
| 视觉布局 | 无遮挡或溢出 | 单列布局正常，无横向裁切 |

浏览器验证没有保存任务配置，也没有修改既有任务产物；新任务默认开关在测试结束前已恢复为开启。API 集成测试同时确认新建任务的识别配置显式写入 `dynamic_chunking=true`。

## 当前验证命令

| 命令/探针 | 结果 |
|---|---|
| `uv run --project apps/sidecar --extra dev pytest apps/sidecar/tests/test_faster_whisper.py -q` | 17 passed |
| `uv run --project apps/sidecar --extra asr --extra dev pytest` | 115 passed，1 个既有 Starlette 弃用警告 |
| `uv run --project apps/sidecar --extra dev ruff check apps/sidecar` | PASS |
| `uv run --project apps/sidecar --extra dev ruff check --config apps/sidecar/pyproject.toml tooling` | PASS |
| `npm run lint`（`apps/web`） | PASS |
| `npm run build`（`apps/web`） | PASS |
| `cargo check --manifest-path apps/desktop/Cargo.toml --target x86_64-pc-windows-msvc --locked` | PASS |
| 应用内 Browser 桌面与移动端流程 | PASS |

## 待提交后门禁

- 独立 Reviewer 对精确提交 HEAD 重新审查；仅 `PASS` 后才可转 Ready。
- GitHub Actions 全绿后合并；随后快进本地 `main` 并删除本地/远端功能分支。
