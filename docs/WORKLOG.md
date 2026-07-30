# MetaGLens 施工日志

> 本文档记录从 2026-07-28 起,对 `/home/h1020/MetaGLens`(软件版)所做的每一次
> 操作与决策,便于检查与复盘。
>
> 配套文档:[`DESIGN-intelligence-and-ux.md`](DESIGN-intelligence-and-ux.md)(设计稿)
>
> 记录约定:
> - 每项含 **动机 / 改动 / 验证 / 结论**,验证一律给可复现命令与实际输出。
> - 决策若与设计稿冲突,单独记在「决策与裁决」一节,写明依据的设计原则。
> - 未完成或已知遗留问题记在「遗留」一节,不隐藏。

---

## 0. 前情摘要(本轮施工之前)

一次全量 review 的结论,作为后续工作的基线。

**已确认对齐的部分**
- 14 个 shell 模板与 skills 版逐字节比对:除 `00_setup.sh`、`01_quality_control.sh`
  (本轮主动改动)外全部一致。
- `render.py` 提供的占位符与模板中的 `{{...}}` 完全对应;步骤级覆盖占位符
  (`BINS_DIR`/`CHECKM2_DB`/`INPUT_PATH`/`DATABASE_PATH`/`MAGS_DIR`/`EGGNOG_DB`/`KRAKEN2_DB`)
  只出现在各自对应的模板中,无错配。
- 4 条预设路由 + custom 路由全部渲染成功并通过 `bash -n`。

**已修复(设计稿 §7 技术债)**

| § 7 编号 | 问题 | 处置 |
|---|---|---|
| 1 | `report.py` fastp glob 写 `*.fastp.json`,模板写 `${SAMPLE}_fastp.json`,QC 标签页恒空 | 已修:改 `*_fastp.json`,样本名取 `stem[:-len("_fastp")]` |
| 2 | `count_fastq_reads` 每样本完整解压 4 次 FASTQ | 已修:新增 `fastp_read_count()` 读 fastp JSON |
| 3 | `wizard.py` 缺 `reuse_and_update` | 已修:补选项并收集 `update_tools` |
| 4 | `cli.py` 调私有 `pipeline._run_script` | 已修:提升为 `run_step()`,抽出共享 `select_steps()` |
| 6 | 无 git / 无测试 / 无 LICENSE / `__pycache__` 入树 | 已修:`git init`(未 commit)、`tests/` 45 项全绿、`LICENSE`(MIT)、`.gitignore`、清除 `__pycache__` |
| 5 | `samples.py` 用内置 `callable` 作类型标注 | **未修** → 本轮 P0-3 |
| 7 | SBATCH 输出路径为相对路径 | **未修** → 本轮 P0-4 |

**额外修复(设计稿未列)**
- 报告不显示原始数据目录:`report.py` 的 `rawdata` 恒为 `""` 且 HTML 无对应元素。
  已修:`generate_report(raw_data_dir=...)` + 头部 chip + 从 status 回退读取。
  同时让 `00_setup.sh` 把 `raw_data_dir` 写入 `pipeline_status.json`。
  (注:skills 版内嵌的 shell 报告本来就显示该字段,此修复是让 `report.py` 与之对齐。)
- `metaglens run --only/--from` 传入非法步骤时静默跑空 → 改为快速失败并列出可用步骤。

---

## 1. 决策与裁决

### D-1 撤销 `metaglens envs`,能力并入 `doctor`

- **背景**:review 中我曾提议新增 `metaglens envs` 展示「环境 × 工具」矩阵。
- **冲突**:设计稿 §3.2 命令面已定义 `metaglens doctor [--env NAME] [--fix]`,
  §4.1 明确其职责即「conda 环境 × 18 个工具的版本矩阵」,与 `envs` 完全重叠。
- **裁决**:**撤销 `envs`**,该能力并入 `doctor`。
- **依据**:设计原则 1(新手优先)——命令面出现两个功能重叠的命令只会增加困惑。

### D-2 「路线需要」的呈现口径:标注而非过滤

- **背景**:我曾提议 `missing_tools()` 只检查当前路由需要的工具。
- **分歧**:设计稿 §4.1 要求「区分'路线需要'与'全部工具',当前路线用不到的
  **只提示不报错**」,样例为
  `bwa-mem2 — ✗ missing (align_tool=bowtie2, 当前路线不需要)`。
- **裁决**:采纳设计稿口径。**全部展示 + 标注不需要的原因**,不过滤。
- **依据**:我的做法隐藏信息;设计稿的做法保留信息但降噪,对新手更友好。

### D-3 五条 conda 改进拆分到三个阶段,不一批交付

原计划把 5 条一次做完,与设计稿 §8 排期冲突,会导致**向导被改两次**。拆分如下:

| 原编号 | 内容 | 处置 | 阶段 |
|---|---|---|---|
| #1 | 稳健发现 conda | 保留,升级为返回可执行路径 | **P0**(补入 §7) |
| #3 | 校验环境存在性 | 保留,三态区分 | **P0**(补入 §7) |
| #4 | 路由相关性 | 按 D-2 改为标注 | P1(`doctor`) |
| #5 | `metaglens envs` | 按 D-1 作废 | — |
| #2 | 向导可选环境列表 | 推迟,与 §5.1 向导重构合并 | P3 |

### D-4 向设计稿 §7 追加两条技术债

设计稿 §7 未收录以下两项,但它们是确定性 bug,且是 §4.1 `doctor` 的地基
(`doctor` 完全建立在 `conda_env.py` 上,底座不稳则整个 P1 失效):

- **§7-8**:`conda_available()` 仅用 `shutil.which("conda")`,当 conda 是 shell
  函数或当前 shell 未激活 base 时误判为「无 conda」,环境检测静默降级为空。
- **§7-9**:`installed_packages()` 用 `except: return {}` 吞掉所有失败,导致
  「环境不存在」与「环境存在但没装工具」无法区分,`missing_tools()` 对不存在的
  环境谎报 18 个工具缺失。

实测证据(本机):

```
$ which conda        -> 找不到(rc=1)
$ echo $CONDA_EXE    -> 空
$ ls ~/miniconda3/bin/conda -> 存在

>>> conda_env.conda_available()                        -> False   # 误判
>>> conda_env.missing_tools('definitely_not_an_env_xyz')
    -> 18 个工具全部"缺失"                                        # 谎报
>>> conda_env.installed_packages('definitely_not_an_env_xyz')
    -> {}                                                         # 失败被吞
```

### D-5 P0 优先,先清地基

设计稿 §8 规定「P0 先于所有新功能」。本轮只做 P0,不碰 `doctor`/`plan` 等新命令。

### D-6 软件版存在的真实约束:准入与经费,而非技术能力

**背景**。一次题外讨论中确认了两件事:

1. 通过 Qoder/VSCode 从 Windows 远程连服务器,skill 是**可用**的——skill 装在
   服务器的 `~/.qoder/skills/`,agent 的工具调用在服务器上执行。本项目的施工
   会话本身就是例证。
2. 但真实约束不是「装不上」,而是「**不允许装**」——导师/管理方因**计费**不批准
   在服务器上安装 agent 软件;学生自己愿意承担这笔钱,但签批权与服务器
   管理权不在学生手上。

**对我上一轮说法的更正**。我曾建议把设计稿 §0/§6 中「目标用户就是装不了 agent
的人」改得「更准」,理由是技术上装得上。**这个判断是错的**:技术障碍可以绕
(换方案、自己编译),而制度与经费准入**绕不过去**。此类约束比技术障碍
**更刚性**,因此设计稿的论据不是被削弱而是被强化。措辞宜从「装不了」改为
「**无权或无经费在服务器上引入计费型 agent**」。

**对设计的三条推论**

1. **§6(AI 顾问层暂不实施)的结论更稳了。** 如果卡点是计费审批,那么一个需要
   API Key 的可选层对目标用户是**双重无效**的:不仅没有 key,而是制度上不得有。
2. **原则 2(离线优先、无 AI 亦完整)不只是技术偏好,而是准入前提。**
   任何「付费后更好用」的设计都会把核心用户推到二等公民位置。
   零密钥、零网络出口、零单次费用应作为**可对外陈述的产品特性**写进文档——
   它是学生用来说服导师的论据,不只是实现细节。
3. **两阶段工作流是合法且应被支持的。** 学生可在自己有权限的地方用 skill/agent
   推演参数与方案,再把**确定性的软件 + 可独立运行的 bash 脚本**放到服务器上跑。
   跨过「准入边界」的载体正是那些独立 bash 脚本——这反向印证了原则 6
   (生成物始终可独立运行)与 §9 不改用 Snakemake 的决策:一旦产物需要额外运行时,
   它就跨不过这道边界了。

**一个新的使用场景(设计稿未记)**。`metaglens plan` 除了预防失败,还有一个
价值:它输出的磁盘/时长/内存预估是学生向导师或管理方**申请资源时的依据**,
且能同时证明「本流程不产生任何计费」。建议在 P1 实现 `plan` 时保留一个
可导出/可粘贴的纯文本摘要形式。

**已裁定(用户确认：全部采纳)**。待确认项已结:同步修正设计稿措辞,并采纳两条附加项。
实际涉及 **4 处**(而非最初说的 §0/§6 两处),已核定行号:

| 位置 | 原文 | 改为 |
|---|---|---|
| §0 用户画像 `:15` | 不方便安装 AI agent | 无权或无经费在服务器上引入**计费型** agent |
| §2 原则 2 `:67` | 目标用户就在**装不了** agent 的服务器上 | ……就在**不被允许**引入计费型 agent 的服务器上 |
| §6 理由 1 `:680` | 目标用户就是**装不了** agent 的人 | ……就是**不被允许**装计费型 agent 的人 |
| §9 `:808` | 服务器上**不方便装** agent | 服务器上**不被允许**引入计费型 agent |

附加项(属“加内容”而非改措辞,已一并采纳):

1. **原则 2 升格**——§2 补述「零密钥 / 零网络出口 / 零单次费用既是准入前提,
   也是可对外陈述的特性(学生用来说服导师的论据)」。
2. **`plan` 纯文本摘要**——§4.3 补一条:`plan` 输出需有可粘贴的纯文本形式,
   供向导师/管理方申请资源时使用,并能同时证明「本流程不产生计费」。
   **注:这是功能需求,不是文档改字**——计入 P1 `plan` 的工作量。

**执行分工**:设计稿由另一个 Qoder 会话维护(见 §6 并行开发说明),
裁定已转达给它执行;本会话不直接改设计稿,只维护本日志与代码。

---

## 2. 本轮 P0 任务清单

| 编号 | 内容 | 来源 | 状态 |
|---|---|---|---|
| P0-1 | 稳健发现 conda 可执行文件 | D-4 §7-8 | ✅ 完成 |
| P0-2 | conda 环境查询三态区分 | D-4 §7-9 | ✅ 完成 |
| P0-3 | `samples.py` 类型标注修正 | 设计稿 §7-5 | ✅ 完成 |
| P0-4 | SBATCH 输出路径改绝对路径 | 设计稿 §7-7 | ✅ 完成 |

验收:全部改动有测试覆盖,`python3 -m unittest discover -s tests -t .` 全绿
(45 → **59** 项)。至此设计稿 §7 技术债 **7/7 全部清空**,D-4 追加的 2 条亦已清空。

---

## 3. 施工记录

### P0-1 + P0-2 稳健发现 conda + 三态区分(`conda_env.py` 重写)

两项根因相邻(都源于 `conda_env.py` 对失败的处理过于粗糙),一并施工。

**动机**

原实现有两处静默失败:
1. `conda_available()` 只用 `shutil.which("conda")`。`conda init` 的标准安装把
   `conda` 装成 **shell 函数**,非交互子进程的 PATH 里没有它,于是在明明装了
   conda 的机器上判定「无 conda」,环境检测静默降级为空。
2. `installed_packages()` 用 `except Exception: return {}` 吞掉一切失败,使
   「环境不存在」与「环境存在但没装包」不可区分 → 向导会建议把 18 个包装进一个
   **不存在的环境**。

**改动**

- 新增 `find_conda()`,四级探测:`PATH` → `$CONDA_EXE` → `$CONDA_PREFIX`
  (含 `<base>/envs/<name>` 时上溯两级取 base)→ `$HOME` 与 `/opt` 下的常见发行目录。
- 新增异常层次:`CondaError` → `CondaUnavailable`(conda 不可用)/
  `EnvNotFound`(环境不存在)。空 dict 现在**只**表示「环境存在但无包」。
- 新增 `env_exists()`;新增 `_env_selector()` 支持 prefix 路径(`-p`)与命名环境(`-n`)。
- `installed_packages()` happy path 仍只调 1 次 conda;仅在失败时才多查一次以定位精确原因。
- 新增 `_env_name()`:conda 把 base 环境报为安装根目录,原实现会显示成
  `miniconda3`,现修正为 `base`。
- 同步修 `conda_setup.py` 的**同一 bug**:它也用 `shutil.which`,且 `conda create`
  用的是裸 `conda`。改为复用 `find_conda()` 并使用解析出的绝对路径,
  否则 `metaglens setup-env` 在本机会直接失败。
- `wizard.py` 适配新异常:`EnvNotFound` 明确报「环境不存在」并提示先
  `metaglens setup-env`,**不再**建议往不存在的环境装包;conda 不可用时给出提示。

**验证**(本机真实 conda,`shutil.which` 探测失败的场景)

```
shutil.which('conda')  -> None            # 旧实现在此失效
find_conda()           -> /home/h1020/miniconda3/bin/conda
conda_available()      -> True
list_envs()            -> 50 envs   ['base', 'bamm_env', 'cctyper_env', ...]
```

**修复前后对比:环境发现数 0 → 50。**

三态区分与 happy path:

```
missing_tools('definitely_not_an_env_xyz')
  -> EnvNotFound: conda environment not found: 'definitely_not_an_env_xyz'
     (旧实现:谎报 18 个工具全部缺失)

inventory('checkm2_env', ['checkm2','drep','fastp','gtdbtk'])
  -> {'checkm2': '1.1.0', 'drep': 'missing', 'fastp': 'missing', 'gtdbtk': 'missing'}
env_exists('checkm2_env') -> True
env_exists('nope_xyz')    -> False
'base' in list_envs() -> True ; 'miniconda3' in list_envs() -> False
```

**结论**:达成。新增 12 项测试(含 `find_conda` 四条探测路径的 mock 用例、
`EnvNotFound` 与「空环境」的区分、`conda_setup` 使用解析路径、base 命名)。

---

### P0-3 `samples.py` 类型标注修正

**动机**:`_PATTERNS` 第三个元素标注为内置函数 `callable` 而非类型,类型检查器无法校验。

**改动**:
```python
# 前
_PATTERNS: List[Tuple[str, "re.Pattern[str]", callable]] = [
# 后
_PATTERNS: List[Tuple[str, "re.Pattern[str]", Callable[["Match[str]"], str]]] = [
```
`typing` 导入补 `Callable`、`Match`。顺带删掉 `conda_setup.py` 中已失效的 `shutil` 导入。

**验证**:`py_compile` 通过;样本配对的 4 种命名约定测试全部照旧通过(纯标注改动,无行为变化)。

**结论**:达成。

---

### P0-4 SBATCH 输出路径改绝对路径

**动机**:13 个模板的 `#SBATCH --output=metaglens_results/reports/logs/...` 是相对路径,
只有从 `work_dir` 提交作业时才正确;从其它目录 `sbatch` 会因日志目录不存在而失败或写错位置。

**改动**(选型说明):`#SBATCH` 是注释指令,由 sbatch 直接解析,**无法**使用脚本内的
`${WORK_DIR}` 变量,必须在渲染期就写成绝对路径。而模板内 `RESULTS_DIR="${WORK_DIR}/metaglens_results"`
统一由 `WORK_DIR` 推导,因此:

- `render.build_global_values()` 把 `WORK_DIR` 渲染为
  `Path(cfg.work_dir).expanduser().resolve()`,**一处改动修好 13 个文件**;
- 13 个模板的指令改为 `#SBATCH --output={{WORK_DIR}}/metaglens_results/reports/logs/...`。

附带收益:生成的脚本不再依赖 cwd,可从任意目录运行——契合设计原则 6
(生成物始终可独立运行)。且样本路径与 manifest 本来就是绝对路径,
改后整个脚本的路径口径一致。

**验证**:新增 2 项测试,其中一项对 `mag_and_contig` 全部 13 个阶段逐脚本断言
`--output` 以 `/` 开头(用故意设成相对的 `work_dir: ./relative_work` 触发)。

**结论**:达成。

---

### 本轮总验证

```
$ python3 -m unittest discover -s tests -t .
Ran 59 tests in 0.657s
OK

$ python3 -m py_compile metaglens/*.py tests/*.py     -> OK
$ bash -n metaglens/templates/*.sh (14 个)             -> OK
```

---

## 4. 遗留与影响面

### 4.1 与 skills 版的模板分叉扩大:2 → 13

本轮 P0-4 修改了全部 13 个含 SBATCH 指令的模板,分叉现状:

| 文件 | 分叉内容 |
|---|---|
| `00_setup.sh` | ① `raw_data_dir` 写入 status ② SBATCH 绝对路径 |
| `01_quality_control.sh` | ① `fastp_read_count()` 复用 fastp JSON 计数 ② SBATCH 绝对路径 |
| 其余 11 个 | 仅 SBATCH 绝对路径(每文件 1 行) |

分叉是**机械且可复制的**——除前两个文件外都只是同一行的同一处替换。按既定决策
不回同步 skills;若将来要同步,把 `#SBATCH --output=` 前缀加上 `{{WORK_DIR}}/` 即可。

### 4.2 未处理项

- **`git` 仍未 commit**。已 `git init` 并配好 `.gitignore`,但按约定不代为提交,
  等你决定提交时机与 commit message。
- **`LICENSE` 署名为 "MetaGLens" / 2026**,若需改为真实姓名请告知。
- **P1 前置件未做**:设计稿 §4.1 要求区分「路线需要 / 全部工具」,需要一个
  `required_tools(cfg) -> {tool: reason}`。现有 `PIPELINE_TOOLS` 是扁平 18 项常量,
  缺少「路由 + 配置开关 → 实际所需工具」的映射。该函数是 `doctor`/`plan`/`recommend`
  三个命令的共同底座,建议作为 P1 第一项。
- **`list_envs()` 无缓存**:`doctor` 若要扫描 N 个环境,`env_exists()` 会重复调用
  `conda env list`。当前只在失败路径触发,影响可忽略;P1 实现 `doctor` 时再评估是否加缓存。

---

## 5. 下一步(待你确认)

设计稿 §8 的 P0 尚有两项未做,均不在本轮范围内:

1. **`metaglens demo`** — 内置迷你数据集端到端自检。设计稿把它排在 P0 最前,
   理由是「新手的第一次成功体验 + 后续所有改动的回归网」。
2. **CI** — 目前测试只能本地手动跑。

是否接着做 `demo`?或者先转 P1(`sense/` + `doctor`/`db`/`plan`)?

---

## 6. 并行开发说明(两个 Qoder 会话同仓库)

### 6.1 现状与分工

本仓库同时有**两个 Qoder 会话**在工作:

| 会话 | 位置 | 职责 |
|---|---|---|
| 本会话 | IDE | 代码与测试实现、本日志 |
| 另一会话 | tmux `metaglens_qoder`(pane 跑 `qodercli`) | 维护 `DESIGN-intelligence-and-ux.md` |

两边通过 `tmux send-keys` / `capture-pane` 互通。商定的边界:
**设计稿归对方,`metaglens/` 与 `tests/` 归本会话**,以免互相覆盖。

> ⚠ **风险：仓库至今无任何 commit。** 已 `git init` 但未提交,两个会话并行改文件
> 而无基线,一旦覆盖**无法回滚**。已多次向用户提请做基线提交,待处理。
>
> **✅ 已解决(2026-07-30)**:基线提交 `0b873d9`(37 文件 / 8327 行)已落地。
> 同时统一署名:git 全局身份 + LICENSE + pyproject 均为 `PengPPPP` / Fudan University。
> git 邮箱用 GitHub noreply(`185229444+PengPPPP@users.noreply.github.com`),
> 使提交在 GitHub 上归属 `PengPPPP` 且不暴露真实邮箱。
> `.gitignore` 补排除 `.qoder/`(IDE 本地权限缓存)。

### 6.2 对方发现的 §7-8:`10_community_summary.sh` 字面量数组 bug

这是**本会话全量 review 时漏掉的**真 bug,由对方发现,本会话独立复现确认。

**根因**。`10_community_summary.sh:67`
```bash
shopt -s nullglob
GTDB_SUMMARIES=("${TAX_DIR}/gtdbtk/gtdbtk.bac120.summary.tsv" "...ar53.summary.tsv")
```
`nullglob` 只对**含通配符的模式**生效;这两个是字面量,所以
`${#GTDB_SUMMARIES[@]}` **恒为 2**,与文件是否存在无关。实验验证:
```
含通配符数组计数 : 0     <- nullglob 生效
字面量数组计数   : 2     <- 文件不存在,仍为 2
```

**后果**。`:76` 的 `elif` 恒真 → `contig_kraken`、`kraken_report` 两个分支成为
死代码,`else` 报错分支不可达;`:74` 的 `&& -gt 0` 是空条件。

**本会话补充的两点**

1. **失败模式比「空矩阵」更糟**:`gtdbtk` 分支的 python 对缺失文件走 `continue`,
   `counts` 保持空,只写出表头、零数据行,**退出码 0**;而 `:297` 只是
   `NUM_TAXA=$(wc -l)-1` 记了日志,**从不校验 >0**,紧接着 `update_step_status completed`。
   所以这同时是设计稿 §4.4「产物验证」缺口的真实实例——光改 glob 不够。
2. **修复后才会暴露的连带问题**:`contig_taxonomy` 默认为 `none`,而
   `09_contig_analysis.sh:174` 只在 `==kraken2` 时才产出 `*_contig_report.txt`。
   故 `contig_based` 走默认配置时:

   | 状态 | 行为 |
   |---|---|
   | 修复前 | SOURCE 误判 `gtdbtk` → 空矩阵 + exit 0 + 标 completed |
   | 修复后 | 五个来源全为 0 → `else` → **exit 1,阶段 10 硬失败** |

   **两种状态都是坏的**。因此 §7-8 的修法必须连带决定这个组合的正确行为。
   建议:主用 fail-fast——在 `validate`/`plan` 阶段就拦(`analysis_basis=contig`
   且 `selected_steps` 含 `10_community` ⇒ 要求 `contig_taxonomy=kraken2`),符合
   §4.3「跑之前就拦住错误配置」;若用户显式不要 taxonomy,则阶段 10 优雅跳过
   并在 `DATA_DICTIONARY` 说明缺失原因,而不是交付一张空表。
   不建议把默认改成 `kraken2`——会静默引入一个数据库依赖。

**同类写法全模板扫描:仅此一处。**

> 记一笔自己的错:本会话第一版扫描器误报了 `06_dereplication.sh:65 GENOMES`
> 与 `08_annotation.sh:67 MAGS`。原因是它们是**多行数组赋值**、通配符在后续行,
> 而扫描器只匹配了赋值首行。查阅源码后已自行纠正。教训:**报 bug 前先读源码**。

**状态**:§7-8 待修(归本会话)。修法 = 真 glob + `NUM_TAXA` 守卫 +
上述 fail-fast + 回归测试。

---

## 7. 连续执行成果审查(2026-07-30,用户休息期间)

实现方(tmux 会话)连续完成 **Phase 0→6**,IDE 会话逐项独立复核。**结论:全部通过。**

### 7.1 提交链

```
27304f4 fix: §7-8 community-source nullglob & empty-matrix guard   (Phase 0)
6741141 feat(sense): hardware probing with stdlib fallback          (Phase 1)
51f52d1 feat(sense): database registry, discovery, validation       (Phase 2)
1bb91d7 feat(decide): parallel plan recommendation with rationale   (Phase 3)
a223ce4 feat(express): metaglens configure — local web config       (Phase 4)
dab955c feat(cli): init offers shell-wizard or web config           (Phase 4.8)
df40665 feat(observe): live self-refreshing monitor.html            (Phase 6)
6bbbf43 docs: README for configure & monitor
6a57fb8 build: include sense/decide/express/observe subpackages
```

测试 **59 → 95 项全绿**;`bash -n` 过全部 14 个模板;工作区干净。

### 7.2 用户硬要求的独立验证(不采信声明,只看代码与实跑)

| 要求 | 验证方式 | 结果 |
|---|---|---|
| 风格与报告全对齐、只换 logo | 抽出 `metaglens/_theme.py`(`REPORT_CSS`+`LENS_SVG`),`report.py` / `webconfig.py` / `observe/monitor.py` **三处同 import**;调色板全仓只出现一次 | ✅ 单一事实来源 |
| logo 可替换、不写死 | `_load_logo_b64()` 读单一 b64 资产 | ✅ |
| 中英切换、不污染配置 | 真起服务,zh 与 en 各 POST 一次同输入 → yaml **逐字节一致**,且不含 `lang`/`token` 键 | ✅ |
| 方案 B 本地小服务 | 实跑:绑 `127.0.0.1`、无 token → **403**、带 token → 200(306 KB 自包含页) | ✅ |
| 共享服务器安全 | 未出现 `0.0.0.0`;token 用 `secrets.token_urlsafe(24)` + `compare_digest`(恒定时间) | ✅ |
| 离线优先、不加重依赖 | `dependencies` 仍为 PyYAML/typer/rich;无 Flask/FastAPI/requests;`psutil` 在函数内 try-import,仅作交叉校验 | ✅ |
| 数据库指定路径 + 校验 + 缺失指引 | 实跑 `/api/db`:扫到 `~/gtdbtk_data/release232`,并从 `metadata/metadata.txt` 读出 **r232**(非猜目录名) | ✅ |
| 实时监控页(方案 S) | 实跑 `write_monitor()`:自刷新 meta、四阶段状态、日志尾(`k=99`)、失败态渲染、无外部资源 | ✅ |
| 终端功能保留 | `run_wizard` 仍在;`status` 命令仍在;`init` 默认走终端向导 | ✅ |
| 并行推荐带理由 | 实测 16c/64G/8样本 → 压到 2×8(因 ~24 GB/job × 8 > 64 GB),`jobs*threads ≤ cores` 恒成立 | ✅ |

### 7.3 审查中我自己的两次误判(留档)

1. **误以为没抽共享视觉模块**。实际抽到了 `metaglens/_theme.py`——计划原文给了
   `express/theme.py` **或** `metaglens/_theme.py` 两个位置,我只查了前者。
2. **误报内存探测有 bug**。我曾用 `SC_PAGE_SIZE * SC_PHYS_PAGES` 算出 1081.5 GB,
   与 `hardware.py` 报的 498 GB 冲突。核 `/proc/meminfo`(MemTotal 522240000 kB)与
   `free -g`(total 498)后确认:**`hardware.py` 正确,我的 sysconf 算法在本机失真**。
   `_BYTES_PER_GB = 1024**3`(GiB),与 `free -g` 同口径,内部一致。

教训同 §6.2 末尾那条:**下结论前先读源码/查权威值**。

### 7.4 遗留

- 设计稿 §8 的 P0 仍有 **`metaglens demo`**(迷你数据集端到端自检)与 **CI** 未做。
- P1 尚未做完的部分:`doctor` / `db` / `plan` 三个命令本身(底座 `sense/` 已具备)、
  `advisor.py` 规则引擎、`gates` / `diagnose` / `repair`(P2/P4)。

---

## 8. Phase 10–15 审查(2026-07-31,用户休息期间连续执行)

实现方连续完成 **Phase 10→15**(设计稿 P2/P3/P4 全部剩余部分),15 个功能提交,
新建 10 个模块。IDE 会话独立复核并**做了攻击式验证**。

### 8.1 提交链

```
36d5947 feat(state): semantic product validation                 (Phase 10)
acc8d40 feat(decide): quality gates, gate command & --strict-gates
5ccff44 feat(decide): failure diagnosis rules & three-part errors (Phase 11)
ef147a0 feat(express): did-you-mean suggestions                   (Phase 12)
85e0f47 feat(express): user-level profile
54d0f0b feat(express): interactive language selection (en/zh)
8ceb702 feat(express): explain — offline knowledge base
93032d2 feat(observe): resource sampling & progress parsers        (Phase 13)
efb3cc3 feat(express): live terminal dashboard
d4788a8 feat(cli): watch command & run --monitor
7027197 feat(decide): parameter advisor with externalised rules    (Phase 14)
47733cd feat(express): generate Methods from what actually ran
4119713 feat(decide): bounded self-repair with a non-negotiable boundary
4ba9d2d feat(cli): recommend, methods & run --auto-repair
3446359 docs: README for the new commands
dc770c1 test: skip rich-dependent dashboard cases (IDE 会话修)
```

新模块:`state.py`、`decide/{gates,diagnose,repair,advisor}.py`、
`observe/{resources,progress/}`、`express/{dashboard,explain,i18n,methods}.py`。
规则全部外置:`decide/rules/{gates,failures,advice}.yaml`、`express/knowledge/topics.yaml`。

### 8.2 发现并修掉的问题:测试在裸解释器下失败

实现方声明"门禁全绿",**实际有 3 个 ERROR**。根因:`test_dashboard_*` 与
`test_watch_once_*` 三例**无条件 `import rich`**,而本机与 CI 都不装 rich
(项目只依赖 PyYAML 跑测试)。这违反了既定约束——测试须在裸解释器可跑。

修法:加 `@unittest.skipUnless(_HAS_RICH, ...)`,**保留全部断言不删**
(其中"watch 必须永不修改运行状态"这条尤其值得留)。修后:**248 通过 / 3 跳过**。
commit `dc770c1`。

**教训**:自证门禁时,"我这里跑过了"不等于"在目标环境跑得过"。

### 8.3 攻击式验证(不看代码看行为)

**① `repair` 安全边界 —— 7 种攻击全部被拒**

| 攻击 | 结果 |
|---|---|
| `min_contig_len` / `completeness_min` / `contamination_max` / `ani_threshold` / `assembler` | 均 `RepairRefused` ✅ |
| 非白名单操作 `delete_outputs` | `RepairRefused` ✅ |
| **夹带**:合法 `parallel_jobs` + 非法 `min_length` 同时提交 | `RepairRefused` ✅(最阴险一条也挡住) |
| 合法的降并发 / 加内存 | 正常放行 ✅ |

白名单:4 个操作(`reduce_parallel`/`reduce_threads`/`increase_memory`/`retry`)、
3 个可改字段(`parallel_jobs`/`threads_per_job`/`memory`)、37 个禁止字段。
**科学参数在结构上动不了**——符合原则 5 与设计稿 §4.6 的不可协商边界。

**② `diagnose` 归因 —— 6 类命中且不编造**

```
exit 137  → oom.killed / environment      + 自动降并发建议
GTDB 缺库 → db.gtdbtk_missing             + "metaglens db where gtdbtk"
命令没找到 → tool.not_found                + "metaglens doctor"
磁盘满    → disk.full                     + "metaglens plan"
通配符空  → glob.unmatched / script_defect + "metaglens gate"
完全未知  → class=unknown「no known signature matched」+ 指向日志
```
每条建议都是可直接执行的命令(三段式落地)。**未知时如实报 unknown,不硬编造原因。**

**③ 产物验证 —— §7-8 的"表头非空"陷阱已堵死**

```
只有表头    → ok=False ✅   ← 正是当初漏过去的情形
有 1 数据行 → ok=True  ✅
文件不存在  → ok=False ✅
完全空文件  → ok=False ✅
```
且在跑测试时观察到它**真实生效**:
```
[metaglens] 10_community: the script reported success but its products did not
pass validation: community_matrix.tsv has 0 data row(s), expected >= 1
(a header line alone is not a result)
```
即**shell 说成功也会被 Python 侧翻掉**——设计稿 §4.4 点名的「当前最实质的可靠性缺口」补上了。

### 8.4 门禁复核

`unittest` **248 通过 / 3 跳过**;`bash -n` 全 14 模板 OK;`compileall` 干净;
`python3 -m metaglens.demo` 两路由 PASS;工作区干净。

### 8.5 当前进度

设计稿 **P0–P4 全部实现完毕**,外加用户后续提出的全部新特性:
网页配置(方案 B)、中英切换、实时监控页(方案 S)、嵌套目录样本发现。

### 8.6 下一步:**从"加功能"转向"质量收口"**

至今**所有验证都基于桩工具与合成数据**。真实工具的参数细节、版本差异、
数据规模效应只有真跑才会暴露。故下一步安排真实环境验证(见 `IMPL-PLAN-webconfig.md`
的 Phase 16),而非继续堆功能。

---

## 9. Phase 16 真实环境验证结果(2026-07-31)

实现方用真实 Illumina 2×150 reads(`~/FD/data/clean_reads/` 的 FWJ101/FWJ102,
每样本抽样 30k 条,临时目录已删)完成了 16.A/16.B/16.C 的**跑前检查**部分。

### 9.1 跑前检查在真实数据上全部准确

- `doctor`:裸 PATH 下正确报告 16 项工具缺失;
- `plan`:正确算出 gtdbtk `ready r232`(经 config 路径解析)、checkm2/eggnog `missing`
  并给出 `db get` 命令;
- `db list`:`~/gtdbtk_data/release232` 解析为 ready,版本 r232 读自 `metadata`
  (非猜目录名)——**真实数据上路径解析链工作正常**。

### 9.2 设计假设不成立的发现(待用户裁决,未擅自改设计)

**fastp、seqkit、megahit 分属三个不同 conda 环境,没有任何单一环境能跑完哪怕一个阶段组。**
而软件的 conda 模型是 `reuse=单环境` / `create=3 组环境`——**都不匹配这台机器
"一工具一环境"的布局**。实测工具分布:

```
fastp   → fastq_megahit
seqkit  → step_10_env
megahit → metawrap_env
(其余 binning/checkm2/drep/gtdbtk 各在独立环境)
```

01→02→03 的真实跑因此**未执行**(用户在此步喊停)。这不是 bug,是**设计对目标环境
的假设偏差**——目标服务器(共享、工具零散)恰恰是这种布局。候选方向(待裁决):
① 支持"每工具/每阶段指定环境"的细粒度模型;② `doctor` 增加"跨环境拼 PATH"建议;
③ 文档明确说明 reuse 单环境模式的适用前提。

### 9.3 IDE 会话预览配置页时发现的两个 UI/逻辑问题

- **logo 过小**:配置页 logo `height:88px`,用户要求放大(约 1.5 倍)。
- **并行建议硬编码 n=1**:`webconfig.py` 的 `refreshPlan()` 里 `var n=1`,
  取到的 `samples-box` 元素从未使用 → 网页并行建议**永远按 1 个样本算**
  (3 样本项目会建议 1×112 而非 3×37)。单元测试覆盖不到(只测后端 `/api/plan`),
  属"只有真点一遍才会发现"的前端缺陷。

以上两项 + Phase 16 文档化,已派实现方处理(见 IMPL-PLAN Phase 17)。
