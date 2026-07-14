# 贡献指南

感谢参与 CaptionNest。首版目标是让普通 Windows 用户无需配置 Python、Whisper 或 FFmpeg 即可生成单个双语字幕。

## 提交范围

| 改动 | 必须满足 |
|---|---|
| Python | 放在 `src/sublingo_local/`，测试放在 `tests/`；Faster-Whisper 延迟导入 |
| React | 放在 `web/`，组件小而专一；默认简体中文 |
| 翻译 Provider | 统一接口；不改变时间轴；不记录 API Key |
| 外部进程 | 参数数组调用；禁止拼接 shell 命令 |
| 桌面/打包 | 不扩大 capability；验证 sidecar 退出；更新许可证声明 |
| 用户行为 | 一个双语 SRT；源语言自动检测；目标语言只支持 zh-CN/en/ko |

## 开发流程

1. 从小而可审阅的分支开始，避免夹带无关格式化。
2. 对行为变化补测试；界面变化完成真实浏览器验证。
3. 按 [开发指南](docs/development.md) 运行 Python 测试、ruff、前端 lint/build 和适用的桌面检查。
4. 修改依赖时提交对应 lock，并更新 `THIRD_PARTY_NOTICES.md`（如许可证边界变化）。
5. PR 说明用户可见变化、验证证据、已知限制和隐私/许可证影响。

不要提交 API Key、ChatGPT/Codex 登录信息、用户视频、字幕、模型文件、PyInstaller 产物或本机绝对路径。
