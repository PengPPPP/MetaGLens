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

- [ ] 7.1 **递归扫描**:替换 `_list_fastqs` 为带**深度上限**(默认 3 层)的递归;
  必须**防符号链接环**(用 `resolve()` 记已访问 inode/路径);跳过隐藏目录。
- [ ] 7.2 **配对只能在同一父目录内进行**。绝对不允许把 A 目录的 R1 和 B 目录的 R2
  配成一对——这是递归实现最容易引入的致命 bug。实现上:先按父目录分组,再在每组内
  套用现有 4 种命名约定。
- [ ] 7.3 **样本 ID 推导顺序**(并把用了哪种记录下来):
  1. 文件名推导(与现有扁平逻辑完全一致);
  2. 若文件名推导出的 ID **在不同目录间冲突**(如布局2 全是 `reads`)→ 改用**父目录名**;
  3. 若父目录名仍冲突 → **报错并要求用户提供 manifest**,不得自行编号糊过去。
  **回归要求**:扁平布局的发现结果与 ID 必须与现在**逐字不变**(现有测试须全绿)。
- [ ] 7.4 **布局透明化**:`discover()` 的返回值除现有 `pattern` 外,增加
  `layout`(`flat` / `nested`)与 `id_source`(`filename` / `dirname`),供向导与网页
  展示"我是怎么判断的"——符合设计原则 4(解释权高于自动化)。
- [ ] 7.5 **用户可自己填/改**(用户明确要的第二条路):
  - 网页配置(Phase 4)的样本表改为**可编辑**:可改 `sample_id`、可勾选排除;
  - 终端向导补「排除部分样本」选项(设计稿 §5.1 已规划但未做);
  - `samples.tsv` manifest 仍是终极兜底,文档写明。
  改完仍须过 `_validate()`(ID 唯一、文件存在、无文件被两个样本共用)。
- [ ] 7.6 测试:两种嵌套布局各一例;跨目录错配的**反例断言**(A/B 目录不得互相配对);
  ID 冲突时回退到目录名;目录名也冲突时报错;符号链接环不死循环;深度超限被截断;
  **扁平布局回归不变**。
- [ ] 7.7 commit `feat(samples): recursive discovery for nested layouts with safe id derivation`。

**验证**:上述两种布局都能正确发现 2 个样本,ID 分别为 `SampleA/SampleB` 与 `S1/S2`。

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
