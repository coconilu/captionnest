# SubLingo Local

本地优先的视频字幕生成与中文翻译工具。

将日语或英语视频交给本机 Faster-Whisper 识别，再选择以下任一翻译方式：

- **Codex Spark**：复用本机 Codex CLI 的 ChatGPT 登录与 `gpt-5.3-codex-spark` 额度。
- **LM Studio**：调用本机 OpenAI-compatible 服务，完全离线翻译。
- **DeepSeek**：调用 DeepSeek 或其他 OpenAI-compatible `/chat/completions` 服务。

默认输出到源视频同目录：

```text
movie.mp4
movie.ja.srt       # 可选原文字幕
movie.zh-CN.srt    # 中文字幕
```

## 当前能力

| 环节 | 实现 |
|---|---|
| 视频输入 | 本机系统文件选择 |
| 语音识别 | Faster-Whisper `large-v3` / `large-v3-turbo` |
| 语言 | 自动检测、日语、英语 |
| 在线翻译 | Codex Spark、DeepSeek/OpenAI-compatible |
| 本地翻译 | LM Studio/OpenAI-compatible |
| 输出 | 原文 SRT（可选）和简体中文 SRT |
| 任务反馈 | 三阶段进度、实时日志、错误与完成态 |

> 应用通过系统文件选择器使用原始视频路径，中文字幕会写回源视频同目录，无需复制视频。

## 环境要求

- Windows 10/11（首要支持平台）
- Python 3.11–3.12
- Node.js 20+
- FFmpeg
- NVIDIA GPU + CUDA（推荐，但 CPU 也可运行）
- Codex CLI（仅 Codex Spark 模式需要）
- LM Studio（仅完全本地翻译模式需要）

模型首次使用时会从 Hugging Face 下载并缓存。若需要自定义镜像，可在启动前设置
`SUBLINGO_HF_ENDPOINT`；本机现有的 `HF_ENDPOINT=https://hf-mirror.com` 会自动切换到其当前实际跳转的官方地址。

## 安装

```powershell
cd C:\Users\admin\Documents\study\sublingo-local
.\scripts\setup.ps1
```

手动安装：

```powershell
uv sync --extra asr --extra dev
npm --prefix web install
npm --prefix web run build
```

## 启动开发环境

```powershell
.\scripts\dev.ps1
```

随后打开 `http://127.0.0.1:5173`。Vite 会把 `/api` 请求代理到本地 FastAPI 服务。

也可以分别启动：

```powershell
uv run --extra asr uvicorn sublingo_local.app:app --host 127.0.0.1 --port 8765 --reload
npm --prefix web run dev
```

## 翻译方式

### Codex Spark

先确认本机状态：

```powershell
codex login status
```

应用通过非交互 `codex exec` 调用模型，不需要 OpenAI API Key。视频和音频不会发送给 Codex，只有分段后的字幕原文会发送。

### LM Studio

1. 在 LM Studio 下载并加载支持中、日、英的指令模型。
2. 启动 Local Server，默认地址为 `http://127.0.0.1:1234/v1`。
3. 在界面填写 LM Studio 显示的模型 ID；API Key 可留空。

本机 RTX 5090 D 32GB 显存建议优先尝试 Qwen3-30B-A3B 的 Q4/Q5 GGUF。

### DeepSeek / OpenAI-compatible

默认 Endpoint 为 `https://api.deepseek.com`。API Key 只存在于当前任务内存中，不会写入任务状态、日志或配置文件。

## 测试

```powershell
uv run --extra dev pytest
uv run --extra dev ruff check .
npm --prefix web run lint
npm --prefix web run build
```

## 桌面版演进

首版刻意保持 `React Web UI → 本地 FastAPI → 处理流水线` 的清晰边界。后续可以复用 React 界面，使用 Tauri 或其他桌面壳管理 Python sidecar，而无需重写识别和翻译 Provider。

详细架构见 [docs/architecture.md](docs/architecture.md)。

## 隐私边界

| 模式 | 留在本机 | 会发送到外部服务 |
|---|---|---|
| Codex Spark | 视频、音频、时间轴 | 分段字幕原文 |
| LM Studio | 全部数据 | 无 |
| DeepSeek | 视频、音频、时间轴 | 分段字幕原文 |
