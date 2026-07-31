# MetaGLens — 提交给评委 / For Judges

> 外滩黑客松 · AI Coding 大赛 · 主题「全民 Coding」

**一句话**：一个把宏基因组分析从"要装 18 个工具、下 200GB 数据库、跑几小时"
变成"确定性、可审计、脱离本工具也能跑的 Bash 脚本"的软件——**由两个 AI agent
协作编写并互相独立审查**完成。

**One line**: software that turns shotgun-metagenomics from "install 18 tools,
download 200 GB of databases, wait hours" into deterministic, auditable Bash you
can run without it — **built by two AI agents developing in tandem and reviewing
each other**.

---

## 30 秒看点 / 30-second tour

打开 **`site/index.html`**（双击即可，无需任何服务器、无需联网）。从上到下：

1. **为什么存在** — 实验室服务器不许装计费型 AI agent，所以需要一个运行时零 AI 的确定性软件。
2. **AI Coding 历程** — AI4S 的 skill 编排 → AI Coding 蒸馏成确定性软件 → 产品里不留 AI。
3. **亲手配置 → 点击运行** — 真实的 12 阶段流程在几秒内端到端跑完（桩工具，见下方诚实声明）。
4. **看报告** — 由本次运行产物生成的自包含 HTML 报告。
5. **读脚本** — 某阶段渲染出的真实 Bash，证明产物可独立运行、可审计。
6. **⭐ 试着攻破它** — 点按钮让它改科学参数 / 夹带非法字段 / 用非白名单操作，
   当场看到**真实的**安全拒绝信息；合法的降并发操作则放行。
   **这些按钮调用的是流程本身用的同一段安全代码，不是演示花架子。**
7. **开发审计轨迹** — 真实的提交数 / 测试数（构建时从 git 与测试文件计算，非手写）。

Open **`site/index.html`** (double-click — no server, no network needed). It has
a bilingual EN/中文 switch in the top-right.

---

## 诚实声明 / Honesty note（重要）

演示使用**桩工具**（stub tools），**不产生任何科学结果**。页面顶部已显著标注。

真正运行的是**完整的控制流、状态机、产物验证和报告生成器**——只把耗时的生信工具
（fastp / MEGAHIT / CheckM2 / GTDB-Tk …）换成了几毫秒返回合法最小产物的桩。
这恰恰证明了本项目的核心卖点：**生成的脚本是独立的、脱离 MetaGLens 也能跑**。

The demo uses **stub tools and produces NO scientific results** — stated
prominently on the page. What runs is the *real* control flow, state machine,
product validation and report generator; only the slow bioinformatics tools are
stubbed. That is precisely what proves the generated scripts are standalone.

我们**绝不伪造科学数据**。攻击面板与审计数字全部来自真实调用与真实仓库。

---

## 为什么这是「AI Coding」而不是「AI4S 产品」

MetaGLens **运行时零 AI**：无 API key、无模型调用、无对外网络。这是面向封闭实验室
服务器的刻意设计原则。AI 的角色在**开发阶段**：两个 agent 把一套 AI4S 的 skill 编排
蒸馏成确定性软件，并互相审查——比如一方发现了 `nullglob` 字面量数组的隐藏 bug、
另一方用"把 bug 注回去"验证测试真能抓到它、并对自动修复的安全白名单做了多种攻击测试。
`docs/WORKLOG.md` 与 git 历史保留了完整轨迹。

MetaGLens runs with **zero AI**. The AI was in the *building*: two agents
distilled an AI-for-Science skill bundle into deterministic software and reviewed
each other — the full trail is in `docs/WORKLOG.md` and git history.

---

## 想自己起交互版？（可选）

静态站已足够看完整个故事。若想亲手在浏览器里跑（含现场攻击 `/api/attack`）：

```bash
pip install -e .          # 需要 Python 3.8+；运行测试仅需 PyYAML
metaglens showcase --open # 本地起站并打开浏览器
# 或对外（只读演示接口，绑 0.0.0.0）：
metaglens showcase --host 0.0.0.0 --port 8080
```

离线自检（几秒，桩工具，验证整条链路可跑）：

```bash
python3 -m metaglens.demo
```

## 仓库 / Repository

- 源码：`metaglens/`（CLI + 感知/决策/观测/表达四层 + 12 阶段 bash 模板）
- 设计与施工记录：`docs/DESIGN-intelligence-and-ux.md`、`docs/WORKLOG.md`、`docs/IMPL-PLAN-webconfig.md`
- 测试：`tests/test_metaglens.py`（`python3 -m unittest discover -s tests -t .`）

## 截图 / Screenshots

见 `screenshots/`（配置 → 运行 → 报告 → 攻击面板）。若目录为空，可打开
`site/index.html` 自行截取，或用户本人补充。
