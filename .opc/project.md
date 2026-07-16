# CaptionNest OPC 项目契约

## Purpose

CaptionNest 是一款本地优先的 Windows 字幕工作台：从本机视频提取音频，使用 Faster-Whisper 生成稳定时间轴，通过统一翻译 Provider 生成译文，并在源视频同目录输出双语 SRT。

## Current state

- 产品已有可持久化、可重试的单文件四阶段流水线：媒体准备、语音识别、字幕翻译、字幕导出。
- Python Sidecar、React/Vite Web 与 Tauri 桌面壳位于同一仓库。
- 当前 ASR 主线仅支持 Faster-Whisper；历史 Qwen 任务只读兼容。
- 当前产品模型仍是单任务、单文件；后续按已批准的 Issue 顺序改善 ASR 质量、可观测性与多任务队列。

## Repository and runtime

| 范围 | 位置 | 责任 |
|---|---|---|
| Python Sidecar | `apps/sidecar/src/sublingo_local/` | API、流水线、ASR、翻译、字幕与任务持久化 |
| Python 测试 | `apps/sidecar/tests/` | Sidecar 业务与安全回归 |
| 仓库级测试 | `tooling/tests/` | 布局、打包与发布约束 |
| Web | `apps/web/` | React/Vite 用户界面 |
| Desktop | `apps/desktop/` | Tauri 生命周期与 Windows 桌面壳 |
| Packaging | `tooling/packaging/` | PyInstaller 与媒体运行时构建输入 |

## Durable constraints

- 用户界面和用户文档默认使用简体中文。
- Faster-Whisper 必须延迟导入；缺少 GPU/ASR 依赖时 Web UI 和单元测试仍须启动。
- 翻译器实现统一 Provider 接口，不记录或持久化 API Key、Prompt 或模型原始响应。
- Codex Spark 只通过参数数组调用本机 `codex exec` 与现有登录；LM Studio、DeepSeek 使用 OpenAI-compatible `/chat/completions`。
- 时间轴和稳定 ID 由程序持有；模型只翻译稳定 ID 对应的文本。
- 默认输出 `<源视频名>.srt` 到源视频目录，目标语言不进入文件名。
- 所有外部进程调用使用参数数组，禁止拼接 shell 命令。
- 每个 PR 必须通过独立 Reviewer、自动化检查和适用的真实浏览器流程；不以开发者总结代替证据。
- PR 合并后必须快进本地 `main`，确认与 `origin/main` 一致，并删除已合并的本地/远端分支及专用 worktree。

## Manager-owned decisions

- 产品方向、Issue 范围变化、不可逆或高风险操作、凭证、费用、部署和组织经验晋升。
- 当前已批准的精确串行实现顺序为：共享 ASR 区间/诊断/A-B 契约 → #17 → #16 → #21 → #18 → #19 → #22。
- 当前已批准全自动 PR 模式：只有上一项取得 Reviewer `PASS`、完成所需验证、GitHub CI 全绿、合并完成且分支与专用 worktree 清理完成后，才进入下一项。
- 开发采用单写入者模式：同一时间只有一个 Developer 修改产品源码；Reviewer 不修改产品源码。

## Team-owned decisions

- 已批准范围内的可逆实现细节、局部重构、测试策略和等价依赖选择。
- Reviewer 发现的范围内缺陷由原 Developer 修复，直至当前 HEAD 重新通过审查。
- 对可复现的本地失败进行有界重试，并保留真实证据。

## Build and test commands

```powershell
uv run --project apps/sidecar --extra asr --extra dev pytest
uv run --project apps/sidecar --extra dev ruff check apps/sidecar
uv run --project apps/sidecar --extra dev ruff check --config apps/sidecar/pyproject.toml tooling
Set-Location apps/web
npm run lint
npm run build
Set-Location ../..
cargo check --manifest-path apps/desktop/Cargo.toml --target x86_64-pc-windows-msvc
```

真实 UI 或桌面行为变更还必须运行适用的浏览器/桌面核心流程，不能只凭源码检查宣告完成。

## Relevant approved organizational experience

当前没有与本项目绑定的已批准组织经验。项目文件、GitHub Issue、真实代码、测试和运行结果是当前事实来源。
