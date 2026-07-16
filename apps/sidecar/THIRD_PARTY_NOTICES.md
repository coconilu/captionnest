# 第三方软件声明

CaptionNest 自有源代码采用 Apache License 2.0。安装包还包含第三方软件与二进制组件；它们继续适用各自的许可证，**不因本项目采用 Apache-2.0 而改变**。

## 随安装包分发的主要组件

| 组件 | 作用 | 主要许可证 | 说明 |
|---|---|---|---|
| Python | sidecar 运行时 | PSF License | 许可证正文见 `licenses/Python-PSF.txt` |
| PyInstaller | Python onedir 打包 | GPL-2.0-or-later，含 Bootloader 特例 | 生成的应用不因使用 Bootloader 自动变为 GPL；仍需保留 PyInstaller 自身声明 |
| FastAPI / Starlette / Uvicorn | 本机 HTTP 服务 | MIT / BSD | Python 分发元数据随 sidecar 保留 |
| Faster-Whisper | 本地语音识别 | MIT | 模型文件不随安装包分发，用户按需下载 |
| CTranslate2 | 推理运行时 | MIT | CPU 运行时默认随 sidecar 分发；CUDA 库不随安装包分发 |
| PyAV | 媒体容器与解码绑定 | BSD-3-Clause | 正文见 `licenses/PyAV-BSD-3-Clause.txt` |
| FFmpeg libraries | PyAV wheel 内置的媒体库 | 取决于 FFmpeg 配置以及实际携带的外部库；不能只依赖 FFmpeg 自报的 LGPL/GPL 字段 | 正文见 `licenses/LGPL-2.1-or-later.txt`、`licenses/LGPL-3.0-or-later.txt` 与 `licenses/GPL-3.0-or-later.txt`；最终发布前还必须审计 wheel 内的动态库和对应源码义务 |
| React / Vite 及前端依赖 | 用户界面 | 以 MIT 为主 | 精确版本见 `apps/web/package-lock.json` |
| Tauri 及 Rust crates | Windows 桌面壳 | MIT、Apache-2.0 及其他兼容许可证 | 精确版本见 `apps/desktop/Cargo.lock` |
| WebView2 Bootstrapper | 安装 WebView2 Runtime | Microsoft 许可条款 | bootstrapper 随 NSIS 安装器分发，WebView2 Runtime 由 Microsoft 安装 |

通用 MIT 许可证正文见 `licenses/MIT.txt`；Apache-2.0 正文见项目根目录 `LICENSE`。

## PyAV / FFmpeg 发布边界

PyPI 的 PyAV Windows wheel 会链接并携带 FFmpeg 动态库，因此最终发布物的许可证取决于**实际 wheel 的构建选项**，不能只根据 PyAV 的 BSD-3-Clause 或本项目的 Apache-2.0 判断。

发布流程必须执行 `scripts/check-media-license.ps1`：

- 检测到 `--enable-nonfree` 时无条件中止；
- 在 Windows 上无法读取完整 FFmpeg 元数据或无法枚举 wheel 携带的 DLL 时中止，不把“未检测到”当作“可以发布”；
- 检测到 `--enable-gpl`、库报告 GPL、已知 GPL 外部编码库配置，或 wheel 实际携带的已知 GPL DLL 时，默认中止；当前至少覆盖 x264、x265、Xvid、vid.stab、Rubber Band 和 xavs/xavs2；
- `-AllowGpl` 只允许发行负责人在完成 GPL 许可证、对应源代码、修改记录、构建与再链接义务审查后显式覆盖；证据文件会记录覆盖状态和所有命中项，正式构建不会自动使用该参数；
- 将 PyAV 版本、FFmpeg 配置参数、自报许可证、wheel 携带的 DLL 文件名、GPL 命中项和门禁决定写入 `FFMPEG_BUILD_INFO.txt` 并随安装包分发；
- 发布者应保存对应 wheel、其来源 URL、哈希与可对应的源代码获取方式。

当前仓库提供 LGPL/GPL 文本并不代表任意 FFmpeg 构建都可直接分发，也不是法律意见。发行者仍需根据其实际启用的 codecs、外部库和发布地区完成审查。

### 官方 wheel 与正式构建状态

当前验证的官方 PyAV 18.0.0 Windows wheel 虽然让 FFmpeg 自报 `LGPL version 3 or later`，但其构建配置启用了 `libx264`、`libx265`，wheel 旁也实际携带相应 DLL。因此它会被默认许可证门禁拒绝，不能仅凭自报 LGPL 直接作为 CaptionNest 官方安装包发布。

CaptionNest 的正式 Windows workflow 不使用该官方 wheel。它从锁定且校验 SHA-256 的 PyAV 18.0.0 源码构建 wheel，链接由锁定 vcpkg baseline 构建的 FFmpeg 8.1.2；manifest 不启用 `gpl`、`nonfree`、`x264` 或 `x265`。最终门禁仍以 wheel 的实际 FFmpeg 配置和 DLL 清单为准，结果与输入来源写入 `FFMPEG_BUILD_INFO.txt` 和 `MEDIA_WHEEL_PROVENANCE.json` 随安装包分发。

正式发布只能选择以下路径之一：

1. 使用可追溯的 LGPL-only、decode-only PyAV/FFmpeg wheel，并确认产物中没有 GPL 外部库；
2. 完成适用于最终组合产物的完整 GPL 发行闭环，再由发行负责人显式使用 `-AllowGpl`，并保存和分发所需许可证、对应源代码与构建材料。

## 不随安装包分发的组件

| 组件 | 边界 |
|---|---|
| Whisper / Hugging Face 模型 | 由用户在应用内按需下载；模型卡与模型许可证由对应发布者提供 |
| Codex CLI | 应用只检测并调用用户自行安装、登录的 `codex exec`；Codex 不随本安装包分发 |
| LM Studio | 用户自行安装和启动；不随本安装包分发 |
| NVIDIA CUDA / cuDNN | GPU 加速为可选能力；相关运行库不随首版安装包分发 |

## 维护方式

依赖版本变化时，以 `apps/sidecar/uv.lock`、`apps/web/package-lock.json` 与 `apps/desktop/Cargo.lock` 为准重新核对许可证。若本文件与第三方组件自带声明冲突，以第三方组件原始许可证和声明为准。
