# Issue #18 QA：任务级 Hotwords

## 验收结论（Developer 自检）

| 契约 | 证据 |
|---|---|
| 结构化任务配置 | `ASRSettings.hotwords: string[]`；旧任务缺字段恢复为空数组 |
| 规范化与限制 | 去首尾空格、去空项、稳定去重；50 条、单条 64 字符、合计 512 字符；控制字符与非法类型拒绝 |
| 运行时一致性 | 空数组省略 Faster-Whisper 参数；非空值以 `, ` 序列化，固定/动态首轮窗口与 #16 二次识别复用完全相同字符串 |
| Token 预算 | 使用当前模型的同一 tokenizer 在首次识别调用前预检；超过 `max_length // 2 - 1` 时整次拒绝，避免 Faster-Whisper 静默截断 |
| 隐私 | 任务配置保存词表；处理日志、识别产物、诊断与 A/B 报告只保存数量或数值，不保存词表正文 |
| Web | 新任务默认设置与任务内识别编辑器均为每行一词；实时显示去重计数、字符数和中文错误，非法时禁用保存/启动 |

## 自动化回归

| 检查 | 结果 |
|---|---:|
| Sidecar `uv run --extra dev pytest -q` | 155 passed |
| Sidecar `ruff check src tests` | passed |
| 仓库级 `python -m pytest -q` | 25 passed |
| Web `npm run lint` | passed |
| Web `npm run build` | passed |
| Desktop `cargo fmt --check && cargo check` | passed |
| 本机 `large-v3` Token 预算复现 | 合法 50 条 / 450 字符 CJK 词表编码为 699 Token；预算 223，调用前明确拒绝 |
| `git diff --check` | passed |

## 真实浏览器

| 场景 | 结果 |
|---|---|
| 任务内输入空行、前后空格和重复词 | 显示 3 条、23 字符；保存后 API 为稳定去重数组，配置版本升至 v2 |
| 单条 65 字符 | `aria-invalid=true`；显示“单个提示词不能超过 64 个字符（第 1 项）”；保存按钮禁用 |
| 新任务默认输入含重复词 | 摘要与提示均显示去重后的 2 条、19 字符 |
| 视觉与控制台 | 全页截图人工检查通过；0 条 warning/error |

## 真实 Faster-Whisper A/B

- Fixture：`issue-18-windows-tts-en-v1`
- Media SHA-256：`e79a7583fe01d3fd8dd04635962262111763b68e6d1144757a981fa0b9034425`
- 模型/设备：本机 `large-v3` / RTX 5090 D / CUDA FP16
- 词项类型：项目名、人名/角色名、作品名、日语地名及罗马字专有词

| 数值指标 | 无 Hotwords | 有 Hotwords |
|---|---:|---:|
| 已知词命中数 | 3 / 5 | 4 / 5 |
| 已知词命中率 | 60% | 80% |
| 普通句词错误数 | 1 / 14 | 1 / 14 |
| 普通句 WER | 7.14% | 7.14% |
| 两个夹具总耗时 | 11,869 ms | 10,822 ms |

A/B 报告只记录上述数值、媒体哈希与配置指纹；未保存音频路径、提示词正文或转写正文。

## 独立 Reviewer

首轮只读审查在 `00c377e7d73bce5d20f8cad632fff8d3b03ed14c` 发现 P1：合法 CJK 词表可超过模型 223 Token 预算并被 Faster-Whisper 静默截断。修复采用调用前整次拒绝，不保存或输出词表正文；精确修复提交将重新送交同一 Reviewer。
