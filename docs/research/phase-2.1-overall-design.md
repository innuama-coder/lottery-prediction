# Phase 2.1 总体设计

版本：2.1.0
运行标签：`P2.1-R00`
不可变 release：`P2.1-R00-60d02be4dbe9-i06`

## 目标与边界

Phase 2.1 是对 Phase 2 统计与验收缺陷的版本化修复。它不原地修改 `artifacts/phase-2/`，不改 Phase 1 冻结输入，也不进入模型、特征、号码排名或投注实现。交付状态与科学分类分开：证据链可以 `GO`，而科学分类仍可为 `indeterminate`；后者只表示当前注册检验和功效不能形成更强结论，绝不表示证明随机。

## 不可变身份与证据流

本轮 release ID 在原始 release 身份后追加 iteration-06 标记：`P2.1-R00-60d02be4dbe9-i06`。它与第六轮基线 `5e1aa70…`、rejected evidence `P2.1-R00-60d02be4dbe9` 及旧 iteration bundle 的关系固定在合同中，旧 release 不覆盖、不修复。readiness 固化 Phase 2.1 与实际导入的完整 Phase 2 运行时代码、Schema/合同摘要、Phase 1 输入、2.1 补充预注册、七个任务输入及预期哈希、隔离目录、依赖锁、wheelhouse 清单、benchmark、正式输出白名单和证据回传 canary。完成 bundle 的公开 readiness 命令使用独立只读路径，从底层重算身份、正式历史结果计数和输出白名单，且不创建或覆盖正式命令 receipt。

负向验证使用独立 staging 副本及与真实 power 命令 receipt、power identity、冻结输入和源码清单绑定的只读基准证据。各负向用例复用该基准并在进入昂贵网格重算前完成 Schema、身份、目录闭包和 receipt 一致性拒绝；正式 final validator 不接受隐式缓存，仍从冻结语料完整重算 240 个 power cells。

readiness 扫描当前 release、任务输入结果目录和按当前 release ID 分区的受保护结果目录，按 JSON 的 `release_id` 与正式 artifact type 计算历史正式结果数，再与合同中的 0 交叉验证。旧 rejected release 不计作新 identity 的前置结果。资源仍只记录事实。

最终 manifest 对实际目录做精确枚举并记录 SHA-256；唯一固定排除是 `acceptance/manifest.json` 与 `acceptance/acceptance.json`。新增未登记文件、缺失文件、重复路径或哈希变化都失败。manifest 自身由规范化 inventory digest 校验，acceptance 则由只读 final validator 从底层 evidence 逐字段重新推导后比较，因此这两个循环排除项不能成为旁路。

输入、预注册和 manifest 的验证是可调用生产路径。E2E 02、06、09 在临时隔离副本中修改真实文件，再调用这些路径并保存 Schema 合法、非零退出码的 verification receipt。正常链同样调用生产 runtime verifier，而不是手工构造 PASS。

## 资源预检

资源预检只记录系统、架构、逻辑 CPU 数、总内存、可用磁盘、解释器和锁定依赖等事实。任务发起人负责为已批准工作负载保障 VPS 容量；执行者记录事实和 benchmark。Phase 2.1 没有通用架构、CPU、内存或磁盘数值门槛，也不会仅因这些快照值产生 `HOLD`、`ENVIRONMENT_FAILURE` 或 `NEEDS_INPUT`。

只有实际命令、benchmark、wheelhouse、测试或正式运行出现可复现错误（例如 wheel 缺失、分配失败、进程被 OOM 终止或磁盘写入失败）时，才按观察到的真实错误分类。不得从资源快照虚构失败。

## 统计修复

原 temporal 备择在前后半段使用两个常数概率，实质是阶跃。2.1 的 `slow_drift` 在每个按日历排序的开奖位置使用不同的线性概率，并精确缩放为注册的“前半平均入选概率减后半平均入选概率”。旧阶跃仅保留为敏感性概念，不进入 2.1 正式 slow-drift 功效格。

DLT、SSQ 各保留五个主决策：边际入选、集合结构、对子依赖、慢漂移和跨区依赖。Holm 校正仍覆盖十个主决策。本轮 historical audit 从冻结 draws 和 reference-null corpus 重算所有统计量、p 值、Holm、敏感性与负对照；不复制旧 audit。power 与独立 replay 分别使用已注册但不同的 seed，对全部五个 family 和 240 个格点完整模拟，不复制旧 Phase 2 power/replay 行。每行记录本轮 seed、参数和生成器；upstream Phase 2 仅提供规则/校准映射和冻结 null/evaluation corpora。

## 独立复核

方法复核是结果前的独立进程检查，只读取合同与预注册。重放复核使用不同 seed；历史确定性效应由单独参考实现重算，未调用主统计入口。这里的独立性是程序化过程独立，不宣称组织或外部人工审计。
