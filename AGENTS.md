# CaptionNest development guidance

- 用户界面和文档默认使用简体中文。
- Python 代码放在 `apps/sidecar/src/sublingo_local/`，业务测试放在 `apps/sidecar/tests/`，仓库级测试放在 `tooling/tests/`。
- React/Vite 前端放在 `apps/web/`，组件应保持小而专一，避免把全部界面写进一个组件。
- Tauri 桌面壳放在 `apps/desktop/`，PyInstaller 和媒体运行时构建输入放在 `tooling/packaging/`。
- Faster-Whisper 必须延迟导入，确保未安装 GPU 依赖时仍能启动 Web UI 和运行单元测试。
- 翻译器必须实现统一 Provider 接口；不得在日志或持久化文件中记录 API Key。
- Codex Spark 通过本机 `codex exec` 和现有 ChatGPT 登录调用，不得伪装成 OpenAI API。
- LM Studio 与 DeepSeek 走 OpenAI-compatible `/chat/completions` 接口。
- 时间轴由程序持有；模型只翻译稳定 ID 对应的文本。
- 输出双语字幕默认写入源视频同目录，格式为 `<视频名>.srt`，目标语言不写入文件名。
- 所有外部进程调用必须使用参数数组，禁止拼接 shell 命令。
- 提交前运行 Python 测试、前端 lint/build，并验证真实浏览器核心流程。
- PR 合并后，切回并快进更新本地 `main`，确认与 `origin/main` 一致；随后删除已合并的本地与远端功能分支，以及该分支使用的专用 worktree。

## 多 Agent PR 门禁

- 产品源码采用单写入者模式：始终只有 `captionnest-developer` 可以实现和修复；主控负责范围、证据与 Git 生命周期。
- 每个 PR 必须由独立的 `captionnest-reviewer` 按 [`docs/code-review.md`](docs/code-review.md) 审查当前 `main...HEAD`；Reviewer 不得修改受版本控制的产品源码。
- Reviewer 返回 `CHANGES_REQUIRED` 时，所有问题必须交还同一 Developer 修复，并由 Reviewer 对新的 HEAD 重新检查。
- 只有当前 HEAD 获得 `PASS`、所需本地验证和 GitHub CI 全绿后才可自动合并；HEAD 变化会使旧 `PASS` 立即失效。
