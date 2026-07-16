# 架构说明

## 总体结构

```mermaid
flowchart LR
    subgraph Desktop["Tauri 2 Windows 应用"]
        WEB["React / Vite"]
        LIFE["sidecar 生命周期"]
        BRIDGE["document-start fetch bridge"]
    end
    subgraph Python["Python onedir sidecar"]
        API["FastAPI"]
        JOB["Job Manager"]
        AV["PyAV"]
        ASR["ASR Provider\nFaster-Whisper 分片识别"]
        ROUTER{"Translator Provider"}
        VALIDATE["稳定 ID 校验"]
        SRT["双语 SRT Writer"]
    end
    WEB --> BRIDGE -->|"127.0.0.1:随机端口\nX-CaptionNest-Session"| API
    LIFE -->|"启动 / 退出时终止"| API
    API --> JOB --> AV --> ASR --> ROUTER --> VALIDATE --> SRT
    ROUTER --> CODEX["codex exec"]
    ROUTER --> LM["LM Studio"]
    ROUTER --> DS["DeepSeek-compatible"]
```

## 模块责任

| 模块 | 责任 | 不负责 |
|---|---|---|
| Tauri 壳 | 随机端口/令牌、sidecar 生命周期、应用数据目录、原生插件 | 识别和翻译业务 |
| React UI | 输入、目标语言、Provider、环境与任务状态 | 直接持有时间轴或密钥 |
| FastAPI | 本机 API、会话校验、任务和系统集成 | 对公网监听 |
| PyAV | 从视频容器读取可解码媒体 | 调用系统 `ffmpeg.exe` |
| ASR Provider | 自动语言检测、分段文本和时间戳 | 翻译 |
| Translator Provider | 稳定 ID 到目标语言文本 | 修改时间轴 |
| Pipeline | 阶段调度、进度、失败清理、唯一产物路径 | 持久化 API Key |
| SRT Writer | 原文在上、译文在下的单文件输出 | 再次切分时间轴 |

## 桌面进程生命周期

```mermaid
sequenceDiagram
    participant T as Tauri
    participant P as Python sidecar
    participant W as WebView
    T->>T: 绑定 127.0.0.1:0 获取随机端口
    T->>T: 生成 256-bit 等效随机会话令牌
    T->>P: 参数数组启动，传 data-dir/port 和环境变量 token
    loop 最长 30 秒
        T->>P: GET /api/health + X-CaptionNest-Session
        P-->>T: 200 OK
    end
    T->>W: document-start 注入 fetch bridge 后创建窗口
    W->>P: /api/* + 会话请求头
    T->>P: 应用退出时 kill sidecar
```

Tauri 只在带令牌的健康检查返回 200 后创建窗口。这样既避免端口竞态把 WebView 接到其他进程，也避免页面早于 API 就绪。sidecar 的 stdout/stderr 只被排空，不落盘，避免用户路径进入持久日志。

## 数据与安全边界

| 边界 | 约束 |
|---|---|
| 网络 | sidecar 固定监听 `127.0.0.1`；桌面模式所有 `/api/**` 必须带随机令牌 |
| WebView | 初始化脚本只重写本应用或本机后端的 `/api/` 请求，不向外部域名附加令牌 |
| 密钥 | API Key 仅存在于任务内存，不出现在 JobView、日志和磁盘 |
| 外部进程 | 全部使用参数数组与 `shell=False` 等价方式；禁止拼接 shell 命令 |
| 时间轴 | 由程序持有；模型只能翻译稳定 ID 文本；写出前严格校验 ID 集合 |
| 文件 | 默认输出源视频同目录；上传副本则输出到应用数据目录中的副本旁 |
| 模型 | 保存到 Tauri 应用数据目录；采用固定提交、临时目录、校验与原子替换 |

## 打包布局

```text
CaptionNest 安装目录/
├─ captionnest.exe                 # Tauri 主程序
├─ captionnest-sidecar.exe         # externalBin，PyInstaller bootloader
├─ _internal/                      # Python、包、PyAV/FFmpeg、CTranslate2
├─ LICENSE
├─ THIRD_PARTY_NOTICES.md
├─ FFMPEG_BUILD_INFO.txt
└─ licenses/
```

PyInstaller 使用 onedir，不使用 onefile 的每次启动临时解压。安装包不携带 ASR 模型、Codex、CUDA/cuDNN 或 LM Studio；它们分别按需下载、用户安装或作为可选环境使用。产品界面与公开能力列表只启用 Faster-Whisper。

Faster-Whisper 先从全片分布式抽样窗口投票确定主语言，再以 60 秒核心窗口和前后 2 秒上下文转写。跨窗口字幕按核心区中点归属并去重。最终可输出两种时间轴：

| 输出模式 | 行为 |
|---|---|
| 逐词重排 | 使用模型逐词时间戳按停顿、时长、字符数和句末标点重新切分；默认 |
| 分片原始段 | 保留模型在每个分片内返回的原始段落边界；用于诊断 |

Qwen 仅保留为源码级实验兼容能力，不向普通用户展示。其模型目录是一个组合包：`asr/` 保存 Qwen3-ASR-1.7B，`aligner/` 保存
Qwen3-ForcedAligner-0.6B。Provider 通过 PyTorch/Transformers 在 CaptionNest 进程内直接调用，
ForcedAligner 的字符/词时间戳由程序聚合成可读字幕段，不经过 LM Studio。为了限制长音频中的对齐漂移，
Provider 使用最长约 60 秒的低能量分窗，首个可靠窗口完成语言检测后锁定语言；时间戳质量门会拒绝零时长堆积和跨越异常长度的单词。

## Provider 契约

所有翻译 Provider 接收同一 `TranslationBatch` 并返回 `TranslatedBatch`。程序先按稳定 ID 规范化模型输出；
无关的额外 ID 会被忽略，顺序会按源字幕恢复。若仍有缺失、重复或空译文，则自动把当前批次递归拆小重试。
SRT 写回前必须满足：

| 检查 | 失败结果 |
|---|---|
| 多余 ID 或顺序变化 | 按稳定 ID 自动修复并记录警告 |
| 遗漏、重复或空译文 | 当前批次拆小重试；单条仍失败才终止任务 |
| 最终 ID 集合完全一致 | 校验失败则不生成文件 |
| 最终译文非空 | 单条重试后仍为空则不生成文件 |
| 源语言与目标语言不同 | 同语任务提前失败 |
| 时间轴只来自 ASR | 模型输出中的时间信息被忽略 |
