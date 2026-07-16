# Issue #19 实验性时间戳规范化 QA

- 验收日期：2026-07-16
- 分支：`codex/issue-19-timestamp-normalization`
- 基线 HEAD：`92049486bff46a85511ebb853925cc5aa78c784b`
- 当前结论：`PRELIMINARY PASS`；功能保持默认关闭，提交后仍须独立 Reviewer 对精确 HEAD 复核并等待 GitHub CI 全绿
- 隐私边界：只记录不可逆媒体/配置指纹、耗时与数值指标，不记录媒体路径、字幕正文或原始异常

## 上游研究与独立实现

| 检查项 | 结果 | 证据 |
|---|---|---|
| 固定来源 | PASS | 研究 `jianfch/stable-ts` 的 `e312072cc024ae9fceb25b057d7d18524873a02b` 提交 |
| 研究范围 | PASS | 静音抑制与 `adjust_gaps` 的行为原则，来源链接见 `docs/timestamp-normalization.md` |
| 许可证 | PASS | 上游 MIT；本仓库未复制其源码，也未引入其包或运行时依赖 |
| 实现差异 | PASS | sample 级纯函数、复用 #17 共享 VAD、300 ms 硬上限、冻结分组、结构异常整轨回退 |

## 验收合同

| 检查项 | 结果 | 证据 |
|---|---|---|
| 流水线位置 | PASS | #16 择优和跨窗口去重全部结束后、两种字幕渲染器之前执行 |
| 静音抑制 | PASS | 起点/终点落入不少于 120 ms 的非语音区间时，分别尝试吸附到右/左边缘 |
| gap adjustment | PASS | 优先使用可安全到达的静音两侧；否则只对重叠或不超过 120 ms 的小 gap 使用确定性中点 |
| 有界性 | PASS | 每个边界最多移动 300 ms；词和父分片至少 100 ms |
| 结构不变量 | PASS | 范围、单调性、父子包含、数量、顺序和逐边界移动上限均在纯函数末端校验 |
| 内容与身份 | PASS | 分组依据校时前快照；两种输出模式均验证文本序列与稳定 ID 不变 |
| 回退 | PASS | 单点不安全保留原边界；整轨结构不安全回退；VAD 不可用精确 no-op |
| 开关与兼容 | PASS | 新旧任务均默认 `timestamp_normalization=false`；旧任务缺字段恢复为关闭 |
| 依赖与隐私 | PASS | 无 stable-ts/PyTorch 依赖，无新增模型调用；诊断和 A/B 只保存数值 |

## 真实视频前 120 秒 A/B

- 夹具 ID：`real-video-a-first-120s-issue-19`
- 媒体 SHA-256：`ef76385e8889457be05319be5221c256e40aae36778a6414df0f0a3f298c62f6`
- 模型/设备：本机 `large-v3`、CUDA FP16、Beam Size 5、动态边界、选择性二次识别开启
- 两个变体仅 `timestamp_normalization` 不同；每种输出模式按关闭→开启顺序运行

### 分片原始段

| 数值指标 | 关闭 | 开启 | 结论 |
|---|---:|---:|---|
| 配置指纹 | `3c191815…a4ee` | `8832e918…db0a` | 仅实验开关不同 |
| 耗时 | 8,665 ms | 8,326 ms | 单次顺序值，不宣称提速 |
| 字幕数 / 非空字符数 | 19 / 133 | 19 / 133 | 内容覆盖不变 |
| 文本与 ID 指纹 | `4d036c14…ac39` | `4d036c14…ac39` | 完全一致 |
| 字幕重叠 | 0 段 / 0 ms | 0 段 / 0 ms | 未制造重叠 |
| 越界 / 起点倒序 / 重复 ID | 0 / 0 / 0 | 0 / 0 / 0 | 结构有效 |
| 边界侵入非语音 | 160,096 samples | 156,256 samples | 减少 3,840 samples（240 ms） |
| 时间轴覆盖 | 34,100 ms | 33,918 ms | 总计收紧 182 ms |
| 最终字幕边界移动 | 0 | 4 个；P50 70 ms、P90 103 ms、最大 112 ms | 全部低于 300 ms |

### 逐词重排

| 数值指标 | 关闭 | 开启 | 结论 |
|---|---:|---:|---|
| 配置指纹 | `17dd96d6…07eb` | `ef2f9bd3…5840` | 仅实验开关不同 |
| 耗时 | 8,002 ms | 8,257 ms | 单次顺序值，不宣称提速 |
| 字幕数 / 非空字符数 | 21 / 133 | 21 / 133 | 分组与内容不变 |
| 文本与 ID 指纹 | `0f758656…ee6` | `0f758656…ee6` | 完全一致 |
| 字幕重叠 | 0 段 / 0 ms | 0 段 / 0 ms | 未制造重叠 |
| 越界 / 起点倒序 / 重复 ID | 0 / 0 / 0 | 0 / 0 / 0 | 结构有效 |
| 边界侵入非语音 | 135,008 samples | 128,800 samples | 减少 6,208 samples（388 ms） |
| 时间轴覆盖 | 24,580 ms | 24,398 ms | 总计收紧 182 ms |
| 最终字幕边界移动 | 0 | 6 个；P50 97 ms、P90 148 ms、最大 148 ms | 全部低于 300 ms |

两种模式的规范化内部统计相同：移动 16 个词边界和 4 个父分片边界，绝对移动总量 28,256 samples；未发生整轨回退。103 个候选因超过上限、最短时长或父子锚点约束而保留原值。真实样本显示静音侵入下降且身份稳定，但被拒绝候选较多，因此只能作为实验性收益证据，不能支持默认开启。

## 波形标注短音频

- 夹具 ID：`issue-19-windows-tts-en-waveform-v1`
- 媒体 SHA-256：`391f6b603c0085e501ba92e4602edb67986bbba9c65b240aec89e8c1a181c585`
- 时长：33,404 ms；6 个独立语音岛，岛间插入 2,200 ms 精确零值静音
- 标注：每个独立 TTS 片段按 10 ms 帧峰值、全片峰值 3% 阈值和前后 10 ms padding 生成可复现波形边界
- 输出：`word_resegmented`；模型/设备为本机 `large-v3` / CUDA FP16

| 指标 | 关闭 | 开启 | 结论 |
|---|---:|---:|---|
| 字幕数 / 标注语音岛 | 6 / 6 | 6 / 6 | 无遗漏 |
| 文本与 ID 指纹 | `5dcea7c1…9757` | `5dcea7c1…9757` | 完全一致 |
| 边界误差样本数 | 12 | 12 | 每个语音岛起止各 1 个 |
| 边界误差 P50 | 281 ms | 281 ms | 不变 |
| 边界误差 P90 | 911.2 ms | 911.2 ms | 不变 |
| 最大边界误差 | 973 ms | 973 ms | 不变 |
| 重叠 / 静音侵入 | 0 / 57,856 samples | 0 / 57,856 samples | 无回归，也无收益 |
| 规范化移动 | 0 | 0；17 个候选保留原值 | 300 ms 保守上限阻止大幅追边 |

该夹具先以 900 ms 停顿试跑，但模型只形成 3 个字幕分组，无法覆盖 6 个标注语音岛，故该轮数字作废。有效版本将停顿增至 2,200 ms，逐词重排得到 6/6 对齐。结果证明保守策略不会为追求波形边界而做超过上限的移动，但也没有改善该样本的 P50/P90，再次支持“默认关闭”。

## 自动化回归

| 检查 | 结果 |
|---|---:|
| Sidecar `uv run --project apps/sidecar --extra asr --extra dev pytest -q` | 196 passed，1 个 Starlette 弃用警告 |
| Sidecar `uv run --project apps/sidecar --extra dev ruff check apps/sidecar` | passed |
| Tooling `uv run --project apps/sidecar --extra dev pytest tooling/tests -q` | 25 passed |
| Tooling Ruff | passed |
| Web `npm run lint` | passed |
| Web `npm run build` | passed |
| Desktop `cargo fmt --check && cargo check --locked` | passed |
| `git diff --check` | passed |

## 真实浏览器

隔离 API 使用 `.tmp/issue19-browser-data` 与独立端口，未读取或修改现有任务；验收结束后服务均已停止。

| 场景 | 桌面 | 390×844 |
|---|---|---|
| 新任务默认开关关闭 | PASS | PASS |
| 默认设置开启后摘要显示“校时 · 实验开启” | PASS | DOM 状态一致 |
| 页面刷新后默认设置仍保持开启 | PASS | DOM 状态一致 |
| 测试结束前恢复默认关闭 | PASS | PASS |
| 任务内识别配置初始关闭 | PASS | PASS |
| 任务内开启并保存 | API `timestamp_normalization=true`、配置 v2 | 摘要显示“实验校时” |
| 横向溢出 | 0 px | 0 px |
| 视觉检查 | 开关、说明、卡片摘要无重叠 | 单列布局、触控开关与文字均完整可见 |
| 控制台 warning/error | 0 | 0 |

自动化首次使用 `networkidle` 等待 Vite 页面刷新，被开发服务器长连接阻塞并超时；改用适用的 `domcontentloaded` 后重跑通过。另一次直接点击隐藏 checkbox 被标签文字拦截，改为按真实用户方式点击 `<label>` 后通过；两次均不是页面运行时错误。

## 独立 Reviewer

待实现提交后，由同一只读 Reviewer 对精确 HEAD 审查；任何 P0–P3 问题均由原 Developer 修复并重新送审，直至 `PASS`。
