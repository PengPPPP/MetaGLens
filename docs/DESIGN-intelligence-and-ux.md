# MetaGLens CLI 智能化与交互体验升级设计

> 状态：设计稿，待评审。本文档只做方案设计，不含实现。
> 对标对象：`/home/h1020/MetaGLens-skills`（AI Agent 驱动的 skill bundle）
> 升级对象：`/home/h1020/MetaGLens`（确定性 CLI 软件）

---

## 0. 产品定位

**首要目标：让宏基因组分析的新手能独立跑完第一条完整流程。**

不追求商业化，不以支撑论文发表为设计目标。典型用户画像：

- 在实验室/院系的共享服务器上工作，**无权或无经费在服务器上引入计费型 agent**（这是软件版存在的直接原因——技术上装得上，但签批权与经费不在学生手上）
- 懂生物、不太懂 Linux 和生信工程；能敲命令，但读不懂 500 行 bash 报错
- 手上有几个到几十个样本，机器是单节点，不是集群
- **最怕的不是慢，是跑了 9 小时才发现一开始就配错了**

这个定位直接决定了优先级排序，和"给专家用的工具"完全不同：

| 对新手是刚需 | 对新手不重要 |
|---|---|
| 跑之前就拦住错误配置（`plan` / `doctor` / `db`） | SLURM/SGE 集群适配 |
| 出错时说人话，并给出下一步命令 | 千样本级吞吐、云端调度 |
| 解释每个参数在生物学上意味着什么（`explain`） | BibTeX / docx 导出 |
| 装完立刻能跑通的 demo | webhook / 邮件通知 |
| 指标异常时主动提示"这不正常"（`gate`） | 导出到 Snakemake/Nextflow |
| 中文交互 | 高级用户的极致可定制 |

**推论：本方案的重心是"防止用户踩坑"和"踩坑后能自己爬出来"，而不是"跑得更快更大规模"。** 第 8 章的排期已按此调整；第 6 章的 AI 层因此暂不实施。

---

## 1. 现状诊断：差距到底在哪

先做了一次逐文件对比。结论很明确：

**技术层已经对齐。** 14 个 shell 模板逐字节比对，13 个完全相同，只有 `00_setup.sh` 有差异。也就是说"能跑出什么科学结果"这件事，软件版已经 100% 继承了 skill 版的能力。

**差距 100% 在编排层。** skill 版之所以"聪明"，不是因为模板写得好，而是因为它背后有一个会**观察、推理、决策、复盘**的 agent。把这些行为拆开看：

| skill 版的智能行为 | 出处 | 软件版现状 |
|---|---|---|
| 跑 `conda env list`，逐工具查版本，出 installed/missing/outdated 表 | SKILL.md Step 0-2 | `conda_env.py` 有 API，但只在向导里打印一行 missing 列表 |
| 结合样本数与总线程数**提出多个并行方案让用户选** | SKILL.md Step 0-1b | 一行 `min(n_samples, total_threads)`，无解释、无备选 |
| 展示样本清单，请用户确认配对、指定排除 | SKILL.md Step 0-3 | 只能整体 Yes / No，No 就退化成"自己给 manifest" |
| 每阶段生成脚本后**展示脚本 + Methods 段落，等确认再推进** | SKILL.md 阶段工作流 10 步 | 一次性全部渲染，无预览、无逐阶段确认 |
| 监控进程到终态，解析日志，报告证据 | execution-monitoring.md | `subprocess.run` 阻塞 + 一个转圈动画，无进度、无日志、无资源观测 |
| 失败后按 script defect / environment / data-config **三类归因** | execution-monitoring.md | 只有一行 "failed (exit N). Check reports/logs/." |
| 有界自愈：最多 2 次，只修脚本缺陷，科学参数绝不静默改，全程留证据 | execution-monitoring.md | 无 |
| 从真实 `tool_versions.txt` 生成过去时 Methods + 只引用实际执行的阶段 | SKILL.md Logging and Methods | 只是 `cat` bash 产出的 methods.md |
| 数据库 200GB 的风险解释 + 显式授权 | SKILL.md Step 0-1 | 一个 `download_dbs: bool` 直接传给 bash |
| 产物验证通过才标 completed | 状态契约第 6 条 | 只看 shell 有没有写 status，Python 侧不复核 |

一句话总结：**软件版是一个正确但沉默的执行器；skill 版是一个会解释、会预警、会兜底的合作者。** 升级的目标不是加功能，是把 agent 的那套"观察—判断—解释"回路用**确定性的规则引擎 + 可观测性**重新实现出来。

同时必须承认一个事实：**没有 LLM 的确定性软件，永远无法复刻 agent 的开放式推理。** 所以本方案的策略是：把 agent 行为中**可规则化的 80%** 沉淀成引擎（离线、可测、可审计），剩下 20% 开放式诊断留给**可选、默认关闭**的 AI 顾问层（第 6 章）。这是本设计最关键的取舍。

---

## 2. 设计原则

这几条是后面所有取舍的依据，评审时如果对某个具体设计有异议，请回到这里看是否原则冲突。

1. **新手优先。** 任何一处"对专家更灵活"与"对新手更易懂"冲突时，选后者。新增选项前先问：新手看到这个选项会不会更困惑？能不能改成一条带解释的推荐值？
2. **离线优先，无 AI 也完整。** 全部核心能力（体检、推荐、监控、归因、门禁）必须在完全断网、无 API Key 的环境下可用。目标用户就在**不被允许引入计费型 agent** 的服务器上——这是制度与经费约束，比技术障碍更刚性、绕不过去。因此"零密钥 / 零网络出口 / 零单次费用"不只是实现偏好，而是**准入前提**，也是**可对外陈述的产品特性**：它正是学生用来向导师申请部署许可的论据。任何"付费后更好用"的设计都会把核心用户推成二等公民。
3. **知识外置，规则可读。** 推荐规则、失败特征、质量阈值一律放 YAML，生物信息工程师能直接改，不必碰 Python。这也是"智能"能持续积累的唯一方式——硬编码在代码里的知识不会有人维护。
4. **解释权高于自动化。** 每一个自动决策都必须能回答"为什么这么定"。宁可多问一句，不要静默替用户做科学决策。对新手来说，"知道自己在做什么"比"少按一次回车"重要得多。
5. **科学参数不自动改。** 继承 skill 的安全契约。自愈只允许动资源参数（线程/内存/重试）和明确的脚本缺陷，绝不动 `--min-contig-len` 这类影响结论的值。
6. **生成物始终可独立运行。** 这是 MetaGLens 相对 Snakemake/Nextflow 的核心卖点。任何新增智能都不能让脚本变得离不开 Python 运行时。
7. **交互与脚本化对等。** 向导里能问的每个问题，都必须有等价的 CLI flag。CI 里 `--yes` 一把过。
8. **交付物英文，交互层可中文。** 沿用 skill 的英文交付契约（run_log / methods / report 保持英文），但终端对话允许 `--lang zh`。

---

## 3. 目标架构

### 3.1 模块布局

现有 6 个模块职责清晰，全部保留。新增能力按"感知 / 决策 / 观测 / 表达"四层组织：

```
metaglens/
├── cli.py                  # 【改】命令注册表，瘦身为纯路由层
├── config.py               # 【改】加 fingerprint()、profile 合并
├── routes.py               # 保持
├── render.py               # 保持
├── pipeline.py             # 【改】执行逻辑收敛，cli 不再调私有函数
├── samples.py              # 【改】支持交互式排除 / 手工配对修正
├── report.py               # 【改】修 fastp glob bug，加 gates / repair 标签页
├── conda_setup.py          # 保持
├── conda_env.py            # 保持（doctor 复用）
│
├── sense/                  # ── 感知层：只读，回答"现在是什么情况"
│   ├── hardware.py         #    CPU / RAM / 磁盘 / 是否在容器 / 调度器可用性
│   ├── dataset.py          #    抽样读 FASTQ：读长、读数估算、编码、GC、文件体积
│   ├── doctor.py           #    工具 × 版本 × 环境矩阵
│   └── database.py         #    DB 注册表状态：存在 / 完整 / 版本 / 占用
│
├── decide/                 # ── 决策层：规则引擎，回答"应该怎么做"
│   ├── advisor.py          #    参数推荐（带理由与置信度）
│   ├── planner.py          #    并行方案 + 资源/时长/磁盘预估
│   ├── gates.py            #    阶段产出质量门禁
│   ├── diagnose.py         #    失败特征匹配 → 归因
│   ├── repair.py           #    有界自愈动作
│   └── rules/              #    ★ 知识库（YAML，可独立演进）
│       ├── advice.yaml
│       ├── failures.yaml
│       ├── gates.yaml
│       ├── estimates.yaml
│       └── databases.yaml  #    数据库 manifest（§4.7.3）
│
├── observe/                # ── 观测层：运行时可见性
│   ├── monitor.py          #    进程/作业生命周期 → 终态
│   ├── progress/           #    各工具日志进度解析器
│   └── resources.py        #    采样 CPU/RSS/IO
│
├── express/                # ── 表达层：人机界面
│   ├── wizard.py           # 【改】分组、可回退、带推荐与解释
│   ├── dashboard.py        #    Rich Live 多面板运行时仪表盘
│   ├── explain.py          #    explain <stage|param|error> 知识查询
│   ├── methods.py          #    Methods / references 生成
│   └── i18n.py             #    交互层中英文
│
└── state.py                # 状态 + 指纹 + 产物级验证
```

**为什么分四层而不是平铺加十个文件**：因为这四层的**测试策略完全不同**。感知层需要 mock 系统调用；决策层是纯函数，最好测；观测层需要 mock 子进程；表达层基本只能靠快照测试。分层后每层能独立演进，也让"知识"（rules/）从"逻辑"（*.py）里剥离出来。

### 3.2 命令面

```
# ── 已有（保留 / 增强）
metaglens init          [--yes] [--from-profile] [--lang zh|en]
metaglens validate      [--json]
metaglens run           [--monitor] [--auto-repair N] [--strict-gates] [--confirm-each]
metaglens resume
metaglens status        [--json] [--watch]
metaglens report
metaglens methods       [--format md|txt]
metaglens routes
metaglens setup-env

# ── 新增
metaglens doctor        [--env NAME] [--fix]        # 环境体检
metaglens db            list|scan|get|verify|where|use # 数据库生命周期（§4.7）
metaglens plan          [--json]                     # 执行计划 + 资源/时长/磁盘预估
metaglens recommend     [--apply] [--explain]        # 参数推荐
metaglens gate          [--stage ID] [--strict]      # 质量门禁报告
metaglens watch                                      # 附着到在跑的流程
metaglens explain       <stage|param|error-code>     # 知识查询
metaglens demo                                       # 迷你数据集端到端自检
```

---

## 4. 智能化：七个核心引擎

### 4.1 环境体检 `metaglens doctor`

对应 skill 的 Step 0-2，把已有的 `conda_env.py` 从"向导里的一行提示"提升为一等命令。

扫描四个维度：conda 环境 × 18 个工具的版本矩阵、可执行文件在 PATH 上的实际可用性（`conda list` 有包 ≠ 命令能跑）、数据库就位情况、硬件余量。输出一张分组表格：

```
Tools — env: proj_qc
  fastp        0.23.4   ✓
  megahit      1.2.9    ✓
  bwa-mem2     —        ✗ missing   (align_tool=bowtie2, 当前路线不需要)
  spades       3.15.5   ⚠ 3.15 已知在低内存下崩溃，建议 ≥3.15.5

Databases
  checkm2      ✓  1.0.1   2.9 GB
  gtdbtk       ✗  未找到  → metaglens db get gtdbtk  (预计 110 GB / 约 2h)

Hardware
  cores 32 / RAM 128 GB / free disk 4.2 TB
  ⚠ metaSPAdes 在 128 GB 上处理 >100M reads 有 OOM 风险
```

关键设计：**区分"路线需要"与"全部工具"**。当前路线用不到的工具缺失只提示不报错——现状的 `missing_tools()` 检查全部 18 个工具，对只跑 contig_based 的用户会产生大量噪音。`--fix` 只做安全动作（补装缺失工具），永不升级已有包（沿用 skill "不做 `conda update --all`" 的约束）。

### 4.2 参数推荐 `metaglens recommend`

这是**最能体现"智能"的部分**，也是当前差距最大的地方。现在所有默认值都是静态常量，不看数据、不看硬件。

**信号采集**（sense 层）：样本数、平均读数（按文件体积 + 抽样前 4000 行估算，不全量解压）、读长、质量编码、总核数、总内存、可用磁盘、是否有调度器。

**规则外置**，`rules/advice.yaml` 形态：

```yaml
- id: assembler.metaspades_needs_ram
  when: { assembler: metaspades, ram_gb: "<128", reads_per_sample: ">80e6" }
  advise: { assembler: megahit }
  severity: warn
  reason: >-
    metaSPAdes on {ram_gb} GB with ~{reads_per_sample} reads/sample frequently
    OOMs. MEGAHIT is markedly more memory-frugal at comparable contiguity for
    complex communities.
  citation: refs:megahit

- id: parallel.oversubscribed
  when: { threads_per_job: "<4", stage: "02_assembly" }
  advise: { parallel_jobs: "ceil(total_threads/4)" }
  severity: warn
  reason: Assemblers scale poorly below 4 threads; I/O contention dominates.
```

**输出必须带理由和置信度**，这是原则 4 的落地：

```
Recommendations  (7 samples · ~42M reads/sample · 2×150bp · 32c/128G)

  assembler          megahit      (当前 megahit)     ✓ 一致
  parallel_jobs      4            (当前 7)           ⚠ 建议改
      └ 7 jobs × 4 threads = 28，但 02_assembly 单样本峰值内存约 24 GB，
        7 并发需 ~168 GB > 可用 128 GB。4 × 8 更稳且总耗时相近。
  min_contig_len     1000         (当前 1000)        ✓ 一致
  taxonomy_tool      gtdbtk       (当前 gtdbtk)      ✓ 一致，但 DB 未就位

--apply 写回 metaglens.yaml（会先展示 diff）
```

`--apply` 前必须展示 YAML diff 并确认。**推荐引擎永不静默改配置。**

### 4.3 执行计划 `metaglens plan`

回答用户在按下 `run` 之前最关心的三个问题：**要跑多久、要占多少磁盘、会跑哪些命令**。现在这三个问题一个都答不了。

```
Plan — demo · mag_per_sample · 7 samples · local 4×8

  Stage           Mode         Est. time    Peak RAM   Disk Δ    Gate
  01_qc           7 par        ~25 min      4 GB       +38 GB    retention ≥70%
  02_assembly     7 par        ~3.5 h       24 GB/job  +12 GB    N50 ≥1kb
  03_mapping      7 par        ~55 min      8 GB       +64 GB    mapped ≥80%
  04_binning      7 par        ~1.2 h       16 GB      +9 GB     bins ≥1/sample
  05_checkm       single       ~40 min      12 GB      +2 GB     —
  ...
  ─────────────────────────────────────────────────────────────────
  Total                        ~9.5 h                  +196 GB
  Free disk 4.2 TB — OK.  Peak RAM 4×24=96 GB / 128 GB — OK (75%).

⚠ 07_taxonomy 需要 gtdbtk 数据库（未就位，110 GB）。
  未准备时该阶段会失败。→ metaglens db get gtdbtk
```

时长模型放 `rules/estimates.yaml`，形如 `per_gbp_per_thread` 系数，按工具和样本规模插值。**必须标注为粗估**（±50%）——给一个有误差标注的量级判断远好于什么都不说，但假装精确会损害信任。

#### 跨字段一致性校验

`plan` 除了估算资源，还承担一类现在**完全没有**的职责：**组合合法性检查**。

`config.validate()` 目前只做逐字段校验（枚举值、非空、目录存在）。但真实的配置错误往往出在**字段组合**上，单看每个字段都合法。典型例：

| 组合 | 单字段看 | 实际后果 |
|---|---|---|
| `analysis_basis=contig` + 含 `10_community` + `contig_taxonomy=none` | 三个都合法 | 阶段 10 无任何 taxonomy 输入可用（详见 §7-8） |
| `read_profiling=kraken2` + kraken2 库为 standard + RAM 64 GB | 都合法 | 索引载不进内存，OOM（§4.7.6 ①） |
| `assembler=metaspades` + 高并发 + 内存不足 | 都合法 | OOM（§4.2 已有规则） |

这类规则应当集中成 `validate` / `plan` 的一类独立检查，而不是散落在各阶段脚本里各自判断。放在 `plan` 而非运行时的理由很直接：**这些错误全部可以在开跑前零成本查出来**，让它们跑到第 10 阶段才暴露是纯粹的浪费。

#### 可粘贴的纯文本摘要（P1 功能需求）

`plan` 的输出除了终端里的彩色表格，**必须**再提供一份纯文本 / 可导出形式（`--format text` 或 `> plan.txt`）。这不是排版细节，而是一个独立用途：

- **资源申请依据。** 学生向导师或服务器管理方申请配额时，需要一份能直接粘进邮件的磁盘/时长/内存预估。彩色 Rich 表格粘出去是乱码。
- **零计费证明。** 摘要应显式声明本流程**零密钥、零网络出口（数据库下载除外且可离线预置）、零单次费用**——这正是原则 2 里"用来说服导师的论据"的落地载体。一份纸面证明比口头保证有用。

因此摘要需包含：项目/路线/样本数、逐阶段资源与总量、所需数据库及其就位状态、以及一行明确的"运行时不产生任何按次计费或外部 API 调用"声明。**计入 P1 `plan` 的工作量，不是文档措辞。**

### 4.4 质量门禁 `metaglens gate`

skill 契约里"never report a stage as completed without validating its expected outputs"这条，软件版目前**完全没有实现**——Python 侧只读 shell 写的 status 标志位，不复核任何产物。这是当前最实质的可靠性缺口。

补两层：

**产物验证**（硬门禁，state.py）：期望文件存在、非空、格式可解析。不通过则该阶段不得标 completed，即使 shell 说成功了。

**一个真实实例（见 §7-8）**：`10_community_summary.sh` 在没有任何 taxonomy 输入时，会写出一张
只有表头的群落矩阵，然后 **exit 0 + 标记 completed + run_log 写"✅ Completion"**。
`:296` 明明算出了 `NUM_TAXA`，却只写进日志、从不校验。

这说明产物验证不能停在"文件存在且非空"——**表头本身就让文件非空**。必须做到语义层：
`community_matrix.tsv` 要求 ≥ 1 个数据行、`quality_report.tsv` 要求 ≥ 1 个 bin、
`dereplicated_genomes/` 要求 ≥ 1 个 FASTA。每个阶段的期望产物都要写明**可判定的**下界。

**科学指标门禁**（软门禁，gates.yaml）：

```yaml
01_qc:
  - metric: retention_rate
    warn_below: 70
    block_below: 40
    hint: 保留率过低通常指向接头污染或 min_length 设置过严。
04_binning:
  - metric: bins_per_sample
    warn_below: 1
    hint: 未产出 bin，常见原因是 contig 太碎（检查 02 的 N50）或测序深度不足。
05_checkm:
  - metric: mimag_hq_count
    warn_below: 1
    hint: 无高质量 MAG（≥90% 完整度 / ≤5% 污染）。可考虑加深测序或改用联合分箱。
```

默认 warn 只提示不中断；`--strict-gates` 时 warn 也阻断。门禁结果写入 `pipeline_status.json` 并在 report.html 里作为独立标签页呈现。

### 4.5 失败归因 `decide/diagnose.py`

复刻 execution-monitoring.md 的三类归因。规则库 `rules/failures.yaml`：

```yaml
- id: oom.killed
  match: { exit_code: 137 }
  class: environment
  title: 进程被 OOM killer 终止
  diagnosis: 该阶段峰值内存超出可用值。
  actions:
    - { kind: auto,  op: reduce_parallel, factor: 0.5, safe: true }
    - { kind: human, text: "或增加 memory: 再重跑该阶段" }

- id: gtdbtk.db_missing
  match: { log_regex: "GTDBTK_DATA_PATH.*not set|Cannot find.*gtdbtk" }
  class: environment
  title: GTDB-Tk 数据库未配置
  actions:
    - { kind: human, text: "metaglens db get gtdbtk（约 110 GB）" }

- id: glob.empty
  match: { log_regex: "No such file or directory.*\\*" }
  class: script_defect
  title: 通配符未匹配到文件
  diagnosis: 上游阶段可能产出为空。
  actions:
    - { kind: human, text: "先跑 metaglens gate --stage <上游> 定位空产出" }
```

失败时输出三段式，而不是现在的一行 `exit 2`：

```
✗ 02_assembly failed  (attempt 1/3, exit 137, 2h14m)

  归因   environment — 进程被 OOM killer 终止
  证据   megahit 峰值 RSS 27.3 GB × 7 并发 ≈ 191 GB > 128 GB
         log: reports/logs/02_assembly.log:1893
  下一步 [1] 自动降并发到 4×8 重跑该阶段   ← 安全，仅改资源参数
         [2] 我自己改 metaglens.yaml
         [3] 只看证据，先不动
```

### 4.6 有界自愈 `decide/repair.py`

严格照搬 skill 的安全边界，这是**不可协商**的部分：

- 上限 2 次（`--auto-repair N` 可调，`0` 关闭）
- **只允许**降并发、降线程、加内存请求、重试瞬时错误
- **禁止**改任何科学参数、改输入、动环境、动数据库、删非空产物
- 每次尝试前保存脚本快照到 `reports/repairs/{stage}/attempt-N/`
- 每次追加一条 JSON 到 `reports/repair_log.jsonl`（诊断、改动、验证命令、结果）
- 同一失败特征重复出现即停止，绝不进入无界循环
- 只重跑失败阶段，不碰上游

对比现状：一次失败就整体退出，用户只能自己读日志。有界自愈能把"半夜跑挂"从"损失一整晚"降到"损失一个阶段"。

### 4.7 数据库生命周期 `metaglens db`

对新手而言，**参考数据库是整条流程上最高的一道墙**：几百 GB、下几小时、容易半路断、断了不知道怎么续、下完不知道对不对、而且往往跑到第 7 阶段才发现没下。这一节单独成节，因为它是 §10 场景 1 的正面解法。

#### 4.7.1 现状问题（均有代码位置）

| # | 问题 | 位置 | 后果 |
|---|---|---|---|
| 1 | 下载失败静默继续 | `00_setup.sh` 四处 `\|\| log "⚠️ ..."` | 下载失败但 `00_setup` 仍标 completed，9 小时后才撞墙 |
| 2 | 用前不检查 | `05_bin_evaluation.sh:76`、`07_taxonomy.sh:72` | 直接把不存在的路径喂给 CheckM2 / GTDB-Tk（仅 `08_annotation.sh:118` 做了检查） |
| 3 | 一个布尔管四个库 | `config.download_dbs` | `contig_based` 路线不需要 CheckM2/GTDB，仍会一起下 110 GB |
| 4 | 「已存在」靠目录非空 | `00_setup.sh:584` `-z "$(ls -A ...)"` | 中断留下的半个 tar.gz 会被判为"已就位"；无校验和、无 sentinel、无版本 |
| 5 | Kraken2 是本地构建 | `00_setup.sh:605` `kraken2-build --standard` | 几小时 CPU + 稳定 NCBI 连接 + 大量中间文件，对新手近乎劝退 |

#### 4.7.2 核心主张：数据库是跨项目资产，不是项目内的一个步骤

把下载塞进 `00_setup.sh` 是根上的设计错误，因为数据库的生命周期与项目不同：

- 一台服务器上 10 个项目应当**共用一份** GTDB，现状是每个项目 `db_dir` 各存一份
- 项目目录会被删，数据库要长期保留
- 下载失败是**环境问题**，不该表现为"某个项目的 setup 失败"

**结论**：抽成独立的 `metaglens db` 命令 + 用户级注册表，项目只**引用**不拥有。`00_setup.sh` 中只保留校验，不再下载。

#### 4.7.3 注册表设计

两层：`decide/rules/databases.yaml`（内置 manifest，随软件版本演进）+ `~/.config/metaglens/databases.yaml`（本机安装记录，记 path/version/installed_at）。

```yaml
checkm2:
  required_by: [05_checkm]
  install:  { kind: tool_command, command: "checkm2 database --download --path {dest}" }
  sentinel: "CheckM2_database/uniref100.KO.1.dmnd"
  env_var:  CHECKM2DB

gtdbtk:
  required_by: [07_taxonomy]
  when:     { mag_taxonomy: gtdbtk }        # 键名依 4.7.9-A 裁决
  sentinel: "taxonomy/gtdb_taxonomy.tsv"
  version_from: "metadata/metadata.txt:VERSION_DATA"   # 见 4.7.6 ③
  env_var:  GTDBTK_DATA_PATH
  note:     版本号（r207/r214/r232…）直接影响分类结果，必须记录

kraken2:
  required_by: [07_taxonomy, 09_contig]
  when:     { any: [{read_profiling: kraken2}, {contig_taxonomy: kraken2}] }
  variants:                       # ← 由可用内存驱动选择，见 4.7.6
    standard-8:  { ram_min_gb: 12 }
    standard-16: { ram_min_gb: 20 }
    standard:    { ram_min_gb: 110 }
  sentinel: "hash.k2d"
  env_var:  KRAKEN2_DB_PATH
```

注意 `mag_taxonomy`（MAG 分类）/ `read_profiling`（读段谱）/ `contig_taxonomy`（contig 分类）是**三个正交维度**，三者都可能独立要求 kraken2 或 gtdbtk。`when` 用 `any` 组合即可覆盖。

**体积与版本号一律不凭记忆写死。** 实现时必须从官方文档取准确值填入 registry，并由 `db verify` 与实际占用对账。给一个错的精确数字比给量级判断更糟。

#### 4.7.4 按需推导，取代布尔开关

新增 `required_databases(cfg) -> {name: reason}`，从路线 + 配置开关推导：

```
mag_per_sample + taxonomy_tool=gtdbtk + use_eggnog=true  → checkm2, gtdbtk, eggnog
contig_based   + contig_taxonomy=none                    → eggnog            # 只需 1 个
```

这与 P1 的 `required_tools(cfg)` 是**孪生函数**，应当一起实现——两者共用同一份"路线+开关 → 实际依赖"的推导逻辑，也共同支撑 `doctor` / `plan` / `recommend` 三个命令。

#### 4.7.5 四态判定与显式失败

状态从二态（有目录/没目录）升级为四态：

| 状态 | 判定 | 处置 |
|---|---|---|
| `missing` | sentinel 不存在 | `plan` 拦截，给出可复制的下载命令 |
| `partial` | 有残留但 sentinel 缺失/体积异常 | 提示可续传，**不**误判为就位 |
| `ready` | sentinel 存在且校验通过 | 放行 |
| `stale` | 版本低于 registry 记录 | 只提示，不强制升级（换版本会改变分类结果） |

**去掉 `|| log ⚠️` 继续的模式。** 数据库未就位由 `plan` / preflight 在**开跑之前**拦下——这是本节存在的全部意义。

#### 4.7.6 三个容易被漏掉的约束

**① Kraken2 的瓶颈是内存，不是磁盘。** kraken2 需要把索引**整个载入 RAM**。standard 索引接近 100 GB，意味着 64 GB 的机器**根本跑不了**，表现为 OOM 而不是变慢。现状软件里完全没有这个概念。因此 kraken2 变体选择必须由**可用内存**驱动（`variants.*.ram_min_gb`），这条直接接入 4.2 的 advisor 引擎。

**② 磁盘预检要算峰值，不是终值。** 现状 `00_setup.sh:618` 是「下 tar.gz → 解压 → 删压缩包」，峰值约为终值的 **2 倍**。用户看到 150 GB 空闲以为够跑 GTDB，结果解压到一半炸。预检必须按 `压缩包 + 解压后` 计算。可选优化：`curl | tar -xz` 流式解压能把峰值降到 1 倍，代价是不能续传——磁盘紧张时提供 `--stream`，默认仍用可续传的两步法。

**③ 发现链必须包含"文件系统扫描"，这是投入产出比最高的一项。** `metaglens db scan` 分三级：

1. **环境变量**：`GTDBTK_DATA_PATH` / `CHECKM2DB` / `KRAKEN2_DB_PATH` / `EGGNOG_DATA_DIR`
2. **文件系统扫描**：`~`、`/opt`、`/shared`、`/data` 等常见位置下按 sentinel 文件特征识别
3. **询问用户**（4.7.8 ②）

**第 2 级不能省，本机实测为证：**

```
$ ls -d ~/gtdbtk_data/*        → /home/h1020/gtdbtk_data/release232
$ du -sh ~/gtdbtk_data         → 94G                 ← 库早就在了
$ echo $GTDBTK_DATA_PATH       → <unset>
$ echo $CHECKM2DB              → <unset>
$ echo $KRAKEN2_DB_PATH        → <unset>
$ echo $EGGNOG_DATA_DIR        → <unset>             ← 四个全未设置
```

只靠环境变量会**完全错过**这个 94 GB 的库，然后建议用户再下一份。

**这条实测把 4.7.9-A 的问题重新定性了**：所谓"GTDB 110 GB 硬门槛"在真实服务器上**常常并不成立**——库往往已经存在，只是没人设环境变量。因此优化重点应当从"降低默认科学标准换上手容易"**转向"把发现做好"**。这也正是原则 5（科学参数不自动改）想要的结果：不必牺牲科学标准，把工程做扎实即可。

**版本号从元数据读，不猜目录名。** 实测 `~/gtdbtk_data/release232/metadata/metadata.txt` 内含 `VERSION_DATA=r232`，这是权威来源；目录名恰好也叫 `release232`，但依赖目录名命中是巧合，不能作为实现依据。sentinel 亦已由真实安装校准（`taxonomy/gtdb_taxonomy.tsv` 存在）。

#### 4.7.7 版本记录

DB 版本写入 `pipeline_status.json` 并进入 methods。skill 契约明确要求"Record database releases separately from software versions"，现状未实现。GTDB 的 release 号会实质影响分类结果，不记录等于结果不可复现。

#### 4.7.8 自定义与复用已有数据库（必须支持）

**这是目标用户的常态，不是边缘情况。** 院系共享服务器上，GTDB / Kraken2 / eggNOG 往往早就有人下好了；新手最该做的事是**指向它**，而不是再下 110 GB。

**现状缺口**：`config.py` 已有 `checkm2_db` / `taxonomy_db` / `eggnog_db` / `kraken2_db` 四个 override（留空则从 `db_dir` 推导），**但 `wizard.py` 从不询问它们**。有现成数据库的用户在向导里无处表达，只能跑完 `init` 再手改 YAML——而新手根本不知道有这几个键。这是能力已存在但入口缺失的典型。

**四条补齐措施**

**① 路径解析优先级必须明确定义**（现状是隐式的，只有 `render._db()` 一行 `override or db_dir/sub`）：

```
1. 配置里的显式路径     db_paths.gtdbtk: /shared/db/gtdb_r220
2. 用户注册表已登记项   ~/.config/metaglens/databases.yaml
3. 环境变量             GTDBTK_DATA_PATH / CHECKM2DB / KRAKEN2_DB_PATH / EGGNOG_DATA_DIR
4. 默认位置             {db_dir}/{name}
```

`metaglens db where <name>` 打印最终解析结果**及命中的是哪一级**——路径来源不透明是这类配置最常见的困惑源。路径统一做 `expanduser()` + `resolve()`。

**② 向导里逐库询问。** 只问 `required_databases(cfg)` 推出来的那几个，不问用不到的：

```
━━━ 5/5  数据库 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

本路线需要 3 个数据库。已自动扫描系统现有安装：

  checkm2   ✓ 已发现  $CHECKM2DB → /opt/db/checkm2          可直接用
  gtdbtk    ✗ 未发现                                         需要处理
  eggnog    ✓ 已发现  /shared/biodb/eggnog                    可直接用

 ? gtdbtk 怎么处理
   [1] 我已经有了，我来指定路径（推荐——服务器上很可能已存在）
   [2] 让 MetaGLens 下载（约 110 GB，数小时）
   [3] 暂时跳过 → 07_taxonomy 将无法运行，其余阶段不受影响

 > 1
 ? gtdbtk 数据库路径
 > /shared/biodb/gtdb/release220
   ✓ 校验通过：找到 taxonomy/gtdb_taxonomy.tsv，release r220，只读
```

选项 [3] 必须**明确说出代价**（哪个阶段跑不了、其余是否受影响），让新手能有依据地决定先跑通前面。

**③ 给出路径后立即校验，而不是等到该阶段。** 用 registry 里的 sentinel 文件当场判定，并回读版本号。写错路径的反馈应当是**当场一行**，而不是九小时后 GTDB-Tk 自己的报错——这是本节最主要的价值。校验失败时区分两种情况：目录不存在 / 目录存在但不像这个数据库（后者附上"期望找到 X 文件"）。

**④ 不得假设数据库目录可写。** 复用同事的目录时**通常没有写权限**。所有针对已有数据库的操作必须是只读的；`db verify` 不得写临时文件到 DB 目录；如某工具确实需要在 DB 目录内写入，必须提前检测权限并给出明确提示，而不是运行到一半失败。`db use` 只在注册表里**登记路径**，不复制、不软链、不改动原目录。

**配置键收敛。** 现有四个键命名不一致——`taxonomy_db` 依 `taxonomy_tool` 而指向 gtdbtk 或 kraken2，同时又另有一个 `kraken2_db` 给 contig 阶段用，很容易设成互相矛盾。建议收敛为与 registry 同名的映射：

```yaml
db_paths:
  gtdbtk:  /shared/biodb/gtdb/release220
  eggnog:  /shared/biodb/eggnog
  # 未列出的走默认解析
```

项目当前尚无 commit、无外部用户，是做这个不兼容改名的最佳时机；晚做成本只会上升。`from_yaml` 遇到旧键时报错并指明新写法。

#### 4.7.9 已裁决

**A. `taxonomy_tool` 是一个建模错误，拆成两个正交键。**

原以为这是"默认值选哪个"的问题，读模板后发现是**配置建模本身错了**。`07_taxonomy.sh` 两个分支做的是完全不同的事：

| 分支 | 输入 | 回答的问题 |
|---|---|---|
| `gtdbtk`（`:75`） | `--genome_dir ${INPUT_PATH}` = 去冗余后的 MAG | 我重建出的这些基因组**各是什么物种** |
| `kraken2`（`:102-116`） | `01_qc/${SAMPLE}_clean_R{1,2}.fastq.gz`，**完全不使用 `INPUT_PATH`** | 这个样本的**读段组成**是什么 |

两者输入不同、产出不同、科学问题不同，**不构成替代关系**。把它们塞进一个 either/or 的键有两个后果：

1. **选 `kraken2` 等于一个 MAG 分类都不做**，而用户以为自己只是"换了个分类工具"。
2. **连带静默降级**：`10_community_summary.sh:71-83` 的来源优先级是
   `bracken → mag_coverage → gtdbtk → contig_kraken → kraken_report`。
   选 kraken2 + Bracken 时会命中最前面的 `bracken`，于是
   `mag_coverage`（"MAG 丰度按 GTDB 分类聚合"，科学信息量最高的那一档）**永远到不了**——
   因为根本没有 GTDB 输出。交付报告的 taxonomy 段一并降级，且全程无任何提示。

   这正是原则 4/5 要防的"静默替用户做科学决策"，只不过这次是**配置建模把用户推下去的**，不是自愈逻辑。

**裁决**：拆为两个正交键，可并存。

```yaml
mag_taxonomy:    gtdbtk | none      # 作用于 06_derep 产出的 MAG
read_profiling:  kraken2 | none     # 作用于 01_qc 产出的清洗读段
```

附带修掉一个**现存功能缺失**：现在的 either/or 结构表达不出"两个都要"，而"既要 MAG 分类、又要读段谱"是常规需求。拆分后 `required_databases()` 也随之自然——两个键各自独立决定要不要 gtdbtk / kraken2。

**B. `db_dir` 默认位置：两个原倾向都不采纳，走第三条路。**

原倾向（改为 `~/.local/share/metaglens/databases`）被本机实测否掉：

```
$ df -h ~ /
/dev/rbd1  4.4T  3.4T  796G  82%  /          ← home 与 / 同一分区
$ quota -s
quota: command not found                      ← 无配额机制
```

风险性质变了但结论相同：不是"home 配额超限"，而是**默认往 home 塞 200 GB 会吃掉剩余空间的 1/4 并挤压根分区**。更直接的反证是用户的库实际在 `~/gtdbtk_data/`——**任何硬编码的新默认位置都只会造成"软件想往 A 装、库在 B"，进而重复下载。**

**裁决**：

1. **不预设可写的默认下载路径。** 发现链优先（见 4.7.6 ③），多数情况下根本用不到"默认下载位置"这个概念。
2. **确需下载时，`db get` 强制显式指定目标目录**，并当场校验该分区剩余空间 ≥ 体积 × 1.2（含 4.7.6 ② 的解压峰值），不足即拒绝，而不是装到一半炸。
3. **"每个项目重复配置"的问题**用 §5.3 已规划的用户级 profile 记忆解决——记住"上次把库放哪了"，而不是硬编码一个路径。
4. 若确实需要兜底默认，用 `$XDG_DATA_HOME` 而非硬编码 `~/.local/share`，且仍受第 2 条的空间检查约束。

---

## 5. 交互体验升级

### 5.1 向导重构

现在的 `wizard.py` 是 20 个问题的线性流：不能回退、不解释影响、不给推荐、答错只能 Ctrl-C 重来。改成**五组 + 组内可回退**：

```
━━━ 2/5  数据 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

发现 7 个配对样本（约定：_R1/_R2）
  1 SRR001  SRR001_R1.fastq.gz  4.2 GB    ~42M reads
  ...
  7 SRR007  SRR007_R1.fastq.gz  3.8 GB    ~38M reads
                                 共 28.4 GB · 2×150bp · Phred33

 ? 确认样本清单
   [1] 全部使用（推荐）
   [2] 排除部分样本
   [3] 改用我自己的 samples.tsv

 >  (回车接受推荐 · b 返回上一组 · ? 查看说明 · q 退出)
```

具体改动：
- **每组结尾给小结**，让用户知道自己刚决定了什么
- **`b` 回退**到上一组，`?` 触发 `explain` 查看当前问题的详细说明
- **回车即接受推荐值**，推荐值来自 4.2 的引擎而非静态常量
- **选项 [2] 排除部分样本**——现状缺这个能力，只能整体接受或自己写 manifest，而排除低质量样本是极常见的需求
- **结束前展示完整 YAML + 影响面摘要**（要跑哪些阶段 / 约多久 / 要多少磁盘 / 缺哪些 DB），再确认落盘
- **补 `reuse_and_update` 选项**（config 已支持该值，向导却没有，属现存不一致）
- **新增数据库分组**：逐个询问本路线实际需要的数据库，支持"我已经有了，指定路径"并当场校验（详见 §4.7.8）。现状向导完全不问 DB 路径，有现成数据库的用户无处表达

### 5.2 运行时仪表盘 `--monitor` / `metaglens watch`

现状是一个 spinner 加计时器，几小时里看不出任何进展。改成 Rich Live 多面板：

```
MetaGLens · demo · mag_per_sample                    elapsed 4h12m · ETA ~5h
─────────────────────────────────────────────────────────────────────────────
 ✓ 01_qc          ████████████████████  7/7      25m   retention 91.4% ✓
 ⟳ 02_assembly    ██████████░░░░░░░░░░  4/7    2h48m   ETA ~2h
      SRR001 ✓ 38m   SRR002 ✓ 41m   SRR003 ✓ 35m   SRR004 ✓ 39m
      SRR005 ⟳ k=99  SRR006 ⟳ k=79   SRR007 ⟳ k=79
 · 03_mapping     ░░░░░░░░░░░░░░░░░░░░  pending
─────────────────────────────────────────────────────────────────────────────
 CPU  ████████████████░░  81%      RAM  ██████████░░░  96/128 GB
 disk +71 GB this run · free 4.1 TB
─────────────────────────────────────────────────────────────────────────────
 02_assembly.log
   [17:41:02] --- [k=79] 3,201,884 vertices, 4,102,331 edges
   [17:43:18] --- [k=99] assembling contigs from SdBG...
─────────────────────────────────────────────────────────────────────────────
 q 退出监控（流程继续） · l 切换日志 · p 暂停后续阶段
```

技术要点：
- `observe/progress/` 下每个工具一个解析器（fastp 的样本计数、MEGAHIT 的 `k=` 行、bowtie2 的百分比、prokka/GTDB-Tk 的分片计数）。**解析失败必须优雅降级**为不定进度 + 日志 mtime 心跳——skill 明确指出"quiet log 不代表卡死"，组装器可以几十分钟不输出，不能因此误判。
- `metaglens watch` 独立进程附着到在跑的流程（读 status + log tail），这样 tmux 里跑、另一个窗口看，符合 README 里推荐的长任务用法。
- `q` 只退出监控界面，不杀进程——这个语义必须做对，否则用户会误杀几小时的活。

### 5.3 其它交互改进

**错误信息三段式。** 全局统一：发生了什么 / 为什么 / 下一步敲什么命令。现状 `_fail()` 只给第一段。

**拼写纠错。** `--only 04_bining` 现在报"not in route"并列出全部步骤；改成 `difflib` 给出"你是不是想输入 04_binning?"。同样适用于 route 名和 config key（`from_yaml` 现在对未知 key 直接抛异常，不给建议）。

**用户级 profile。** `~/.config/metaglens/profile.yaml` 记住上次的线程数、DB 目录、conda 前缀、语言，下次作为默认。跑第二个项目时不必重答硬件相关的问题。

**`--json` 全覆盖。** `status` / `plan` / `doctor` / `gate` / `validate` 都支持，方便接 CI 或外部平台。

**`metaglens explain`。** 把 skill 的领域知识做成可查询知识库：`explain 04_binning` 讲这阶段在干什么、常见坑；`explain completeness_min` 讲阈值的科学含义与 MIMAG 标准；`explain oom.killed` 讲怎么处理。这是把 skill 里散落在 9 个 SKILL.md 中的知识**资产化**——否则这些知识随 skill 版一起被丢掉了。

**`metaglens demo`。** 内置极小合成数据集（几千条 reads），几分钟跑完全流程。装完立刻自检，也是最好的 CI 冒烟测试和教学材料。

**i18n。** 交互层 `--lang zh|en`，对国内新手是刚需而非锦上添花——读不懂英文提示等于没有提示。`run_log.md` / `methods.md` / `report.html` 保持英文（沿用 skill 的英文交付契约，原则 8）。

### 5.4 Methods 生成（低优先级）

现状 `methods` 命令只是 `cat` bash 产出的文件。因为不以论文发表为目标，这一块**只做最小必要的修正**，砍掉 BibTeX / docx 导出。

保留的核心诉求只有一个：**版本号必须真实**。Python 侧同时掌握"配置里选了什么"和"`tool_versions.txt` 里实际装的是什么"，所以生成逻辑应该在 Python：

- 只写**实际执行**的阶段（读 status 的 selected_steps + completed），不提没跑的分支
- 用**真实版本号**，缺失时标 `[provisional]` 并提示补
- 过去时、参数完整

对新手的价值不在于"能投稿"，而在于**它是一份自动生成的、说明"我到底做了什么"的记录**——新手往往过几周就忘了自己当时设了什么参数。

---

## 6. 关于 AI 顾问层：暂不实施

**决策：不做。** 从架构和排期中移除 `ai.py`、`metaglens ask`、`--ai-diagnose`。

理由与产品定位直接相关：

1. **目标用户就是不被允许装计费型 agent 的人。** 软件版存在的全部理由就是"在**不被允许引入计费型 agent** 的服务器上跑通流程"——卡点是导师/管理方的计费审批，不是技术。给它加一个需要联网和 API Key 的功能，等于在核心用户面前放一个他们**制度上不得使用**的入口——反而制造困惑（违反原则 1）。这条约束比技术障碍更刚性、绕不过去，因此"不做 AI 层"的结论只会更稳，不会更松。
2. **不商业化，就没有维护它的动力。** AI 层需要持续跟进 endpoint 变更、脱敏策略、prompt 调优。一个不追求商业化的工具养不起这份长期成本。
3. **与可复现性有张力。** 核心价值是可复现，而 LLM 输出不可复现。新手更容易把 AI 的猜测当成结论。
4. **规则库已经覆盖了新手会遇到的绝大多数情况。** 新手踩的坑高度集中（DB 没下、内存不够、路径写错、样本没配对上），这些恰恰是规则引擎最擅长的部分。开放式推理主要在疑难杂症上有价值，而那不是新手场景。

**如果将来要复活这一层**，以下约束不可动摇（记录在此，避免届时重新讨论）：

- 默认关闭，未配置时全部功能无损可用
- 发送前必须展示将要发送的内容并确认
- 只发日志尾部 + 状态摘要；绝不发原始序列、绝对路径、主机名
- AI 不得直接改任何文件，只能产出 patch 建议
- AI 输出必须与规则库结论视觉区分，明确标注为"推测"

**替代方案：把知识写进 `explain` 知识库。** 与其让 AI 临场生成解释，不如把 skill 版 9 个 SKILL.md 里的领域知识固化成可查询、可校对、可离线使用的静态知识库（见 5.3）。对新手而言，一份准确的静态解释优于一段可能出错的动态生成——而且它顺带把 skill 版的知识资产保住了。

---

## 7. 技术债清理（P0，先于所有新功能）

上一轮 review 发现的问题，必须先清掉——在有 bug 的地基上加智能只会放大问题：

1. **`report.py:49` fastp glob 不匹配** — 匹配 `*.fastp.json`，模板写的是 `${SAMPLE}_fastp.json`，QC 标签页恒空。**这是用户可见的功能性 bug。**
2. **`count_fastq_reads` 冗余解压** — 为计数把每个 FASTQ 完整解压两遍，而 fastp JSON 里已有精确读数。7 样本 × 28 GB 白跑一遍 IO。
3. **`wizard.py` 缺 `reuse_and_update`** — config 校验接受该值，向导给不出，配置面不一致。
4. **`cli.py:250` 调私有 `pipeline._run_script`** — 绕过 `pipeline.run`，两处执行逻辑重复，加监控时会分叉。
5. **`samples.py:20` 用 `callable` 作类型标注** — 应为 `Callable[..., str]`。
6. **无 git 仓库 / 无测试 / 无 LICENSE 文件**（pyproject 声明 MIT）/ `__pycache__` 混在源码树。
7. **SBATCH 输出路径为相对路径** — 只在从 work_dir 提交时才正确。

> 1–7 项已于 2026-07-28 一轮施工中全部清空（含 `conda_env.py` 发现不稳、环境不存在不校验两条追加项），
> 施工与验证记录见 [`WORKLOG.md`](WORKLOG.md) §3。下列第 8 项为后续核查中新发现。

8. **`10_community_summary.sh:67` 用字面量数组冒充 glob** —— 新发现，**影响整条 `contig_based` 路线**。

   ```bash
   shopt -s nullglob
   GTDB_SUMMARIES=("${TAX_DIR}/gtdbtk/gtdbtk.bac120.summary.tsv" \
                   "${TAX_DIR}/gtdbtk/gtdbtk.ar53.summary.tsv")   # ← 无通配符
   ```

   `nullglob` 只对**含通配符**的模式生效，这两个元素是字面量字符串，因此
   `${#GTDB_SUMMARIES[@]}` **恒为 2**，与文件是否存在无关。同一 `nullglob` 作用域内实测：

   ```
   literal array count : 2   <-- 文件并不存在，期望 0
   glob array count    : 0
   ```

   连带后果（`10_community_summary.sh:71-84` 的来源选择链）：

   - `:76` `elif [[ ${#GTDB_SUMMARIES[@]} -gt 0 ]]` **恒真** → 只要走到这一行就选 `gtdbtk`
   - 因此 `:78` `contig_kraken` 与 `:80` `kraken_report` 两个分支是**死代码**，永不可达
   - `:82` 的 `else` 报错分支同样不可达
   - `:74` 的 `&& ${#GTDB_SUMMARIES[@]} -gt 0` 是空条件，实际只在判断 `-f MAG_RELABUND`

   **真实失败模式比"产出空矩阵"更糟。** `gtdbtk` 分支的内嵌 python（`:138-158`）对缺失文件
   走 `continue`，`counts` 保持为空，于是只写出表头 `taxon\tMAG_count` 零数据行，
   **退出码 0**。随后 `:296` 只是 `NUM_TAXA=$(( $(wc -l < "${MATRIX}") - 1 ))` 记进日志
   （`:298`），**从不校验 > 0**，紧接着 `:302` 就 `update_step_status ... completed`。

   最终形态：**空表 + exit 0 + 标记 completed + run_log 写"✅ Completion"**。
   这同时是 §4.4「产物验证」缺口的一个**真实实例**——所以光把字面量改成真 glob 不够，
   阶段 10 必须在 `NUM_TAXA == 0` 时拒绝标 completed。

   **全模板扫描结论：同类写法仅此一处。** `06_dereplication.sh:65` 的 `GENOMES` 与
   `08_annotation.sh:67` 的 `MAGS` 是多行数组赋值、通配符在后续行，写法正常（曾被
   只匹配赋值首行的扫描器误报）。

   **修复时必须一并决定的连带行为。** `config.contig_taxonomy` 默认为 `none`
   （`config.py:100`），而 `09_contig_analysis.sh:174` 只在 `== kraken2` 时才产出
   `taxonomy/*_contig_report.txt`。因此 `contig_based` 路线走默认配置时：

   | | 阶段 10 的结果 |
   |---|---|
   | 修复前 | GTDB 计数恒为 2 → 误判 `SOURCE=gtdbtk` → 空表 + exit 0 + 标 completed |
   | 仅修 glob | 五个来源全为 0 → 落到 `else` → **exit 1 硬失败** |

   **两种状态都是坏的**，所以不能只改 glob 就收工。处置方案：

   - **主用 fail-fast**：在 `validate` / `plan` 阶段就拦住——
     `analysis_basis=contig` 且 `selected_steps` 含 `10_community` ⇒ 要求
     `contig_taxonomy=kraken2`，并提示这会引入 kraken2 数据库依赖。
     这正是 §4.3「跑之前就拦住错误配置」该管的事。
   - 若用户**显式**声明不要任何 taxonomy，则阶段 10 **优雅跳过**，并在
     `DATA_DICTIONARY.md` 里写明缺失原因，而不是交付一张空表。
   - **不建议**把 `contig_taxonomy` 默认直接改成 `kraken2`——那会静默引入一个
     上百 GB 的数据库依赖，违反原则 4。

   **暴露出的一类共性缺口**：`config.validate()` 目前只做**逐字段**校验，
   完全没有**跨字段一致性校验**。上面这条（路线 × 阶段 × 开关的组合合法性）
   属于后者，应当作为 `validate` / `plan` 的一类独立规则来实现，而不是散落在各处。

---

## 8. 实施路线

按"新手能不能独立跑完第一条流程"重新排序。**判断一个能力该排多前，看它能不能阻止一次新手的高代价失败。**

| 阶段 | 内容 | 为什么这个顺序 | 验收标准 |
|---|---|---|---|
| **P0 · 地基** | 第 7 章全部技术债；git init + pytest + CI；`metaglens demo` | demo 排在最前是因为它是新手的第一次成功体验，也是后续所有改动的回归网 | 新装环境下 `metaglens demo` 五分钟内端到端绿；CI 通过 |
| **P1 · 别让他白跑** | `sense/` 全部；`required_tools()` + `required_databases()` 孪生函数（共同底座，第一项做）；`doctor`；`db list/scan/get/verify`（含四态判定、显式拦截、峰值磁盘预检）；`plan`；错误信息三段式 + 拼写纠错 + `--lang zh` | 新手最高代价的失败是"跑 9 小时才炸"，而根因几乎总是缺 DB、内存不够、路径写错——全部可在跑之前查出来。`db scan` 单独值得优先：共享服务器上复用同事已下好的 GTDB 是零成本收益 | 缺 GTDB-Tk / 内存不足的机器上，`plan` 30 秒内准确预警且下一步命令可直接复制；`db scan` 能发现系统已有库并复用；kraken2 按可用内存推荐正确变体 |
| **P2 · 看得见、爬得出来** | `--monitor` 仪表盘；`watch`；`gate` 质量门禁；diagnose 归因 | 跑起来之后的两个新手痛点：不知道进行到哪（会误以为卡死而 Ctrl-C 掉几小时的活）、挂了不知道为什么 | 人为造 OOM，能正确归因为 environment 并给出可执行建议；QC 保留率 40% 时能被门禁拦住 |
| **P3 · 教他做对** | 向导重构（分组/回退/推荐/`?` 解释）；`explain` 知识库；`recommend` 参数推荐 | 前三阶段解决"不出事"，这一阶段解决"做得对"。放在后面是因为它依赖 P1 的感知能力才能给出有依据的推荐 | 一个没读过文档的新手能独立完成配置并跑完；每个推荐值都能说出理由 |
| **P4 · 兜底与打磨** | `repair` 有界自愈；report 新增 gates 标签页；methods 生成器 | 自愈价值高但风险也高，必须等 diagnose 的归因准确率验证过再上，否则会"自动地做错事" | 注入 OOM 后能自动降并发重跑成功，`repair_log.jsonl` 证据完整；两次失败即停 |
| **不排期** | SLURM/SGE 深度适配、webhook/邮件通知、Snakemake 导出、AI 顾问层 | 均不服务于"新手单节点上手"这个主线；有真实用户诉求时再单独立项 | — |

依赖新增：`psutil`（资源观测，必需）。`textual` 不引入——Rich Live 足够，多一个重依赖对"服务器上装软件"的用户是负担。

---

## 9. 风险与取舍

**不改用 Snakemake / Nextflow（已评审确认）。** 这是评审人第一个会问的问题——"你为什么要重新发明一个工作流引擎？"，所以完整记录论证。

换引擎能**白拿**下面这些能力，正好覆盖本方案 P1/P2 相当大一块工作量：

| 能力 | 引擎自带 | 本方案要手写 |
|---|---|---|
| 依赖 DAG 自动推导 | ✓ | `routes.py` 手维护 `prerequisite` 链 |
| 断点续跑 | ✓ 按文件时间戳 | 手维护 `pipeline_status.json` |
| 并行调度 | ✓ | `run_parallel` + `planner.py` |
| SLURM/SGE/云适配 | ✓ 换个 profile | 手写 SBATCH 指令模板 |
| 每步独立 conda 环境 | ✓ | `conda_setup.py` 的三组划分 |
| provenance 报告 | ✓ | 手写 |

**代价是交付物的性质变了。** 用户拿到的不再是能从上往下通读的 `02_assembly.sh`，而是带 `rule` / `wildcards` / `{input}` / `{output}` 的 Snakefile。要跑要改，先得装 Snakemake、再学它的 DSL。README 里那句明确承诺——"generated scripts are standalone and inspectable... without MetaGLens"——直接作废。

**对本项目的目标用户，这个代价是不可接受的。** 软件版存在的理由是"服务器上**不被允许引入计费型 agent**"；让这批人转头去装并学会 Snakemake，摩擦是同一量级的。**换引擎等于用一种依赖换掉另一种依赖，而不是消除依赖。** 另外那 4000 行 bash 是从 skill 版原封不动继承、已验证跑通的资产，换引擎意味着全部丢弃重写。

**不冲突的中间路线（记录备查，当前不排期）：** 将来若出现千样本级或云端需求，可加 `metaglens export --format snakemake|nextflow`，导出一份工作流定义给需要规模化的用户。这是**加法**——bash 脚本仍是主交付物，两条路并存，新手路径完全不受影响。只有在出现真实用户诉求时才立项。

**时长/内存预估必然不准。** 生物信息负载对数据特征极敏感。所以一律标注 ±50% 并给出依据的样本规模，让用户知道这是量级判断而非承诺。给带误差标注的估算 > 什么都不给。

**规则库会腐化。** 缓解手段：规则带 `id` 和 `last_reviewed` 字段；`metaglens doctor --rules` 列出长期未命中的规则；命中统计可选上报（默认关闭）。承认这需要持续投入——但外置 YAML 至少让维护成本降到"改配置"级别。

**过度自动化的风险，对新手尤其大。** 专家会怀疑软件的建议，新手会全盘接受。最坏情况是软件静默替用户做了科学决策，用户拿到一堆 MAG 却完全不知道它们是在什么参数下产生的、为什么是这个数量。原则 4（解释权高于自动化）、原则 5（科学参数不自动改）和自愈动作白名单就是为此设的护栏，评审时请优先审这三处。这也是为什么 `recommend` 一定要输出理由而不只是输出值。

**Rich Live 与 SLURM 提交的模式冲突。** 集群上提交作业后进程立刻返回，没有本地进程可监控。设计上 `--monitor` 在 slurm/sge 下切换为轮询 `squeue`/`sacct`（skill 的 execution-monitoring.md 已给出该做法），仪表盘布局共用。

---

## 10. 怎么衡量做到了

不是"加了 N 个命令"，而是**一个没做过宏基因组分析的人，能不能自己跑完并且知道自己在做什么**。具体到可验收的场景：

| # | 场景 | 现在 | 目标 |
|---|---|---|---|
| 1 | 缺 GTDB-Tk 数据库就开跑 | 跑 9 小时后在 07 阶段炸 | `plan` 在 30 秒内预警，给出下载命令与体积预估 |
| 2 | 128 GB 机器上 7 样本并发组装 | OOM，全阶段失败 | `recommend` 提前算出峰值 168 GB > 128 GB，建议降到 4 并发 |
| 3 | 组装阶段 40 分钟没有输出 | 只有转圈，新手以为卡死按了 Ctrl-C，几小时白费 | 仪表盘显示 `k=79 进行中` + 日志心跳，明确"这是正常的" |
| 4 | QC 保留率只有 40% | 静默继续，跑完 9 小时才发现数据本身有问题 | 门禁立刻提示"这不正常"，并说明常见原因（接头污染 / min_length 过严） |
| 5 | 某阶段半夜挂了 | 损失一整晚 | 自动降并发重跑该阶段，留下 `repair_log.jsonl` 证据 |
| 6 | 看到 `exit 137` | 不知道这是什么 | 归因为"被 OOM killer 杀掉"，给证据行号和两个可选下一步 |
| 7 | 不知道 `completeness_min: 50` 意味着什么 | 只能去翻文献 | `metaglens explain completeness_min` 讲清 MIMAG 标准与取舍 |
| 8 | 第一次装完，不确定环境对不对 | 只能拿真实数据试，错了代价高 | `metaglens demo` 五分钟跑通全流程，确认环境可用 |

前 6 条对应的都是**真实的、代价高昂的失败模式**；后 2 条对应的是新手最容易卡住不敢往下走的时刻。这八条就是"智能化"要买的东西——**每一条都能换算成一个新手省下的一整天。**
