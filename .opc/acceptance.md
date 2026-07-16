# CaptionNest OPC 验收契约

| Criterion | Verification method | Required evidence | Status |
|---|---|---|---|
| Issue 行为 | 逐条核对当前 Issue/Epic 的验收标准和非目标 | 验收表、真实 Diff、目标测试或产物 | pending |
| Build/runtime health | 运行受影响运行时的构建和静态检查 | 当前命令、退出码和精简结果 | pending |
| Automated tests | 先运行目标测试，再运行仓库要求的完整回归 | 当前测试结果；失败须区分既有问题与回归 | pending |
| Real user flow | 在真实浏览器、桌面壳或目标运行时操作关键流程 | 操作路径、可见状态及必要截图/记录 | pending |
| Backward compatibility | 加载旧任务、旧 API 数据或已有产物，验证兼容策略 | 兼容夹具和回归结果 | pending |
| Security and privacy | 检查密钥、Prompt、原始响应、路径、日志和外部进程边界 | 安全断言、日志/JSON 检查和 Diff 审阅 | pending |
| Independent review | Reviewer 检查当前 `main...HEAD`，不接受 Developer 总结作为证据 | 当前 HEAD 的 `PASS` 报告，无未解决 P0/P1/P2 | pending |
| GitHub integration | PR 检查与 review thread 全部收敛 | GitHub CI 全绿、无未解决阻断项 | pending |
| Merge lifecycle | 合并后同步并清理本地与远端状态 | `main == origin/main`，功能分支和专用 worktree 已删除 | pending |

## Review verdict contract

Reviewer 只能给出以下结论：

- `PASS`：全部必需标准有当前、直接、可复现的证据。
- `CHANGES_REQUIRED`：给出优先级、文件/行号、影响、复现方式和有边界的修复合同。
- `BLOCKED`：说明缺少的外部条件及为何无法在当前授权范围内继续。

跳过、陈旧、间接推断或仅由 Developer 声称通过的检查，一律不得记为 `PASS`。Reviewer 在评估期间不得修改受版本控制的产品源码；允许测试/build 生成被忽略的临时产物，并可写入 `.opc/qa/` 验收报告。

## Experience path

```text
确认 Issue 契约
  → 创建独立分支和 Draft PR
  → 唯一 Developer 实现并自测
  → 独立 Reviewer 检查真实 Diff 与运行证据
  → CHANGES_REQUIRED 时交还原 Developer 修复
  → Reviewer 重新检查当前 HEAD
  → PASS + GitHub CI 全绿
  → 自动合并、同步 main、清理分支/worktree
  → 进入下一项
```

## Known non-goals

- 不用多名 Agent 并行修改产品源码制造“多人开发”。
- 不因全自动模式扩大 Issue、变更产品方向或跳过外部授权边界。
- 不自动晋升组织经验，不启用或安装 Mem0，不修改全局 Codex 配置。
- 不把 Reviewer 变成修复者；发现问题始终交还原 Developer。
- 不把 Draft PR、旧测试结果或源码阅读单独视为可合并证据。
