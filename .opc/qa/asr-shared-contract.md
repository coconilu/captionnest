# 共享 ASR 区间、诊断与 A/B 契约 QA

- 验收时间：2026-07-16T21:41:44+08:00
- 分支：`codex/asr-shared-contract`
- 基线与当前 HEAD：`e88b30dca1e14727e741dc559af899989fe5df20`
- 验收对象：当前未提交工作树；提交后 Reviewer 必须再对精确提交 HEAD 复核
- 结论：`PASS`

## 范围核对

| 项目 | 结果 | 证据 |
|---|---|---|
| sample 级半开区间与 speech 补集 | PASS | `normalize_intervals()`、`complement_intervals()` 及分区测试 |
| 固定窗口诊断 | PASS | 核心区连续全覆盖、上下文包含核心区、窗口候选计数与明细一致 |
| 候选诊断 | PASS | `candidate-*` 专用 ID、区间有界、非有限指标降级为 `null` |
| 词时间戳完整度 | PASS | 倒序、负值、明显越界及 0.5ms 越界均不计入有效数量 |
| 汇总计数 | PASS | 输出片段数与字幕明细绑定；重试计数满足 selected <= request <= retry candidate <= candidate |
| A/B 契约与隐私 | PASS | 只允许有限数值或 `null`；报告不保存字幕、Prompt、响应、密钥或路径 |
| 旧产物兼容 | PASS | 无 `diagnostics` 的旧 `transcription.json` 可直接加载 |
| 两种输出模式 | PASS | `word_resegmented` 与 `chunk_segments` 运行探针均生成一致诊断 |
| 懒导入 | PASS | 阻断 `faster_whisper` 模块后仍可导入 `sublingo_local.jobs` |
| 非目标 | PASS | 未实现动态切片、二次识别、#21 Attempt/Token 或 UI |

## 返工 finding 复核

| 原 finding | 结果 | 独立证据 |
|---|---|---|
| 轻微越界词时间戳被计为有效 | CLOSED | `_valid_word_offsets(0.0, 1.0005, window_duration=1.0)` 返回 `False` |
| 输出片段汇总可与实际字幕数量漂移 | CLOSED | `TranscriptionResult` 跨字段校验及失败回归测试 |
| 重试候选/请求/采用计数可越界 | CLOSED | 三组参数化失败回归测试 |

## 当前验证证据

| 命令或运行探针 | 结果 |
|---|---|
| `git diff --check main` | PASS |
| `uv run --project apps/sidecar --extra asr --extra dev pytest apps/sidecar/tests/test_asr_diagnostics.py apps/sidecar/tests/test_faster_whisper.py apps/sidecar/tests/test_pipeline.py` | 26 passed |
| `uv run --project apps/sidecar --extra asr --extra dev pytest` | 105 passed，1 个既有 Starlette 弃用警告 |
| `uv run --project apps/sidecar --extra dev ruff check apps/sidecar` | PASS |
| 两种 `ASROutputMode` 的 fake Faster-Whisper 运行探针 | 两种模式均 PASS |
| 禁用 `faster_whisper` 模块后的导入探针 | `LAZY_IMPORT_OK` |

## 跳过项与合并边界

- 未运行前端 lint/build、Cargo 和真实浏览器：本 PR 不修改 Web、桌面或用户交互行为，不将这些项目记为已通过。
- GitHub CI 尚未运行；本报告仅表示独立 Reviewer 对当前工作树 `PASS`，自动合并仍须等待提交后精确 HEAD 复核与 CI 全绿。
- Reviewer 除本报告外未修改、暂存或提交任何受版本控制文件。
