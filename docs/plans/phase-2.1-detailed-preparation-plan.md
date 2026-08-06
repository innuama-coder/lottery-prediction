# Phase 2.1 详细准备计划

1. 核对任务分支、目标分支基线 SHA、独立 worktree、原任务输入和 iteration-01 修订；只复制输入快照，不修改任务目录与 `runs/00`。
2. 检查 Phase 1 冻结输入和历史 Phase 2 指针/摘要。把需要的 Git LFS 对象放入本地准备目录，核对 OID，不回写历史制品。
3. 依据 `requirements/phase2_1.lock` 下载 wheelhouse，创建普通用户 venv，并用 `--no-index` 安装。保存 wheel 文件名、大小和 SHA-256 清单。
4. 完成 Phase 2.1 代码、专用结果 Schema、合同、补充预注册、测试和文档。运行 Phase 2.1 与 Phase 2 回归、离线 build、`compileall` 和 `git diff --check`。
5. 仅在代码稳定后创建唯一 release 目录。readiness 收集资源事实、依赖、benchmark、源码摘要、冻结输入、隔离目录和证据回传 canary；资源事实不与通用容量阈值比较。
6. readiness 为 `READY` 且正式历史结果计数为 0 后冻结 G0/G1；随后先运行独立方法复核。G0/G1 或方法复核失败时不得生成正式 qualification、audit、power。
7. 同一 release 内按 qualification、audit、power、replay、独立重放复核和 10 个 E2E 的顺序生成新证据。所有核心 JSON 立即通过专用 Schema。
8. 把固定测试、build、lint 和正式命令日志复制到 release；生成递归 manifest 后禁止再改写任何已清单化文件。
9. final acceptance 从底层行重算 10/10 历史覆盖、240/240 功效覆盖、240/240 重放一致、10/10 E2E、blocking findings 和科学分类。最后运行只读 final bundle validator。
10. 提交并推送当前任务分支；不得合并 `main`。最终报告列出 release、基线、提交、推送、命令/退出码、bundle 路径、指标、科学分类和风险。
