# CaptionNest Issue #36 设计 QA

## 比较目标与证据

- Source visual truth：Figma 节点 `30:30`，本地同尺寸源图：`C:\Users\admin\.codex\visualizations\2026\07\17\019f6dfe-6eb4-7591-bf2e-533b8f144719\captionnest-figma-source-1440x1024.png`
- Figma 品牌资源：`https://www.figma.com/api/mcp/asset/9b5fc1b5-46b4-42a3-8b49-06edd8890942`
- 最终实现截图：`C:\Users\admin\AppData\Local\Temp\captionnest-issue36-figma-reskin\readability-token-1440x1024.png`
- 最终弹窗截图：`C:\Users\admin\AppData\Local\Temp\captionnest-issue36-figma-reskin\readability-token-modal-1440x1024.png`
- Viewport：`1440x1024`
- State：任务列表、任务 Inspector、新建任务弹窗；弹窗尺寸 `650x570`，位置 `x=395, y=227`。
- Full-view comparison：`C:\Users\admin\.codex\visualizations\2026\07\17\019f6dfe-6eb4-7591-bf2e-533b8f144719\captionnest-figma-vs-readable-token-implementation.png`
- 首轮同状态比较：`C:\Users\admin\.codex\visualizations\2026\07\17\019f6dfe-6eb4-7591-bf2e-533b8f144719\captionnest-figma-vs-implementation.png`
- Focused logo comparison：`C:\Users\admin\.codex\visualizations\2026\07\17\019f6dfe-6eb4-7591-bf2e-533b8f144719\captionnest-header-logo-focused-comparison.png`
- Focused toolbar comparison：`C:\Users\admin\.codex\visualizations\2026\07\17\019f6dfe-6eb4-7591-bf2e-533b8f144719\captionnest-toolbar-plus-focused-comparison.png`
- Focused typography comparison：`C:\Users\admin\.codex\visualizations\2026\07\17\019f6dfe-6eb4-7591-bf2e-533b8f144719\captionnest-readable-typography-focused-comparison.png`
- Focused Inspector comparison：`C:\Users\admin\.codex\visualizations\2026\07\17\019f6dfe-6eb4-7591-bf2e-533b8f144719\captionnest-readable-inspector-focused-comparison.png`
- Reviewer fix modal 1440：`C:\Users\admin\AppData\Local\Temp\captionnest-issue36-figma-reskin\reviewer-fix-modal-1440x1024.png`
- Reviewer fix modal 900：`C:\Users\admin\AppData\Local\Temp\captionnest-issue36-figma-reskin\reviewer-fix-modal-900x800.png`
- Reviewer fix modal 390：`C:\Users\admin\AppData\Local\Temp\captionnest-issue36-figma-reskin\reviewer-fix-modal-390x800.png`

## Findings

- 无剩余 P0/P1/P2。
- 已修复 P2：顶栏品牌图标从旧 favicon 替换为 Figma 提供的真实 `captionnest-logo.svg`，32px 槽位、裁切和青绿色调与源图一致。
- 已修复 P3：任务工具栏的“新建任务”改用 Lucide `Plus`；空态入口继续使用文件创建语义图标。
- 已修复 Reviewer P2：旧规则的高特异度曾让批量栏、任务 ID、总进度、空文件与模型提示回落到 7–9px；现由覆盖层中的 legacy selector readability floor 统一使用 `xs/sm` Token。

## 五项保真检查

| 表面 | 结果 | 证据 |
|---|---|---|
| 字体与排版 | 通过 | 按用户要求采用可读性优先 Token：xs 12/16、sm 14/20、md 18/26、lg 24/34；相对 Figma 的极小字号属于明确接受的产品偏差。 |
| 间距与布局 | 通过 | 顶栏 56px、侧栏 184px、工作区 856px、Inspector 400px；任务表 384px 高。 |
| 颜色与 Token | 通过 | `#121417` 画布、`#181a1f` 表面、`#08b7c7` 强调色、`#30343b` 边框与源图一致。 |
| 图像与资源 | 通过 | Logo 使用 Figma 原始 asset，无 CSS、文字或手绘 SVG 替代。 |
| 文案与内容 | 通过 | 简体中文产品文案一致；任务名、日志与模型状态属于运行时内容。 |

## 交互、可访问性与响应式

- 两个新建任务入口、侧栏导航、任务选择、Inspector、配置和运行操作保持真实功能。
- 弹窗打开后背景 `inert=true`、body 滚动锁定、焦点进入关闭按钮；Escape 关闭并返回触发按钮。
- 1440：页面 `scrollWidth=1440`，无横向溢出；56/184/856/400 与任务表 384px 几何不变，扫描到的文字裁切为 0。
- 900：弹窗 `760x768`，内容区 `scrollWidth=clientWidth=743`，文字裁切为 0，所有操作按钮在视口内。
- 390：弹窗 `374x784`，内容区 `scrollWidth=clientWidth=357`，文档宽度 390，文字裁切为 0，底部按钮完全可达。
- 浏览器核心流程检查 console errors：`0`。
- 可见文本计算样式扫描：1440、900、390 的默认工作台和弹窗均无 `<12px` 节点；三档扫描结果均为空数组。

## Comparison history

### Pass 1 — full view + modal/task state

- 证据：`captionnest-figma-vs-implementation.png`。
- P2：实现仍使用旧霓虹聊天气泡 favicon，品牌资产与 Figma 不符。
- P3：工具栏“新建任务”使用 FilePlus/上传感图标，Figma 为纯 Plus。
- 其余结构、颜色、尺寸、弹窗和任务 Inspector 无 P0/P1。

### Pass 2 — post-fix full view + focused regions

- 修复：下载 Figma 原始 logo 到 `apps/web/public/captionnest-logo.svg` 并由 `AppHeader` 复用；工具栏改用 Lucide `Plus`。
- 证据：`captionnest-figma-vs-final-implementation.png`、`captionnest-header-logo-focused-comparison.png`、`captionnest-toolbar-plus-focused-comparison.png`。
- 结果：先前 P2/P3 均关闭；无新增 P0/P1/P2。

### Pass 3 — readability-first typography tokens

- 用户反馈：实现虽然接近 Figma，但整体字号偏小，要求可读性优先于稿件的极小字号。
- 修复：在 Figma override 的 `:root` 集中新增 `--text-xs/--line-xs`、`--text-sm/--line-sm`、`--text-md/--line-md`、`--text-lg/--line-lg`；导航、正文、表格、按钮、输入、Inspector 与弹窗全部复用 Token，移除覆盖层内散落的数字字号。
- 证据：`captionnest-figma-vs-readable-token-implementation.png`、`captionnest-readable-typography-focused-comparison.png`、`captionnest-readable-inspector-focused-comparison.png`，以及 1440/900/390 浏览器扫描。
- 结果：关键字号分别为 12/16、14/20、18/26、24/34；三档均无横向溢出和文字裁切，既有几何不变，无新增 P0/P1/P2。

### Pass 4 — Reviewer legacy-specificity fix

- Reviewer finding：旧选择器仍把批量栏“已选 0 项”设为 8px、批量按钮设为 7px、任务 ID 设为 8px、总进度设为 9px，弹窗空文件与模型缺失提示设为 9px。
- 修复：在 Figma override 中新增高特异度可读性底线，显式覆盖 `.bulk-action-bar > span/button`、`.console-header > div span`、`.progress-row strong`、`.batch-source-empty`、`.inline-error`，并同时覆盖同源环境/模型状态选择器。
- 1440 计算样式：批量栏标签 12px、按钮 14px、任务 ID 12px、总进度 12px、空文件 12px、模型提示 12px；弹窗保持 `650x570`，页面宽度 1440。
- 900 证据：默认工作台与弹窗 `<12px` 节点均为 0，内容宽度 `743/743`，裁切 0，页面宽度 900。
- 390 证据：默认工作台与弹窗 `<12px` 节点均为 0，内容宽度 `357/357`，裁切 0，页面宽度 390。
- 结果：Reviewer 指出的 P2 已关闭，无新增 P0/P1/P2。

## 可接受差异

- Modal 在当前视口按 Issue #36 要求严格垂直居中，`y=227`；Figma 稿约为 `y=270`。
- 双来源按钮和额外配置按钮保留真实产品能力。
- Inspector 比静态稿更丰富，用于保留可恢复控制、状态与日志。
- 对比中的任务名、任务数量和运行日志为动态数据，不作为视觉偏差。
- 字号高于 Figma 稿的极小文字是用户明确要求的可读性优先偏差，并通过统一 Token 保持高密度工具风格。

## Implementation Checklist

- [x] 使用 Figma 真实品牌资源。
- [x] 工具栏使用纯 Plus 图标。
- [x] 校验 1440、900、390 三档布局。
- [x] 校验弹窗焦点、滚动锁和操作可达性。
- [x] 建立四级字体与行高 Token，并验证关键组件只复用 Token。
- [x] 覆盖旧高特异度选择器，并枚举三档视口全部可见文本的计算字号。
- [x] 运行 lint、build、Issue 定向测试和 diff-check。

final result: passed
