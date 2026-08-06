# Phase 2.1 VPS 运行手册

## 容量责任与预检语义

任务发起人负责为批准的工作负载保障 VPS 容量。执行者必须记录系统、架构、CPU、内存、磁盘、Python、依赖和 benchmark，但不得用未在任务中特别授权的通用数值阈值阻塞运行。只有实际执行出现可复现的资源耗尽或环境错误，才报告真实终态与命令证据。

## 准备阶段（允许网络）

```bash
mkdir -p .phase2_1/wheelhouse
python3 -m pip download --requirement requirements/phase2_1.lock --dest .phase2_1/wheelhouse
python3 -m venv .phase2_1/venv
.phase2_1/venv/bin/python -m pip install --no-index --find-links .phase2_1/wheelhouse --requirement requirements/phase2_1.lock
.phase2_1/venv/bin/python -m pip install --no-index --find-links .phase2_1/wheelhouse --no-deps --no-build-isolation -e .
```

冻结的 Phase 2 null corpora 若在 clone 中表现为 Git LFS 指针，必须在准备阶段取得对象并核对指针 OID；不得改写仓库内历史指针。正式运行通过 `--lfs-root` 读取已核验的本地对象。

## 正式顺序（无公网依赖）

先设置 `PIP_NO_INDEX=1`，然后严格执行：

```bash
.phase2_1/venv/bin/python scripts/phase2_1/prepare_phase2_1.py --wheelhouse .phase2_1/wheelhouse --task-input-dir /path/to/task-input --corpus-root .phase2_1/lfs
python3 scripts/phase2_1/validate_phase2_1_readiness.py
.phase2_1/venv/bin/python -m lottery_research.phase2_1 gates
python3 scripts/phase2_1/independent_method_review.py
.phase2_1/venv/bin/python -m lottery_research.phase2_1 qualification
.phase2_1/venv/bin/python -m lottery_research.phase2_1 audit
.phase2_1/venv/bin/python -m lottery_research.phase2_1 power --lfs-root .phase2_1/lfs
.phase2_1/venv/bin/python -m lottery_research.phase2_1 replay --lfs-root .phase2_1/lfs
python3 scripts/phase2_1/independent_replay_review.py --lfs-root .phase2_1/lfs
python3 scripts/phase2_1/run_phase2_1_e2e.py
.phase2_1/venv/bin/python -m lottery_research.phase2_1 logs
.phase2_1/venv/bin/python -m lottery_research.phase2_1 negative-suite
.phase2_1/venv/bin/python -m lottery_research.phase2_1 manifest
.phase2_1/venv/bin/python -m lottery_research.phase2_1 accept
python3 scripts/phase2_1/validate_final_bundle.py
```

在 `gates` 的 G0/G1 未通过前，不得运行 qualification、audit 或 power。任何失败尝试必须保留在唯一的新运行目录；不得覆盖正式 release，也不得回写 `artifacts/phase-2/`。

## Build、lint 与回归

仓库使用 setuptools 的 `pyproject.toml`，没有独立 linter 配置。因此实际 build 是离线 wheel 构建，实际 lint 是对 Python 源、脚本和测试执行 `compileall` 加 `git diff --check`：

```bash
python3 -m pip wheel . --no-deps --no-build-isolation --wheel-dir .phase2_1/build-wheel-i05
python3 -m compileall -q src scripts tests && git diff --check
```

上述命令由 `logs` 在已激活隔离环境中真实执行；每条 receipt 记录原命令、开始/结束时间、stdout/stderr 摘要与哈希、真实退出码。预放置伪 receipt 会因不可覆盖写入而失败，任一未执行或非零命令使 logs 为 FAIL。正式结果之后不得再安装或下载依赖。命令日志进入 release 的 `logs/` 后才冻结递归 manifest。
