# 实施计划：Web 配置(方案 B)及其后端

> 本文件是**跨会话协调基准**。
> - 规划/审查:IDE 会话(PengPPPP 本人对话的这个)
> - 实现:tmux `metaglens_qoder` 会话(持续在线)
> - 每完成一个 Phase,实现方在对应勾选框打勾并追加「完成备注」,然后 **git commit**。
> - 实现方负责 `metaglens/`、`tests/`、`README.md`;`docs/WORKLOG.md` 归 IDE 会话。
> - 设计依据:`DESIGN-intelligence-and-ux.md`;施工历史:`WORKLOG.md`。
>
> **运行模式(2026-07-30 用户授权)**:用户休息期间,实现方**连续执行 Phase 1→Phase 6**,
> **无需逐阶段等待人工审查**。但每个 Phase 仍须自证门禁通过才能 commit:
> `python3 -m unittest discover -s tests -t .` 全绿 + `bash -n` 过全模板 + 遵守铁律。
> **若某 Phase 无法通过验证、或遇到需用户拍板的歧义(尤其科学参数/安全取舍),停下来在完成备注
> 写明阻塞原因,不要提交碎代码、也不要猜着往下做。** 全部做完后停下汇报总清单(各 Phase commit 哈希)。

---

## 0. 目标与总原则

**用户目标**:课题组服务器常有 Linux 可视化 + 内置浏览器。让新手在**网页**里完成配置
(线程/任务、要做什么、数据库路径),数据库缺失时告知去哪下载。选型已定:**方案 B
(本地小服务)**——`metaglens configure` 起一个本地 HTTP 服务、自动开浏览器、填完
POST 回写 `metaglens.yaml`。

**为什么 B 依赖后端**:用户要的不是"填空表单",而是"会检查会建议的助手"——
配线程要有硬件推荐、填 DB 路径要当场校验、缺失要先扫描确认再给下载指引。这些是
**后端能力**(感知层),网页只是它的入口。所以顺序 = 先造后端,再套网页壳。

**贯穿原则(违反即回退)**:
1. **离线优先**:全部功能在断网、无 API Key 下可用。新增重依赖一律禁止;
   `psutil` 只能作**可选增强**,缺失时用 stdlib(`os.cpu_count`、`shutil.disk_usage`、
   `/proc/meminfo`)兜底。Web 服务只用标准库 `http.server`,**不引入 Flask/FastAPI**。
2. **配置对等**(设计原则 7):网页产出的 `metaglens.yaml` 必须与终端向导**完全一致**,
   两者复用同一个 `Config` 及其 `validate()`。终端向导**保留**为无 GUI 服务器的兜底。
3. **共享服务器安全**:Web 服务绑 `127.0.0.1` + 一次性 token;假定单用户,文档写明。
4. **复用现有**:`samples.discover()`、`Config`、`conda_env`(已修)、`routes`,不要重造。
5. **科学参数不自动改**:推荐只给资源类(线程/并发)与路径,绝不改 `min_contig_len` 等。
6. 每个 Phase 独立 commit;`python3 -m unittest discover -s tests -t .` 必须保持全绿。
7. **视觉对齐(用户明确要求)**:Web 配置页的整体风格/版式必须与 **skill 版报告**和
   **现有 `report.py` 报告**完全一致——同一套蓝色 poster 主题、六边形透镜背景、
   Times New Roman;**唯一变量是 logo**。为防两处 HTML 日后漂移,须把共用视觉
   (CSS 调色板 + 透镜 SVG)**抽成一个共享模块**,由 `report.py` 与 `webconfig.py` 共同
   import(见 Phase 4.2)。
8. **交互层可中英切换(用户明确要求)**:配置页提供中/英语言选项(设计原则 8 + §5.3)。
   注意边界:**只切 UI 文案,产出的 `metaglens.yaml` 与语言无关**;交付物(report/methods)
   仍英文,故共享模块只放"视觉",不放"文案"。

**测试环境注意**:本机无 `typer`/`rich`,所以 CLI 层不能在 unittest 里直接 import;
沿用现有做法——核心逻辑放无第三方依赖的模块,测试只 import 这些模块。

---

## Phase 0 — 收尾 §7-8(P0 遗留,独立,最先做)

**动机**:`10_community_summary.sh` 的 `GTDB_SUMMARIES` 是字面量数组,`nullglob` 不生效,
计数恒为 2 → `contig_based` 路线交付物是坏的。详见 `WORKLOG.md §6.2`。

**步骤**
- [x] 0.1 `10_community_summary.sh:67`:把 `GTDB_SUMMARIES` 改成真 glob。
  参考同文件 `06_dereplication.sh` 的多行 glob 写法:
  `GTDB_SUMMARIES=("${TAX_DIR}/gtdbtk/"*.summary.tsv)`(或分别列 bac120/ar53 的 glob 模式)。
- [x] 0.2 同文件 `:297` 附近:`NUM_TAXA==0` 时**拒绝**标 completed,报错退出并提示
  「该来源产出空表,检查上游」。这是设计稿 §4.4「产物验证」的落地。
- [x] 0.3 `config.py` / `validate()`:当 `analysis_basis` 推出为 contig(或 route 含
  `09_contig`)且 `selected_steps` 含 `10_community`、而 `contig_taxonomy==none` 时,
  给出**明确错误**:「contig 路线要产出群落表需 contig_taxonomy=kraken2(需 kraken2 库)」。
  这是 §4.3「跑之前拦住」。**不要**把默认改成 kraken2(会静默引入 DB 依赖)。
- [x] 0.4 回归测试:`tests/` 加用例——字面量 vs 真 glob 计数差异、空表被拦、上述 validate 报错。
- [x] 0.5 `bash -n` 全模板 + unittest 全绿 → commit `fix: section 7-8 community-source nullglob & empty-matrix guard`。

**验证**:构造一个"无 gtdbtk 输出"的目录,确认 SOURCE 不再误判 gtdbtk;contig 默认配置被 validate 拦住。

---

## Phase 1 — `sense/hardware.py`(硬件感知)

**目标**:回答"这机器几核、多少内存、多少可用磁盘"。

**步骤**
- [x] 1.1 新建 `metaglens/sense/__init__.py`、`metaglens/sense/hardware.py`。
- [x] 1.2 `probe() -> HardwareInfo(cores, ram_gb, disk_free_gb, in_container)`:
  - cores: `os.cpu_count()`;
  - ram_gb: 优先读 `/proc/meminfo` MemTotal,失败退 `os.sysconf`;
  - disk_free_gb: `shutil.disk_usage(path)`;
  - psutil 若可用可作交叉校验,但**不得**作为硬依赖(`try: import psutil except: None`)。
- [x] 1.3 测试:mock `/proc/meminfo` 与 `shutil.disk_usage`;断言 psutil 缺失时仍返回结果。
- [x] 1.4 commit `feat(sense): hardware probing with stdlib fallback`。

**验证**:本机应报 ~112 核 / ~1081G RAM / 数百 G 空闲(实测基线,供对照)。

---

## Phase 2 — `sense/database.py`(数据库注册表 + 发现 + 校验)

**目标**:用户填 DB 路径能当场校验;没填能先扫系统;真没有才给下载指引。

**步骤**
- [x] 2.1 新建 `metaglens/sense/database.py`,定义 registry(每库):`env_var`、
  `sentinel`(判定该目录确为此库的标志文件,如 gtdbtk 的 `taxonomy/gtdb_taxonomy.tsv`)、
  `version_file`(如 gtdbtk 的 `metadata/metadata.txt` 里 `VERSION_DATA=`)、
  `size_hint_gb`、`download_hint`(命令或 URL 文本,不触发实际下载)。
  覆盖:checkm2 / gtdbtk / kraken2 / eggnog。
- [x] 2.2 `discover(name, cfg) -> DbStatus`:解析优先级 **CLI/config 显式路径 → 环境变量
  → 文件系统扫描(`~`、`/shared*`、`/opt*`、`{db_dir}` 下的候选目录名)→ 默认位置**;
  对命中的目录跑 sentinel 校验并回读版本。区分三态:已就位/路径写错(目录在但非此库)/未找到。
- [x] 2.3 `validate(name, path) -> (ok, detail)`:仅 sentinel + 版本,**只读**,不写 DB 目录。
- [x] 2.4 `required_databases(cfg) -> {name: reason}`:按 route + 配置开关(taxonomy_tool /
  contig_taxonomy / use_eggnog 等)推出**本次真正需要**的库;用不到的不报缺失(设计稿 §4.1)。
  **这是共用底座**(doctor/plan/web 都要),务必先做对。
- [x] 2.5 测试:mock 文件系统,覆盖三态 + required_databases 随配置变化。
- [x] 2.6 commit `feat(sense): database registry, discovery, validation`。

**验证**:本机 `~/gtdbtk_data/release232` 应被"文件系统扫描"发现(94G,环境变量未设);
版本应读 `metadata/metadata.txt` 的 `VERSION_DATA=r232`,不靠猜目录名。

---

## Phase 3 — `decide/planner.py`(并行方案推荐)

**目标**:给出 `parallel_jobs × threads_per_job` 推荐 + **理由**(设计原则:解释权高于自动化)。

**步骤**
- [x] 3.1 新建 `metaglens/decide/__init__.py`、`metaglens/decide/planner.py`。
- [x] 3.2 `recommend_parallel(cores, ram_gb, n_samples) -> Plan(jobs, threads_per_job, reason)`:
  以现有 `render.build_global_values` 里的推导为基线,补上"单样本峰值内存 × 并发 ≤ 总内存"
  这一约束(内存系数放模块常量,标注为粗估),输出人话理由。
- [x] 3.3 测试:小内存/多样本场景应压低并发并给出理由;`jobs*threads ≤ cores`。
- [x] 3.4 commit `feat(decide): parallel plan recommendation with rationale`。

---

## Phase 4 — `express/webconfig.py` + `metaglens configure`(方案 B 本体)

**目标**:本地网页配置,实时调用 Phase 1–3 的能力。

**步骤**
- [x] 4.1 新建 `metaglens/express/__init__.py`、`metaglens/express/webconfig.py`,
  基于 stdlib `http.server.ThreadingHTTPServer`。绑 `127.0.0.1`、端口用 `0`(系统分配),
  生成一次性 token,URL 带 `?token=`;所有请求校验 token,否则 403。
- [x] 4.2 **先抽共享视觉模块**(防漂移):把 `report.py` 里的 `_CSS` 调色板与 `_LENS`
  透镜 SVG 抽到 `metaglens/express/theme.py`(或 `metaglens/_theme.py`),`report.py` 改为
  import 它、行为不变(**回归:重建一次报告,`window.__MG__` 数据与关键 DOM 不变**)。
  然后 `GET /`:返回**自包含 HTML**表单,复用该共享主题(内嵌、不引外部资源),
  分组同终端向导五组。
  - **logo 可替换**:页面 logo 读单一 b64 资产,默认 `report_logo.b64`;若要用软件专属
    logo,把 `MetaGLens-software专属版.png` 转 b64 放同名位置即可,**代码不写死**。
    (用户只要求"替换 logo",其余视觉一律照搬,不得改动。)
  - **语言切换**:页面顶部放 中文/English 切换;所有 label 与帮助文案**双语内置**
    (建议 JS 里存 `I18N = {zh:{...}, en:{...}}`,切换即重渲染,无需刷新)。
    默认语言:CLI `--lang` > `Accept-Language` > 中文。**切语言不影响任何将写入 yaml 的值。**
- [x] 4.3 只读 JSON 接口(供前端实时用):
  - `GET /api/samples?dir=` → 调 `samples.discover()`,回样本清单 + 配对约定;
  - `GET /api/hardware` → Phase 1;
  - `GET /api/plan?cores=&ram=&n=` → Phase 3 推荐 + 理由;
  - `GET /api/db?name=&path=` → Phase 2 校验/发现,缺失时回 `download_hint`;
  - `GET /api/required-dbs?...` → Phase 2 required_databases。
- [x] 4.4 `POST /save`:用 `Config(**payload)` 构造 → `validate()`;不通过回错误清单;
  通过则 `to_yaml()` 写 `metaglens.yaml`,回成功页(附下一步命令 `metaglens run`)。
- [x] 4.5 `cli.py` 加 `configure` 命令:起服务、`webbrowser.open` 自动开(带 `--no-browser`);
  终端打印 URL+token;`Ctrl-C` 优雅关停。headless 时不报错,只打印 URL 让用户端口转发。
- [x] 4.6 **对等测试**:构造一份 payload,分别经 web 的 `/save` 路径与直接 `Config.to_yaml`
  产出 yaml,断言两者一致;`/api/*` 各接口的纯逻辑测试(不起真服务,直接测 handler 函数)。
  **另加**:切换语言后 POST 同样输入,产出的 yaml **逐字节一致**(证明 i18n 不污染配置);
  抽出共享主题后 `report.py` 重建报告的回归测试仍绿。
- [x] 4.7 commit `feat(express): metaglens configure — local web config (approach B)`。
- [x] 4.8 **配置入口引导(用户要求③)**:`cli.py` 的 `init` 一进来先问一句
  「在终端向导填 / 打开网页填」(默认终端,回车即选);选网页则走 Phase 4 的
  `configure` 服务。headless 无浏览器时自动回退到终端向导并提示。两条路径产出
  同一份 `metaglens.yaml`(对等)。commit `feat(cli): init offers shell-wizard or web config`。

**安全自查**:确认未绑 `0.0.0.0`;无 token 请求返回 403;不写 DB 目录;不发起网络请求。

---

## Phase 5 — 文档

- [x] 5.1 `README.md`:新增 `metaglens configure` 用法 + 安全说明(127.0.0.1/token)+
  「无 GUI 时用终端向导或端口转发」。
- [x] 5.2 在本文件对应 Phase 打勾并写完成备注。
- [x] 5.3 通知 IDE 会话审查(git diff)。

---

## Phase 6 — 实时运行状态 HTML(用户要求①,方案 S:自刷新静态页)

**目标**:运行过程中用户随时能打开一个 HTML 看到全部进展。终端可视功能(§5.2 +
`metaglens status`)**保留**,HTML 是加法。

**选型(用户已确认:方案 S)**:不起服务。由一个 `metaglens monitor`
旁路进程(或运行本身)每几秒读 `pipeline_status.json` + `reports/logs/*` 重写
`results/monitor.html`;页面靠 `<meta http-equiv="refresh">` 自刷新。用户 `file://` 直接开,
不依赖任何服务存活;运行结束/崩溃后仍可打开看最终态。

**步骤**
- [x] 6.1 `observe/monitor.py`:从 `pipeline_status.json`(steps/started/finished/attempts)+
  当前阶段日志尾部 采集监控数据;纯 stdlib。
- [x] 6.2 `monitor.html` 渲染:**复用 Phase 4.2 抽出的共享视觉模块**(与交付报告
  同一套皮),展示:阶段时间线/状态、当前阶段、已耗时、日志尾部;自包含、自刷新。
- [x] 6.3 `cli.py` 加 `monitor` 命令(旁路启动,不影响 run);与现有 `status`/§5.2 终端版共存。
- [x] 6.4 测试:给定一份 status.json + 日志,断言 monitor.html 含各阶段状态与日志尾;
  阶段失败时页面能正确标红。
- [x] 6.5 `README` 补 `metaglens monitor` 用法。commit `feat(observe): live self-refreshing monitor.html`。

**依赖**:Phase 4.2 共享视觉模块(保证与交付报告风格一致)。

---

## Phase 7 — 嵌套目录的样本发现(用户要求:输入文件夹里可能还套着文件夹)

**动机(已实测确认的缺口)**:`samples._list_fastqs` 用 `raw_dir.iterdir()`,**非递归**。
因此以下两种真实世界最常见的交付布局**当前直接失败**(报 "No FASTQ files found"):

```
布局1 每样本一个子目录(测序公司/SRA 常见)
  raw/SampleA/SampleA_R1.fastq.gz + SampleA_R2.fastq.gz
布局2 子目录内文件名通用,靠目录名区分样本
  raw/S1/reads_1.fq.gz + reads_2.fq.gz
  raw/S2/reads_1.fq.gz + reads_2.fq.gz
```

**这个任务的难点不是"递归",而是样本 ID 从哪来 + 不能跨目录错配。**
以下约束是硬性的,写错会造成"样本张冠李戴"这类最坏的科学错误:

- [x] 7.1 **递归扫描**:替换 `_list_fastqs` 为带**深度上限**(默认 3 层)的递归;
  必须**防符号链接环**(用 `resolve()` 记已访问 inode/路径);跳过隐藏目录。
- [x] 7.2 **配对只能在同一父目录内进行**。绝对不允许把 A 目录的 R1 和 B 目录的 R2
  配成一对——这是递归实现最容易引入的致命 bug。实现上:先按父目录分组,再在每组内
  套用现有 4 种命名约定。
- [x] 7.3 **样本 ID 推导顺序**(并把用了哪种记录下来):
  1. 文件名推导(与现有扁平逻辑完全一致);
  2. 若文件名推导出的 ID **在不同目录间冲突**(如布局2 全是 `reads`)→ 改用**父目录名**;
  3. 若父目录名仍冲突 → **报错并要求用户提供 manifest**,不得自行编号糊过去。
  **回归要求**:扁平布局的发现结果与 ID 必须与现在**逐字不变**(现有测试须全绿)。
- [x] 7.4 **布局透明化**:`discover()` 的返回值除现有 `pattern` 外,增加
  `layout`(`flat` / `nested`)与 `id_source`(`filename` / `dirname`),供向导与网页
  展示"我是怎么判断的"——符合设计原则 4(解释权高于自动化)。
- [x] 7.5 **用户可自己填/改**(用户明确要的第二条路):
  - 网页配置(Phase 4)的样本表改为**可编辑**:可改 `sample_id`、可勾选排除;
  - 终端向导补「排除部分样本」选项(设计稿 §5.1 已规划但未做);
  - `samples.tsv` manifest 仍是终极兜底,文档写明。
  改完仍须过 `_validate()`(ID 唯一、文件存在、无文件被两个样本共用)。
- [x] 7.6 测试:两种嵌套布局各一例;跨目录错配的**反例断言**(A/B 目录不得互相配对);
  ID 冲突时回退到目录名;目录名也冲突时报错;符号链接环不死循环;深度超限被截断;
  **扁平布局回归不变**。
- [x] 7.7 commit `feat(samples): recursive discovery for nested layouts with safe id derivation`。

**验证**:上述两种布局都能正确发现 2 个样本,ID 分别为 `SampleA/SampleB` 与 `S1/S2`。

---

## Phase 8 — P1 命令层:`doctor` / `db` / `plan`

**动机**:`sense/` 底座已就位(Phase 1–2),但三个"跑之前拦住错误配置"的命令还没有。
这是设计稿 P1「别让他白跑」的正主,也是新手最高代价失败(缺 DB、内存不够、路径写错)
的唯一拦截点。增量很小,价值最高。

- [x] 8.1 **`required_tools(cfg) -> {tool: reason}`(前置底座,先做)**。
  现有 `PIPELINE_TOOLS` 是扁平 18 项常量,推不出"本次真正要用哪些"。映射:
  route → `routes.STEPS[x].env_group` → `conda_setup.ENV_GROUPS[group]`,再叠加配置开关
  (`assembler` megahit/spades、`align_tool` bowtie2/bwa-mem2、`taxonomy_tool`、
  `contig_taxonomy`、`use_prokka` / `use_eggnog` / `use_bracken` / 四个 binner 开关)。
  与 `required_databases()` 对称。**这是 doctor/plan 共用底座。**
- [x] 8.2 **`metaglens doctor [--env NAME] [--fix] [--json]`**(设计稿 §4.1):
  输出分组表格:工具×版本(按 `--env` 或 config 的环境)、**可执行文件在 PATH 上真的能跑吗**
  (`conda list` 有包 ≠ 命令可用)、数据库就位情况(复用 `sense/database`)、硬件余量
  (复用 `sense/hardware`)。
  **按裁决 D-2**:当前路线用不到的工具**照常展示但标注「当前路线不需要」,不算缺失、不报错**;
  只有 `required_tools` 里的缺失才是问题。`--fix` **只补装缺失,永不升级已有包**
  (沿用 skill "不做 conda update --all" 的约束),且执行前需确认。
- [x] 8.3 **`metaglens db list|status|get|verify|where [--json]`**(设计稿 §4.7):
  - `status` / `list`:只显示 `required_databases(cfg)` 推出来的库 + 三态(就位/路径错/未找到);
  - `where <name>`:打印**完整解析链**及命中的是哪一级(显式 → 环境变量 → 文件系统扫描 → 默认);
  - `verify <name>`:只读校验(sentinel + 版本),**不得往 DB 目录写任何临时文件**;
  - `get <name>`:**按裁决 B**——强制显式目标目录(不预设可写默认路径),下载前
    **校验剩余空间 ≥ 体积 × 1.2**,不足则拒绝并提示换盘;**必须显式确认才真下载**
    (200 GB 级操作,默认不动手)。
- [x] 8.4 **`metaglens plan [--json]`**(设计稿 §4.3):阶段表(模式/预估时长/峰值内存/
  磁盘增量),合计行,并对缺失的必需数据库**预警 + 给出 `db get` 命令**。
  时长/内存**必须标注为粗估(±50%)**并说明依据的样本规模——给带误差标注的量级判断,
  但不许假装精确。
  **另含 D-6 附加项 B**:提供一份**可粘贴的纯文本摘要**(`--plain` 或摘要区),供学生向
  导师/管理方申请资源使用,并能同时说明「本流程不产生任何计费」。
- [x] 8.5 测试:`required_tools` 随路由/开关变化(contig 路线不应要求 binner/checkm2 等);
  doctor 在"环境不存在"与"conda 不可用"下的表现(复用已修的三态);`db where` 解析链
  命中层级正确;`db get` 空间不足时拒绝;`plan` 在缺 DB 时给出预警;`--json` 可被 `json.loads`。
- [x] 8.6 `README` 补三个命令用法。commit 分三个:
  `feat(sense): required_tools`、`feat(cli): doctor & db commands`、`feat(cli): plan command`。

**铁律不变**:离线优先、不引重依赖、只读不写别人的 DB 目录、大额下载必须显式确认、
科学参数不自动改。

---

## Phase 9 — `prodigal` 修复 + `demo`(桩工具端到端)+ CI

### 9.A `prodigal` 缺口(IDE 会话已裁定:修)

证据:`09_contig_analysis.sh:116` 直接调用 `prodigal`;`conda_setup.ENV_GROUPS` 与
`conda_env.PIPELINE_TOOLS` **都没有它**;而 skill 版 SKILL.md 明确把 Prodigal 列入
待检查工具。故这是软件版的遗漏,非有意取舍。

- [x] 9.A1 `ENV_GROUPS["mag"]` 加 `prodigal`(`09_contig`/`08_annotation` 的 env_group 均为 mag)。
- [x] 9.A2 `conda_env.PIPELINE_TOOLS` 加 `prodigal`(否则向导缺失检查同样漏报)。
- [x] 9.A3 测试:`setup-env` 的 mag 组命令含 prodigal;contig 路线 `required_tools` 与
  `ENV_GROUPS` 不再出现"要求但装不上"的差集。**建议加一条通用断言:
  `required_tools` 覆盖的每个工具都能被某个 ENV_GROUP 提供**——防止再漏第二个。
- [x] 9.A4 commit `fix(conda): ship prodigal — required by contig route but absent from env groups`。

### 9.B `metaglens demo` — 桩工具端到端(方案已定)

**目标**:装完就能几秒内验证整条链路可用;同时成为后续所有改动的回归网。

**选型**:**桩工具(stub toolchain)**。造极小假可执行(`fastp`/`megahit`/`bowtie2`/`samtools`/
`seqkit`/`metabat2`/`prodigal`/... 各吐出格式合法的最小产物),置于临时 PATH 最前,
让**真实模板完整执行**。理由:零依赖、零数据库、完全离线、几秒完成,且检验的是模板的
**真实控制流 + 状态机 + 产物验证 + 报告/监控生成**,而非仅 `bash -n`。
(这类测试本可抓到 §7-8 的 nullglob bug——桩工具不产 gtdbtk 输出时,来源选择会被真正走一遍。)

- [x] 9.B1 `metaglens/demo/` :合成极小 FASTQ(每样本几千条,内置或即时生成)+ 桩工具集。
  桩必须产出**下游真正会读的最小合法产物**(如 fastp 的 `_fastp.json` 含 summary 字段、
  contigs FASTA、BAM 可被后续步骤识别的替代物等);桩要**打印自己被调用**便于诊断。
- [x] 9.B2 `metaglens demo [--route NAME] [--keep] [--json]`:建临时项目 → 渲染 → 用桩 PATH
  跑完选定路由 → 断言各阶段 `completed`、关键产物存在、`report.html` 与 `monitor.html` 生成
  → 打印结论并清理(`--keep` 保留供排查)。**默认不碰用户已有项目、不写 `~`**。
- [x] 9.B3 至少覆盖 `mag_per_sample` 与 `contig_based` 两条路由(后者正是 §7-8 出问题的那条)。
- [x] 9.B4 测试 + `README` 说明 `demo` 是"装完自检 + 回归网",并注明它用桩工具、
  **不产生科学结果**(避免误解为真实分析)。
- [x] 9.B5 commit `feat(demo): offline end-to-end self-check with a stub toolchain`。

**后续可选(本阶段不做)**:`demo --full` 用真实工具跑最短路由,受工具可用性限制。

### 9.C CI

- [x] 9.C1 `.github/workflows/ci.yml`:矩阵 Python 3.8/3.10+;跑
  `python3 -m unittest discover -s tests -t .`、`bash -n` 全模板、`metaglens demo`(桩)。
  不依赖网络与 conda。
- [x] 9.C2 commit `ci: run tests, bash -n and the stub demo`。

---

## Phase 10 — 产物验证 + 质量门禁(`state.py` + `decide/gates.py`)

**为什么排最前**:设计稿 §4.4 直接称此为「当前最实质的可靠性缺口」——Python 侧只读
shell 写的状态标志位,**从不复核产物**。§7-8 就是活例子(空表 + exit 0 + 标 completed)。

- [x] 10.1 `metaglens/state.py`:**语义级**产物验证。注意设计稿的明确警告——不能停在
  「文件存在且非空」,**表头本身就让文件非空**。每阶段写明可判定下界,至少:
  `01_qc` 每样本 clean R1/R2 存在且 > 0;`02_assembly` contigs ≥ 1 条序列;
  `03_mapping` 每样本 BAM + depth 非空;`04_binning` all_bins ≥ 1 个 FASTA;
  `05_checkm` `quality_report.tsv` ≥ 1 个 bin 数据行;`06_derep` `dereplicated_genomes/` ≥ 1 FASTA;
  `07_taxonomy` summary ≥ 1 数据行;`08_annotation` 每 MAG 至少一份注释;
  `09_contig` 蛋白/GFF 非空;`10_community` matrix ≥ 1 数据行;`11_delivery` 关键交付物齐全。
- [x] 10.2 **接入**:阶段脚本标 completed **之后**,Python 侧(`pipeline.run_step`)复核产物;
  不通过则把该阶段改回 `failed` 并写明原因,`run` 中止。**即使 shell 说成功也不放过。**
- [x] 10.3 `metaglens/decide/rules/gates.yaml` + `decide/gates.py`:软门禁(科学指标)。
  按设计稿:`01_qc` retention_rate(warn<70 / block<40)、`04_binning` bins_per_sample(warn<1)、
  `05_checkm` mimag_hq_count(warn<1,≥90%完整/≤5%污染)。**规则外置 YAML**(原则 3),
  每条带 `id` 与 `hint`(人话解释常见原因)。
- [x] 10.4 `metaglens gate [--stage ID] [--strict] [--json]`:默认 warn 只提示;
  `--strict-gates` 时 warn 也阻断。结果写入 `pipeline_status.json.gates`。
- [x] 10.5 `run` 增 `--strict-gates`;report.html 增 **Gates 标签页**(复用 `_theme`)。
- [x] 10.6 测试:每阶段产物验证的正反例;**"表头非空但零数据行必须判失败"**;
  软门禁三档(pass/warn/block);`--strict-gates` 行为差异;`--json` 可解析。
  **并且:用 `demo` 端到端验证门禁真的会拦**(参考我用注回 bug 的方式自证有牙齿)。
- [x] 10.7 commits:`feat(state): semantic product validation`、`feat(decide): quality gates`、
  `feat(cli): gate command & --strict-gates`。

---

## Phase 11 — 失败归因 `decide/diagnose.py`(把 `exit 137` 翻成人话)

设计稿 §4.5。现状失败只有一行 `exit N. Check reports/logs/.`。

- [x] 11.1 `decide/rules/failures.yaml`(**外置规则**,每条带 `id`/`match`/`class`/`title`/
  `diagnosis`/`actions`)。三类归因:`script_defect` / `environment` / `data_config`。
  至少覆盖:OOM(exit 137)、数据库未配置(GTDB-Tk/CheckM2/Kraken2/eggNOG 各自特征)、
  通配符未匹配(上游空产出)、磁盘满、命令未找到(工具缺失)、权限拒绝、
  scheduler 相关(如适用)。
- [x] 11.2 `diagnose.py`:输入 = 退出码 + 日志尾 + 阶段 + `pipeline_status.json.last_failure`;
  输出 = 归因(类别/标题/证据行/建议动作)。匹配失败要**优雅降级**为"未知失败 + 日志位置",
  绝不硬编造原因。
- [x] 11.3 **错误信息三段式**(设计稿 §5.3):全局统一为 发生了什么 / 为什么 / 下一步敲什么命令。
  `cli._fail()` 现在只给第一段,升级它并在失败路径接入 diagnose。
- [x] 11.4 `metaglens diagnose [--stage ID] [--json]`;`run` 失败时自动打印归因。
- [x] 11.5 测试:各特征规则命中(用构造日志);未知失败的降级;三段式包含可执行命令。
- [x] 11.6 commits:`feat(decide): failure diagnosis rules`、`feat(cli): three-part error reporting`。

---

## Phase 12 — 交互打磨(拼写纠错 / profile / i18n / explain)

- [x] 12.1 **拼写纠错**(§5.3):`difflib.get_close_matches` 用于 `--only`/`--from` 步骤名、
  route 名、以及 `Config.from_yaml` 的未知键(现在直接抛异常、不给建议)。
  提示形如「未知步骤 '04_bining';你是不是想输入 '04_binning'?」。
- [x] 12.2 **用户级 profile**(§5.3):`~/.config/metaglens/profile.yaml` 记住上次的
  `total_threads`/`db_dir`/`conda_env`/`lang`,下次作默认。**读失败要静默降级**;
  遵循 `XDG_CONFIG_HOME`。**profile 只提供默认值,绝不覆盖显式配置。**
- [x] 12.3 **`express/i18n.py`**:交互层 `--lang zh|en` 覆盖终端输出(向导/doctor/plan/gate/
  diagnose 的提示语)。**交付物(run_log/methods/report)保持英文**(原则 8)。
  与 Phase 4 网页的 i18n 共用文案表(能共用则共用,不强求)。
- [x] 12.4 **`express/explain.py` + `metaglens explain <topic>`**(§5.3):把 skill 的领域知识
  资产化成**离线可查知识库**。至少覆盖:12 个阶段各自在干什么 + 常见坑;
  关键科学参数(`completeness_min`/`contamination_max` 与 MIMAG 标准、`ani_threshold`、
  `min_contig_len`、`min_length`、`quality_threshold`)的科学含义与取舍;
  Phase 11 的失败 id(如 `oom.killed`)。知识放 YAML/Markdown 数据文件,**不硬编码在 py 里**。
- [x] 12.5 `--json` 全覆盖补齐(§5.3):`status`/`validate` 若还缺则补上。
- [x] 12.6 测试:纠错建议正确;profile 读写与降级、不覆盖显式值;`explain` 各主题有内容且
  找不到时给候选;i18n 切换不影响交付物语言。
- [x] 12.7 commits 分开提(纠错 / profile / i18n / explain)。

---

## Phase 13 — 运行时可观测(`observe/resources.py` + 进度解析 + `watch` + 终端仪表盘)

设计稿 §5.2。**注意:Phase 6 的 monitor.html 已覆盖"网页看"这一半,本阶段做终端那一半。**

- [x] 13.1 `observe/resources.py`:采样 CPU/RSS/磁盘增量(stdlib 优先,psutil 可选增强)。
- [x] 13.2 `observe/progress/`:每工具一个解析器(fastp 样本计数、MEGAHIT 的 `k=` 行、
  bowtie2 百分比、prokka/GTDB-Tk 分片计数)。**解析失败必须优雅降级**为不定进度 +
  日志 mtime 心跳——设计稿明确警示「quiet log 不代表卡死」,组装器可几十分钟无输出,
  **不得因此误判为挂死**。
- [x] 13.3 `express/dashboard.py`:Rich Live 多面板(阶段进度条 + 每样本状态 + 资源 + 日志尾)。
- [x] 13.4 `metaglens run --monitor` 与 `metaglens watch`(独立进程附着,读 status + log tail,
  适配 tmux 里跑、另一窗口看)。**`q` 只退出监控界面,绝不杀进程**——设计稿点名此语义
  必须做对,否则用户会误杀几小时的活。
- [x] 13.5 监控页(Phase 6)与终端仪表盘**共用** `observe/` 采集层,避免两套逻辑漂移。
- [x] 13.6 测试:各解析器对样本日志的解析;**无法解析时降级为心跳而非报错**;
  `watch` 在无运行时给出友好提示。资源采样在 psutil 缺失时仍可用。
- [x] 13.7 commits:`feat(observe): resource sampling & progress parsers`、
  `feat(express): live terminal dashboard`、`feat(cli): run --monitor & watch`。

---

## Phase 14 — 参数推荐 `decide/advisor.py` + Methods 生成 + 有界自愈 `repair`

**顺序要求**:`repair` 必须最后做——设计稿 §8 明确「自愈价值高但风险也高,必须等
diagnose 归因准确率验证过再上,否则会自动地做错事」。

- [x] 14.1 `decide/rules/advice.yaml` + `advisor.py`(§4.2):规则外置,**每条建议必须带
  理由与严重度**。至少含设计稿给的两例:metaSPAdes 在小内存 + 大数据量下建议换 MEGAHIT;
  `threads_per_job < 4` 时组装器 I/O 争用告警。
- [x] 14.2 `metaglens recommend [--apply] [--explain]`:输出「当前值 vs 建议值 + 理由」。
  `--apply` **必须先展示 YAML diff 并确认**;**推荐引擎永不静默改配置**(原则 4)。
  **科学参数只提示不自动改**(原则 5)。
- [x] 14.3 `express/methods.py`(§5.4,低优先但要做对一件事——**版本号必须真实**):
  只写**实际执行**的阶段(读 status 的 selected_steps + completed),用
  `reports/tool_versions.txt` 的真实版本,缺失时标 `[provisional]`;过去时;
  不提没跑的分支。`metaglens methods` 改为由 Python 生成而非单纯 cat。
- [x] 14.4 `decide/repair.py`(§4.6,**安全边界不可协商**):
  上限 2 次(`--auto-repair N`,`0` 关闭);**只允许**降并发/降线程/加内存请求/重试瞬时错误;
  **禁止**改任何科学参数、改输入、动环境、动数据库、删非空产物;
  每次尝试前保存脚本快照到 `reports/repairs/{stage}/attempt-N/`;
  每次追加 JSON 到 `reports/repair_log.jsonl`(诊断/改动/验证命令/结果);
  **同一失败特征重复出现即停**,绝不无界循环;只重跑失败阶段,不碰上游。
- [x] 14.5 `run --auto-repair N`(**默认 0 即关闭**,需用户显式开启)。
- [x] 14.6 测试:advisor 规则命中与理由输出;`--apply` 未确认时不写文件;
  methods 只含已执行阶段且版本来自 tool_versions;
  **repair 的白名单必须有反例断言**——尝试修改科学参数时必须被拒绝;
  两次失败即停;repair_log.jsonl 证据完整。
  用 `demo` 注入一次可修复失败(如伪造 exit 137)验证降并发重跑成功。
- [x] 14.7 commits 分开提(advisor / recommend / methods / repair)。

---

## 收尾要求(Phase 10–14 全部完成后)

- [x] 15.1 `README` 补齐所有新命令;`docs/IMPL-PLAN-webconfig.md` 勾选与完成备注齐全。
- [x] 15.2 全量门禁:`unittest` 全绿、`bash -n` 全模板、`python3 -m metaglens.demo` 两路由 PASS、
  `compileall` 干净、CI 配置涵盖新命令。
- [x] 15.3 汇报总清单(各 Phase commit 哈希 + 关键验证输出),等 IDE 会话审查。

---

## Phase 16 — 真实环境验证(从"加功能"转向"质量收口")

**动机**:至今**所有**验证都基于桩工具与合成数据。桩能证明控制流与状态机正确,
但证明不了:真实工具的参数是否被接受、版本差异、真实输出格式是否如预期解析、
以及**多 conda 环境下工具解析是否真的работает**。本阶段目的是**暴露真跑才会出现的问题**,
不是加功能。

**本机实测前提(已由 IDE 会话勘查)**:
- `conda` 在 `/home/h1020/miniconda3/bin/conda`,**50 个环境**;
- **PATH 上没有任何生信工具**——全部在各自 conda 环境里;
- 相关环境:`fastq_megahit`(疑含 fastp/megahit)、`checkm2_env`、`drep_env`、
  `gtdbtk` / `gtdbtk27`、`metawrap_env`(疑含 binning 全套);
- GTDB-Tk 数据库真实存在:`~/gtdbtk_data/release232`(94 GB,r232)。

**先决原则(务必遵守)**
- **只读、不污染**:不得修改/升级任何既有 conda 环境;不得写 `~/gtdbtk_data`;
  一切产出写入自建临时工作目录。
- **不下载任何数据库**(200 GB 级);缺库的阶段就如实跳过并记录。
- 真跑必须**小规模**:抽样少量 reads,单/双样本,避免长时间占机器。
- **发现的问题一律先记录再修**;修真实缺陷时同样遵守既有铁律。

### 16.A 环境勘查(先摸清能跑到哪一步)

- [x] 16.A1 用 `metaglens doctor --env <name>` 逐个探查候选环境
  (`fastq_megahit` / `metawrap_env` / `checkm2_env` / `drep_env` / `gtdbtk`),
  记录每个环境**实际可运行**的工具与版本(注意 `doctor` 的 `package_only` 与可执行两种信号)。
- [x] 16.A2 产出一张「工具 → 所在环境 → 版本」实测表写进完成备注。
  **这张表决定后续能真跑哪几个阶段。**
- [x] 16.A3 顺带验证 `doctor` 在真实环境上的报告是否准确(有无误报/漏报)。

### 16.B 真数据准备(小规模)

- [x] 16.B1 找一份可用的真实 FASTQ:优先复用机器上已有的公共数据;若无,
  用 `seqkit`/`head` 从任意现存 fastq.gz **抽取少量 reads**(如每样本 2–5 万条)构造 2 个样本。
  **不联网下载**。若确实找不到任何真实 fastq,如实记录并跳到 16.D。
- [x] 16.B2 数据放临时目录;记录来源与规模。

### 16.C 真跑(能跑多远跑多远)

- [x] 16.C1 用 `metaglens init`(或直接写 yaml)+ `plan` + `doctor` + `db status` 走一遍**跑前检查**,
  确认这些命令在真实环境下给出的信息**准确**(这本身就是 P1 的核心价值验证)。
- [ ] 16.C2 真跑 `01_qc` → `02_assembly` → `03_mapping`(最可能具备工具的三段)。
  `conda_mode=reuse` 指向实测可用的环境;若工具分散在多个环境导致单一 `conda_env`
  无法满足,**这本身就是一个发现**——记录下来(现设计假定 create 模式才分三组)。
- [ ] 16.C3 能继续则继续 `04_binning` → `05_checkm` →(有 GTDB 库)`07_taxonomy`。
  不能则如实记录卡在哪、为什么。
- [ ] 16.C4 全程检查:`pipeline_status.json` 状态跃迁、产物验证(Phase 10)是否对真实产物
  判断正确(**特别注意有无误判真实有效产物为失败**——假阳性比漏检更烦人)、
  `gate` 的科学指标是否算得出、`report.html` 与 `monitor.html` 是否用真实数据渲染正确。
- [ ] 16.C5 若中途失败:用 `metaglens diagnose` 看归因是否命中真实原因(而非 unknown),
  记录归因质量。**这是 diagnose 首次面对真实失败。**

### 16.D 结果与修复

- [x] 16.D1 把全部发现整理成清单:分「真实缺陷」/「设计假设不成立」/「文档需澄清」三类。
- [x] 16.D2 **真实缺陷**逐个修 + 补回归测试(优先修会导致错误科学结果或误判的)。
- [x] 16.D3 **设计假设不成立**的(如多环境工具分散)只记录 + 提出方案,**不擅自改设计**,
  等用户裁决。
- [x] 16.D4 清理:删除临时工作目录;确认既有 conda 环境与 `~/gtdbtk_data` **未被改动**。
- [x] 16.D5 commit:`test: real-environment validation findings`(+ 各修复独立 commit)。

**验收**:完成备注里有 ① 工具实测表 ② 真跑到哪一阶段及每阶段结论
③ 发现清单(三类)④ 已修项与其回归测试 ⑤ 未污染环境的确认。

---

## Phase 17 — 配置页修复 + Phase 16 文档化

### 17.A logo 放大(用户明确要求)

- [x] 17.A1 `_theme.py` 的 `.logo{height:88px}` 改为 **`height:132px`**(约 1.5 倍)。
  这是共享主题,报告/配置/监控三页会一起变大——**这是期望的**(视觉统一)。
  确认 header 的 `align-items:center` 与 `flex-wrap` 在放大后仍正常、不挤压标题。
- [x] 17.A2 若移动端 `@media(max-width:700px)` 下 132px 过大,可加一条
  `.logo{height:96px}` 的媒体查询;否则保持。

### 17.B 并行建议硬编码 n=1(IDE 会话发现的前端缺陷)

`webconfig.py` 的 `refreshPlan()`:
```javascript
var n=1; var sb=document.getElementById("samples-box");   // sb 取了却从未使用
fetch(api("/api/plan?cores="+hw.cores+"&ram="+hw.ram_gb+"&n="+n))
```
后果:网页并行建议**永远按 1 个样本算**(3 样本 → 1×112,应为 3×37)。

- [x] 17.B1 让 `refreshSamples()` 把发现的样本数存到一个模块级变量(如 `window.__nSamples`
  或闭包变量),`refreshPlan()` 用它代替 `n=1`;无样本时回退 1。
- [x] 17.B2 **补一条能抓住这类前端缺陷的测试**:对 `build_page()` 产出的 JS 做静态断言——
  至少断言 `/api/plan` 的 URL 里 `n=` 参数**不是硬编码的 `n=1`**,而是引用样本数变量。
  (这类前端逻辑单测难覆盖,用"生成的 JS 不含某坏模式"的断言兜底。)
- [x] 17.B3 顺带自查 webconfig.py 里**还有没有其它"取了变量却没用"或硬编码**的地方。

### 17.C Phase 16 发现文档化

- [x] 17.C1 把 Phase 16 的工具实测表(工具→环境→版本)、跑前检查结论、
  以及**多环境分散问题**(fastp/seqkit/megahit 分属三个环境,无单一环境能跑完一组)
  整理进完成备注。**这是"设计假设不成立"级,只记录 + 给候选方案,不改设计、不改代码。**
- [x] 17.C2 候选方向写入备注供用户裁决:① 每工具/每阶段指定环境的细粒度模型;
  ② `doctor` 增加"跨环境拼 PATH"建议;③ 文档说明 reuse 单环境模式的适用前提。

### 17.D 门禁与提交

- [x] 17.D1 全绿:`unittest`(裸解释器)+ `bash -n` + `demo` 两路由;`compileall` 干净。
- [x] 17.D2 commits 分开:`style(theme): enlarge logo`、`fix(webconfig): plan uses real sample count`、
  `docs: phase 16 real-env findings`。

---

## 已定决策

- **Phase 6 选型 = 方案 S**(自刷新静态页,无服务)——用户 2026-07-30 确认。
  理由:“运行中随时能开、跨会话/崩溃后仍能看”不依赖服务存活;复用交付报告同一套皮。

---

## 完成备注(实现方在此追加)

<!-- 例:Phase 0 完成于 <commit>，验证输出：... -->

### Phase 0 — §7-8 收尾（commit `27304f4`）

- 0.1 `10_community_summary.sh:67` `GTDB_SUMMARIES` 改真 glob
  `("${TAX_DIR}/gtdbtk/"*.summary.tsv)`；下游 python 仍按具体文件名 isfile 打开，安全。
- 0.2 同文件在写出矩阵后、topN 前加产物校验：`NUM_TAXA<1` 则 `exit 1`（`enable_step_failure_trap`
  已在 `:53` 装好，退出会被标 failed），并移除 `:297` 的重复计算。
- 0.3 `config.py::validate()` 增跨字段一致性检查：`10_community` 在 route 内、且无 `07_taxonomy`
  又 `contig_taxonomy!=kraken2` 时报明确错误。未改任何默认值。mag 路线不受影响（含 07_taxonomy）。
- 0.4 新增 6 项测试（`TestCommunitySourceFix` ×3、`TestContigCommunityValidation` ×3），含
  bash 实证 `glob=0 / literal=2`、guard 先于 completed、contig 默认被拦 / kraken2 放行。
- 0.5 验证：`bash -n` 全 14 模板通过；`unittest` 59→**65** 全绿。

关键输出：
```
$ bash -c 'shopt -s nullglob; a=("<empty>/"*.summary.tsv); l=("<empty>/x.tsv" "<empty>/y.tsv"); echo ${#a[@]} ${#l[@]}'
0 2
$ python3 -m unittest discover -s tests -t .
Ran 65 tests in 0.849s
OK
```

### Phase 1 — `sense/hardware.py`（commit `6741141`）

- 新建 `metaglens/sense/`（`__init__.py` 导出 `HardwareInfo` / `probe`；`hardware.py`）。
- `probe(path, meminfo_path)`：cores=`os.cpu_count()`；ram 优先 `/proc/meminfo` MemTotal，
  退 `os.sysconf(SC_PAGE_SIZE*SC_PHYS_PAGES)`，再退可选 psutil；disk=`shutil.disk_usage`；
  `in_container` 查 `/.dockerenv` 与 `/proc/1/cgroup`。纯 stdlib，psutil 仅 try-import 兜底。
- 新增 4 项测试（真实 probe、meminfo 解析、mock disk_usage、psutil 缺失仍完整）。
- 验证：`unittest` 65→**69** 全绿；`bash -n` 全模板 OK；本机实测
  `112 cores / 498 GB RAM / 796 GB free`（cores 与基线一致；RAM 以本机 MemTotal 实测为准）。

### Phase 2 — `sense/database.py`（commit `51f52d1`）

- `REGISTRY`（checkm2/gtdbtk/kraken2/eggnog）：`env_var`/`sentinel`/`version_file`/
  `default_subdir`/`size_hint_gb`/`download_hint`/`scan_names`，与 `render._db` 的默认子目录对齐。
- `discover(name, cfg, scan_roots=None)`：显式 config 路径 → 环境变量 → 文件系统扫描
  （glob，非 os.walk，有界）→ 默认位置；三态 `ready/wrong_path/missing`，回读版本。
- `validate(name, path)`：只读 sentinel + 版本；`required_databases(cfg)` 按 route+开关推导，
  不报当前路线用不到的库。
- 新增 8 项测试（scan/env/wrong_path/missing/validate/三种 required 组合）。
- 验证：`unittest` 69→**77** 全绿；`bash -n` 全模板 OK；本机实测 `discover('gtdbtk')`
  →`ready scan r232 /home/h1020/gtdbtk_data/release232`（环境变量未设，纯靠文件系统扫描命中，版本读自 metadata）。

### Phase 3 — `decide/planner.py`（commit `1bb91d7`）

- `recommend_parallel(cores, ram_gb, n_samples) -> Plan(jobs, threads_per_job, reason, memory_capped)`。
- 基线 `jobs=min(samples,cores)`，补内存约束 `jobs*PEAK_MEM_GB_PER_JOB(=24, 粗估) ≤ ram_gb`；
  保证 `jobs*threads_per_job ≤ cores`；RAM 未知(0)时不做内存约束并在理由里说明。
- 只决策资源类参数，不碰科学参数。
- 新增 4 项测试（内存压并发+理由含 OOM、乘积≤cores 全组合、充裕内存满并发、RAM 未知不压）。
- 验证：`unittest` 77→**81** 全绿；`bash -n` 全模板 OK。

### Phase 4 — Web 配置（commit `a223ce4` + `dab955c`）

- 4.2 抽 `metaglens/_theme.py`（`REPORT_CSS` + `LENS_SVG`，即原 report._CSS/_LENS 逐字节搬移）；
  `report.py` 改 `from ._theme import ...`。回归：报告输出经归一化时间戳后与重构前**逐字节一致**
  （唯一差异是 datetime 时间戳），并有测试断言 `report._CSS is _theme.REPORT_CSS`。
- 4.1/4.3/4.4 `express/webconfig.py`（stdlib `ThreadingHTTPServer`，绑 127.0.0.1、端口 0、
  一次性 token，无 token → 403）；只读 API：`/api/hardware|plan|samples|db|required-dbs`；
  `POST /save` 走同一 `Config.validate()`+`to_yaml`。请求逻辑抽成纯函数便于单测。
- 4.2 页面：自包含 HTML，复用共享皮（`REPORT_CSS`+`LENS_SVG`），logo 读单一 b64 资产
  （换资产即换 logo，代码不写死）；中英 `I18N` 内置，切语言只重渲染 UI。
- 4.5/4.8 `cli.py`：新增 `configure` 命令（`--lang`/`--no-browser`）；`init` 先问
  终端向导 / 网页（默认终端），网页路径调 `webconfig.serve`。终端向导保留。
- 新增 10 项测试：save 与直接 `to_yaml` **逐字节一致**；zh/en 两次 POST **逐字节一致**（i18n 不污染）；
  非法 payload 不写文件；四个 API；`build_page` 含 token/双语/皮；**活服务** 无 token→403 / 有 token→200 /
  POST /save 写 yaml；且断言绑定地址为 `127.0.0.1`。
- 验证：`unittest` 81→**91** 全绿；`bash -n` 全模板 OK；`py_compile cli.py` OK（本机无 typer，不 import 运行）。
- 安全自查：绑 `127.0.0.1`（有测试断言）、无 token 403、不写 DB 目录、无网络请求。

### Phase 6 — 监控页（commit `df40665`）

- `observe/monitor.py`：`collect(results_dir)` 读 `pipeline_status.json`（steps/started/
  finished/attempts + selected_steps 顺序）+ 当前阶段日志尾（`reports/logs/{step}.log`，glob 兜底）；
  `render_html` 复用 `_theme`（同交付报告皮），`<meta http-equiv="refresh">` 自刷新，
  状态色 completed/running/failed/pending；`write_monitor` 写 `results/monitor.html`。
- `cli.py` 新增 `monitor`（`--interval`/`--once`，循环重写；旁路，不影响 run）。与 `status` 共存。
- 新增 4 项测试：collect 选中 running 阶段+日志尾、render 含皮/refresh/各阶段/running 色、
  failed 标红+last_failure 块、write_monitor 自包含。
- 验证：`unittest` 91→**95** 全绿；`bash -n` 全模板 OK；`py_compile cli.py` OK。

### Phase 5 — 文档（commit `<pending-docs>`）

- `README.md`：Quick start 增 `configure`/`monitor`；新增「Web configuration」（127.0.0.1+token、
  与向导产出一致、语言无关、headless 端口转发）与「Live monitor」（file:// 自刷新、崩溃后仍可看、旁路）两节。
- 计划文档全部勾选框完成，各 Phase 完成备注齐全。
- 5.3 待办：通知 IDE 会话审查 git diff（由汇报环节完成）。


### Phase 7 — 嵌套目录的样本发现（commit `195934f`）

- 7.1 `_list_fastqs` → `_walk_fastqs(raw_dir, max_depth=3)`：迭代式 DFS，跳过隐藏目录，
  按 `resolve()` 记已访问目录去重 → 符号链接指回祖先不会死循环。
- 7.2 **按父目录分组后组内配对**（`_pair_in_group`）。跨目录配对在结构上不可能发生；
  组内 id 冲突则否决该命名约定（与原扁平语义一致），继续试下一个。
- 7.3 ID 推导：文件名全局唯一 → `filename`；否则父目录名唯一 → `dirname`；
  两者都冲突 → 报错并要求 manifest（不自行编号）。
- 7.4 `discover()` 返回 `Discovery(samples, pattern, layout, id_source)` NamedTuple。
- 7.5 向导新增「Exclude some samples」（写出校验过的 samples.tsv）；网页样本表可改 ID/勾选排除，
  `/save` 携带 `samples` 时写出 samples.tsv 并置 `sample_manifest`，全部过 `_validate()`；
  README 新增「Sample discovery」节说明约定/嵌套/ID 来源/manifest 兜底。
- 7.6 新增 9 项测试：布局1、布局2、**跨目录错配反例**、ID 与目录名双冲突报错、
  符号链接环、深度超限截断、扁平回归（ids/pattern/路径逐项断言）、网页改名写 manifest、重复 ID 被拒。

**实测输出（计划验证项，全部符合）**：
```
布局1: ['SampleA','SampleB'] | pattern: _R1/_R2 | layout: nested | id_source: filename
布局2: ['S1','S2']           | pattern: _1/_2   | layout: nested | id_source: dirname
        S1 -> S1/reads_1.fq.gz + reads_2.fq.gz     （配对严格在各自目录内）
        S2 -> S2/reads_1.fq.gz + reads_2.fq.gz
扁平:   ['S1','S2']          | layout: flat   | id_source: filename
```

- 门禁：`unittest` 95→**104** 全绿；`bash -n` 全 14 模板 OK；`py_compile` 全模块 OK。

**一处需确认的适配（非行为变更）**：7.4 要求 `discover()` 返回值增加两个字段，
故三处既有测试的 2 元解包改为 `discover(...)[:2]`（`tests/test_metaglens.py:181,188,211`），
断言值一字未改；`pipeline.resolve_samples` 用 `[0]` 不受影响。


### Phase 8 — P1 命令层（commit `8ad153e` + `860c556` + `12b0a5d`）

**8.1 `sense/tools.py::required_tools(cfg)`**（commit `8ad153e`）
- 19 条 `ToolSpec`（tool/command/group/steps/条件），依据**模板实际调用**而非 `ENV_GROUPS` 粗粒度包列表。
- 编码了两条 group 推导会漏掉的真实依赖：
  ① `03_read_mapping.sh` 调 `jgi_summarize_bam_contig_depths`（随 **metabat2** 发布）→ contig 路线
  开 `calc_depth` 也需要 binning 组工具；② `prodigal` 被 `09_contig` 与「eggNOG 但关掉 Prokka」的
  `08_annotation` 调用，**却完全不在 `ENV_GROUPS` 里**（见下「遗留」）。
- 实测：`mag_per_sample` 需 14/19；`contig_based+kraken2` 需 9/19。

**8.2 `doctor`**（commit `860c556`）
- `sense/doctor.py::build_report()` 纯函数产出 dict，表格与 `--json` 同源。
- **裁决 D-2**：不需要的工具照常列出、标 `not_needed`，**永不进 problems**；只有 `required_tools`
  里的缺失才算问题。
- 区分「包在 / 命令能跑」两个信号，新增 `package_only` 状态（`conda list` 有包但不在当前 PATH）。
- `--fix` 只装缺失、**不升级任何已有包**，且执行前 `typer.confirm`。
- `conda_env` 新增 `env_prefixes()` 以检查 `<prefix>/bin/<cmd>` 是否真存在。

**8.3 `db list/status/where/verify/get`**（commit `860c556`）
- `resolution_chain()`：四级链（config→env→scan→default）逐级给出候选与结论，**标出命中哪一级**。
- `verify` 只读；`plan_get()` **不下载不写盘**。
- `get` 按**裁决 B**：强制显式目标目录、空间需 ≥ 体积×1.2（解压峰值）、不足即拒绝、
  必须显式确认才执行；**无官方下载命令的库只打印官方指引，绝不编造 URL**。

**8.4 `plan`**（commit `12b0a5d`）
- `decide/plan.py`：阶段表（模式/时长/峰值内存/磁盘）+ 合计 + 缺库预警（附 `db get` 命令）
  + 资源预警（峰值内存/磁盘超限）；`ok=False` 时 CLI 退出码 2，可作 gate。
- 时长/内存**显式标注 ±50%** 并写明依据样本规模（`~40M read pairs (2x150bp) @ 8 threads/job`）。
- **D-6 附加项 B**：`--plain` 输出可粘贴纯文本摘要（无任何 Rich 标记，有测试断言），
  含「无 API key / 分析期间无外呼 / 无按次计费」声明。

**顺带修掉一个真实缺陷**：`hardware._disk_free_gb` 对尚不存在的 `work_dir` 返回 0 GB，
既显示误导又让磁盘预警短路失效；改为回溯到最近存在的父目录（`/tmp/mg_nope/deep` → 772 GB）。

**门禁**：`unittest` 113→**135** 全绿（8.1 +9、8.2/8.3 +13、8.4 +8、磁盘修复 +1）；
`bash -n` 全 14 模板 OK；`py_compile` 全模块 OK。

**实测输出**：
```
$ required_tools  mag_per_sample → 14/19 ; contig_based+kraken2 → 9/19
$ doctor (env=checkm2_env)  required=14 not_needed=5 ok=False  problems=15
$ db where gtdbtk
  1. config            (not set)
  2. env               $GTDBTK_DATA_PATH (unset)
  3. scan     <== USED /home/h1020/gtdbtk_data/release232
  4. default           /tmp/w/databases/gtdbtk
$ plan --plain (7 samples/32 threads) → TOTAL 10h02m / 168 GB peak / 158 GB disk
  + 标注 COARSE ±50%，checkm2 与 eggnog 缺失并给出 db get 命令
```

**遗留（需你裁决，我未擅自改）**：`prodigal` 是 `09_contig` 的必需工具，但不在
`conda_setup.ENV_GROUPS` 任何一组里 → `metaglens setup-env` 不会安装它，contig 路线
建完环境仍会缺工具。`doctor` 现在能正确报出来。是否把 `prodigal` 加进 `ENV_GROUPS["mag"]`？
这会改变 `setup-env` 的建环境行为，故未动。


### Phase 9 — prodigal 修复 + demo(桩) + CI（commit `4800f03` + `2e798c2` + `2924313`）

**9.A prodigal**（commit `4800f03`）
- `ENV_GROUPS["mag"]` 与 `PIPELINE_TOOLS` 均补 `prodigal`。
- **通用断言（比单修 prodigal 更值钱）**：`required_tools(cfg) ⊆ ∪ENV_GROUPS`，
  覆盖全部 4 条 route × 6 组开关组合；另加 `all_known_tools() ⊆ ∪ENV_GROUPS`。
- **验证这条断言真有牙齿**：临时撤掉 prodigal 后它按预期失败，报
  `mag_per_sample {'use_prokka': False}: required but no ENV_GROUP provides {'prodigal'}`。

**9.B `metaglens demo`**（commit `2e798c2`）
- `metaglens/demo/`：`stubs.py`（22 个桩，各吐下游真正会读的最小合法产物）+
  `runner.py`（建临时项目 → 渲染 → 桩 PATH 跑完 → 断言阶段/产物/report/monitor）+
  `__main__.py`（`python3 -m metaglens.demo`，**不依赖 typer/rich**，供 CI）。
- 桩链让**真实 contig 名贯通**：`bowtie2-build` 记 `.stubrefs` → 比对器回放为 `@SQ`
  → `samtools coverage` 据此汇总，`mag_abundance` 才能按 `<mag>|<contig>` 聚合。
- 覆盖 `mag_per_sample`（12 阶段）与 `contig_based`（7 阶段）。
- **建 demo 过程中它自己抓出两个真实缺口**：DAS Tool 的 `Fasta_to_Contigs2Bin.sh`
  辅助脚本、`samtools coverage` —— 真实脚本走到那里就停，`bash -n` 永远发现不了。
- 另外 Phase 0 的空表守卫在开发中真的触发过一次（MAG 名未贯通导致 0 taxa），说明守卫有效。
- 默认只写自己的临时目录（有测试断言 `$HOME` 内容前后不变）；失败时保留现场。
- README 显著标注 **不产生科学结果**。

**9.C CI**（commit `2924313`）
- `test` 任务：Python **3.8/3.10/3.12** × (compileall + unittest + `bash -n` 全模板 + 桩 demo)，
  只装 PyYAML，无网络无 conda。
- `package` 任务：构建 wheel 并断言各子包与模板确实在包内（针对"子包曾被 wheel 丢弃"的回归守卫）。
- 本地已等价验证：`compileall` OK、`bash -n` OK、`python -m metaglens.demo` 两路由 PASS、
  `python -m build --wheel` 成功且 8 个必需路径全部在包内（50 文件）。

**门禁**：`unittest` 135→**147** 全绿（9.A +5、9.B +7、磁盘/其它 +0；含 demo 端到端 2 条路由）；
`bash -n` 全 14 模板 OK；`py_compile`/`compileall` 全模块 OK。

**实测输出**：
```
$ python3 -m metaglens.demo
==> mag_per_sample   12 阶段全 completed → PASS
==> contig_based      7 阶段全 completed → PASS
PASS — 2 route(s); stub tools, no scientific output.

contig_based 的 10_community/SOURCE.txt:
  "Kraken2 contig-based composition ..."   ← 正是 §7-8 修复前不可达的死代码分支
mag_per_sample 的 report.html payload: qc=2 行 / mags=4 / taxa=1 / timeline=12
  （qc 有数据即 §7-1 fastp glob 修复仍生效）
```

**打包**：`pyproject` 补 `metaglens.demo` 子包（否则 wheel 装不到 demo）。


### Phase 10 — 产物验证 + 质量门禁（commit `36d5947` + `acc8d40`）

- `metaglens/state.py`：**语义级**验证，13 个阶段各写明可判定下界。核心是不停在"文件非空"——
  表头本身就让文件非空（§7-8 就是这么漏过去的）：`table_has_rows` 排除表头与注释后要求 ≥1 数据行、
  `fasta_has_records` 数 `>`、`gzip_has_reads` 检查真有 FASTQ 记录。
- `pipeline.run_step`：脚本报成功后**复核产物**，不通过则改回 `failed` 并记 `product_validation`，
  返回非零。demo 也改为复用同一验证器，避免两套契约漂移。
- `decide/gates.py` + `rules/gates.yaml`：6 条软门禁（retention/bins/MIMAG HQ/retained/reps/taxa），
  每条带 `id` 与人话 `hint`。默认 warn 不阻断，`block_*` 才独立阻断，`--strict` 提升 warn；
  指标算不出来记 `unknown` 而非失败（contig 路线不会因没有 CheckM2 被误判）。
- `metaglens gate [--stage] [--strict] [--json]`、`run --strict-gates`、report 新增 **Gates 标签页**（复用 `_theme`）。
- **实测**：真实 demo 产物 19 项验证全通过、6/6 门禁 pass 无误报；把 community matrix 截成只剩表头
  （18 字节，非空）→ 正确判失败；QC 保留率改 30% + CheckM2 改 55/18 → `block qc.retention_rate` + MIMAG 警告。
- 测试 +19（166→…）。

### Phase 11 — 失败归因（commit `5ccff44`）

- `rules/failures.yaml` 13 条（三类归因）：OOM 按退出码与按日志、四个数据库各自特征、磁盘满、
  空 glob、命令缺失、权限、缺输入、缺状态、调度器超时。
- `diagnose.py`：产物验证失败优先（原因已精确已知），否则按序匹配；**匹配不到时明确降级**为
  "未知失败 + 证据 + 日志位置"，有测试断言未知路径**不出现** memory/database/disk/permission 任一词。
- `cli._fail3` / `_print_diagnosis` 三段式（发生什么 / 为什么 / 下一步可复制命令）；
  `metaglens diagnose`；`run` 失败自动归因。
- **顺带修真实缺陷**：`from .diagnose import diagnose` 让函数覆盖同名子模块，导致
  `from metaglens.decide import diagnose` 拿到函数、属性访问全部报错（17 个测试暴露）。改为先导入子模块、
  函数另名 `diagnose_failure`。
- 调试中还修了 `glob.unmatched` 正则（真实消息里 `*` 在引号内）与规则优先级（glob 需在 tool 之前）。

### Phase 12 — 交互打磨（commit `ef147a0` + `85e0f47` + `54d0f0b` + `8ceb702`）

- `express/suggest.py`：difflib + 子串兜底。接入 `--only`/`--from`、route 名、`from_yaml` 未知键、
  `validate` 的 route/custom_steps。`routes.py` 用 difflib 直调以免循环依赖。
  实测 `04_bining→04_binning`、`taxonomy→07_taxonomy`、纯乱码不给假建议。
- `express/profile.py`：XDG 路径，只记 5 个机器级键；**只填空不覆盖**（`defaults_for` 跳过显式值）；
  损坏文件静默降级为空。
- `express/i18n.py`：交互层 en/zh，**交付物保持英文**（有测试：切 zh 后报告仍是英文 chrome）；
  缺翻译降级到 en 再到 key；有测试断言 en 的每个键都有 zh。
- `express/explain.py` + `knowledge/topics.yaml`：**30 个主题**（12 阶段 + 科学参数 + 失败 id + MIMAG），
  知识在**数据文件**非代码；查找支持精确/大小写/前缀，未命中给候选。
- 补 `status`/`validate` 的 `--json`；全局 `--lang`；`pyproject` 补 package-data 让 YAML 进 wheel。

### Phase 13 — 运行时可观测（commit `93032d2` + `efb3cc3` + `d4788a8`）

- `observe/resources.py`：`/proc/meminfo` + `disk_usage` + load average（标注为近似），
  目录遍历有上限防卡 UI；psutil 仅可选（有测试强制 import 失败仍可用）。
- `observe/progress.py`：**对着真实日志**写解析器，因此抓到两处自己的错误——宽松的完成正则把
  `sequences:` 当成单元名；`08_annotation` 只记起始行不记逐个完成，需要"起始数-1"兜底。
  解析失败降级为不定进度 + 工具提示（MEGAHIT `k=`、bowtie2 对齐率）。
  **静默措辞刻意不含 stalled/hung 即使是否定式**——扫读者只会看到那个惊悚词，不会看到 "not"。
- `observe/monitor.collect` 升级为**唯一采集层**，HTML 页与终端仪表盘同源（13.5）。
- `express/dashboard.py` + `metaglens watch` + `run --monitor`：**watch 严格只读**，
  有测试断言 watch 前后 `pipeline_status.json` 逐字节一致。

### Phase 14 — advisor + methods + repair（commit `7027197` + `47733cd` + `4119713` + `4ba9d2d`）

- `advisor.py` + `rules/advice.yaml`：8 条规则，**每条带理由与严重度**；含设计稿点名的两例。
  **科学参数只警告**：`scope: science` 无 advise 载荷，且 `applicable_changes` 再按
  `APPLICABLE_FIELDS`/`SCIENTIFIC_FIELDS` 双重过滤（测试断言只有科学规则命中时可写集合为空）。
  表达式求值改为**白名单 AST 走查**而非 `eval`（规则文件用户可编辑，eval 等于给它代码执行权）。
- `express/methods.py`：只写实际 completed 的阶段、版本取自 `tool_versions.txt`、
  缺失标 `[provisional]`。**修真实缺陷**：无版本号的行原样当版本 → 会打出
  `v[stub] fastp --version`，现判为未记录。
- `decide/repair.py`：白名单 4 个操作 / 3 个可写字段，`FORBIDDEN_FIELDS` 显式列出所有科学/输入/
  数据库/路由参数使拒绝可审计；上限 2 次；同特征即停；每次留脚本快照 +
  `repair_log.jsonl` 一行（含拒绝）；只重跑失败阶段。
- **反例测试齐全**：试改 `completeness_min`/`contamination_max`/`ani_threshold`/`min_contig_len`/
  `kmer_list`/`assembler` 全部抛 `RepairRefused`；未知操作、输入/数据库字段同样被拒。
- **端到端实测**：exit 137 → 归因 `oom.killed` → 并发 4→2 → 阶段恢复 → 证据完整 →
  `completeness_min`/`ani_threshold`/`assembler` 均未改动。

### Phase 15 — 收尾

- `README`：Quick start 补 6 个新命令；新增「Reliability: validation, gates, diagnosis」
  「Watching a run」「Recommendations and bounded repair」三节，写明语义级验证的理由、
  watch 只读语义、科学参数不自动改的边界。
- CI 新增「rules 与知识文件加载且格式正确」步骤——外置 YAML 是承重件，畸形文件必须让 CI 红
  而不是静默停用某个功能。
- **全量门禁**：`unittest` **248** 全绿（177→248，+71）；`bash -n` 全 14 模板 OK；
  `compileall` 干净；`python3 -m metaglens.demo` 两路由 PASS；wheel 67 文件、18 个必需路径齐全。


### Phase 16 — 真实环境验证发现（仅记录，未改设计/代码，待用户裁决）

**前提**：conda 在 `/home/h1020/miniconda3/bin/conda`，50 个环境；PATH 上无任何生信工具，
全部在各 conda 环境内。真实数据用 `~/FD/data/clean_reads/` 的 FWJ101/FWJ102
（Illumina 2×150），每样本抽样 30k 条至临时目录（**已删除**）。全程只读，未改动任何
conda 环境，未写 `~/gtdbtk_data`，未下载数据库。

**① 工具实测表（工具 → 环境 → 版本，均以"命令可执行"为准）**

| 工具 | 环境 | 版本 | 所属阶段组 |
|---|---|---|---|
| fastp | fastq_megahit | 1.1.0 | qc (01) |
| megahit | fastq_megahit / metawrap_env | 1.2.9 / 1.1.3 | qc (02) |
| seqkit | step_10_env（及多个 step_*_env） | — | qc (02) |
| bowtie2 | metawrap_env / metabolic_env | 2.3.5.1 / 2.5.4 | qc (03) |
| samtools | metawrap_env / metabolic_env | 1.9 / 1.12 | qc (03) |
| metabat2 | metawrap_env | 2.12.1 | binning (04) |
| maxbin2 / concoct / prokka / kraken2 / prodigal | metawrap_env | 各版本 | binning/mag |
| checkm2 | checkm2_env | 1.1.0 | mag (05) |
| drep | drep_env | 3.6.2 | mag (06) |
| gtdbtk | gtdbtk27 / gtdbtk | 2.7.2 / 1.2.0 | mag (07) |

**② 跑前检查在真实数据上全部准确**
- `doctor`：裸 PATH 下正确报告 16 项必需工具缺失；
- `plan`：正确解析 gtdbtk 为 `ready r232`（经 config `taxonomy_db` 路径），
  checkm2/eggnog 为 `missing` 并给出 `metaglens db get` 命令；
- `db list`：`~/gtdbtk_data/release232` 解析为 ready，版本 r232 读自 `metadata/metadata.txt`
  的 `VERSION_DATA=`（非猜目录名）——真实数据上路径解析链（config→env→scan→default）工作正常。

**③ 设计假设不成立：工具一环一装、高度分散（待裁决，未改设计/代码）**

`fastp`、`seqkit`、`megahit` 分属**三个不同** conda 环境，**没有任何单一环境能跑完
哪怕一个阶段组**（01_qc 需 fastp；02_assembly 需 megahit+seqkit，二者不同环境）。
而软件的 conda 模型是 `reuse=单环境` / `create=3 组环境`——**均不匹配这台共享服务器
"一工具一环境"的实际布局**。目标用户（共享服务器、工具零散）恰恰是这种布局，故此为
"设计假设不成立"级发现，而非个别 bug。

01→02→03 的真实跑因此**未执行**（用户在此步喊停）。

**候选方向（供用户裁决）**
1. 支持"每工具/每阶段指定环境"的细粒度 conda 模型；
2. `doctor` 增加"跨环境拼 PATH"的建议（探测各工具所在环境，给出可粘贴的 PATH）；
3. 文档明确说明 `reuse` 单环境模式的适用前提（所有工具须在同一环境）。

### Phase 17 — 配置页修复 + Phase 16 文档化（commit `9519932` + `801d70d` + 本备注）

- **17.A**（`9519932`）：`_theme.py` `.logo` 88px→**132px**（约 1.5 倍，用户要求）。
  共享主题，报告/配置/监控三页一起变大（期望的视觉统一）；header 已有
  `align-items:center`+`flex-wrap`，不挤压标题；移动端媒体查询加 `.logo{height:96px}`。
- **17.B**（`801d70d`）：`refreshPlan()` 硬编码 `n=1` 且取了 `samples-box` 从未使用，
  导致网页并行建议永远按 1 样本算（3 样本会建议 1×112 而非 3×37）。改为用模块级
  `SAMPLES.length||1`，删除死变量。补回归测试：断言生成 JS 中 `n` 绑定为
  `SAMPLES.length||1`、旧的 `var n=1;` 与死 fetch 不存在（URL 拼接两版相同，故断言绑定
  而非 URL）。**已负向验证**：还原旧 bug 该测试失败。顺带自查全页 JS，无其它
  "取了没用"或硬编码参数。
- **17.C**：Phase 16 发现整理入上节（工具实测表 + 跑前检查结论 + 多环境分散问题与候选方案）。
- **17.D 门禁**：`unittest` **249** 全绿（裸解释器，248→249）；`bash -n` 全 14 模板 OK；
  `compileall` 干净；`python3 -m metaglens.demo` 两路由 PASS。
