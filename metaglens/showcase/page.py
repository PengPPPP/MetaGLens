"""The one-page showcase HTML.

Reuses the shared theme so the demo site looks like the report and config pages.
This module is the skeleton (Phase 18.A): the flow works end to end; the fuller
narrative copy (Phase 18.B) is layered on afterwards. All strings are static —
the page never interpolates anything taken from a request.
"""

from __future__ import annotations

import json

from .._theme import REPORT_CSS, LENS_SVG
from .jobs import DEMO_ROUTES, SHOWCASE_SCRIPT_STAGE


def _load_logo_b64() -> str:
    try:
        from importlib import resources
        return resources.files("metaglens.templates").joinpath(
            "report_logo.b64").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def build_page(static: bool = False, boot_extra: dict = None) -> str:
    """Return the self-contained showcase page.

    ``static`` marks an exported, backend-free build so the JS can fall back to
    pre-baked artefacts instead of calling the run API.
    """
    logo = _load_logo_b64()
    logo_src = f"data:image/png;base64,{logo}" if logo else ""
    boot = {"routes": list(DEMO_ROUTES), "static": static,
            "scriptStage": SHOWCASE_SCRIPT_STAGE, "logo": logo_src}
    if boot_extra:
        boot.update(boot_extra)
    return (
        _TEMPLATE
        .replace("/*__CSS__*/", REPORT_CSS)
        .replace("<!--__LENS__-->", LENS_SVG)
        .replace("/*__BOOT__*/", json.dumps(boot, ensure_ascii=False))
    )


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>MetaGLens — live demo</title>
<style>/*__CSS__*/
.hero{max-width:1180px;margin:0 auto;padding:8px 34px 0;}
.hero h1{font-size:34px;margin:6px 0;}
.lead{font-size:18px;color:var(--ink-soft);max-width:820px;}
.honesty{background:rgba(217,138,36,.12);border:1px solid var(--warn);border-radius:12px;
  padding:12px 16px;margin:16px 0;color:var(--ink);font-size:15.5px;}
.honesty b{color:var(--warn);}
.step-card{scroll-margin-top:20px;}
.runbtn{font-size:17px;padding:11px 22px;font-weight:700;background:var(--blue);color:#fff;border:none;}
.runbtn:hover{background:var(--blue-strong);color:#fff;}
.runbtn[disabled]{background:var(--line);color:var(--muted);cursor:not-allowed;}
.stagelist{display:flex;flex-direction:column;gap:4px;margin-top:10px;}
.stagerow{display:flex;gap:10px;align-items:center;font-size:15px;}
.dot{width:12px;height:12px;border-radius:50%;background:var(--line);flex:none;}
.dot.done{background:var(--good);} .dot.running{background:var(--warn);}
.dot.failed{background:var(--bad);}
.viewer{width:100%;height:560px;border:1px solid var(--line);border-radius:12px;background:#fff;}
pre.script{background:#0d1b2a;color:#cfe0f6;padding:16px;border-radius:12px;overflow:auto;
  max-height:460px;font-family:monospace;font-size:12.5px;line-height:1.5;}
.langsw{margin-left:auto;}
.audit{display:flex;gap:14px;flex-wrap:wrap;}
</style></head><body>
<!--__LENS__-->
<header>
  <img class="logo" id="logo" alt="MetaGLens"/>
  <div class="headline"><div class="t">MetaGLens</div>
  <div class="d">reads → MAGs, made reproducible</div></div>
  <div class="langsw"><button class="btn" onclick="setLang('en')">EN</button>
  <button class="btn" onclick="setLang('zh')">中文</button></div>
</header>

<nav id="nav"></nav>

<main>
  <section class="hero">
    <h1 data-i18n="title">See it run in your browser</h1>
    <p class="lead" data-i18n="lead"></p>
    <div class="honesty"><b data-i18n="honestyTag">Demo note</b> —
      <span data-i18n="honesty"></span></div>
  </section>

  <div class="card step-card" id="why"><h2 data-i18n="whyH"></h2>
    <p class="hint" data-i18n="whyP"></p></div>

  <div class="card step-card" id="configure"><h2 data-i18n="cfgH"></h2>
    <p class="hint" data-i18n="cfgP"></p>
    <div class="row"><label data-i18n="cfgProject">Project</label>
      <input type="text" id="f-project" value="demo_project"/></div>
    <div class="row"><label data-i18n="cfgRoute">Route</label>
      <select id="f-route"></select></div>
    <div class="reason" id="cfg-yaml"></div>
  </div>

  <div class="card step-card" id="run"><h2 data-i18n="runH"></h2>
    <p class="hint" data-i18n="runP"></p>
    <button class="btn runbtn" id="runbtn" onclick="startRun()" data-i18n="runBtn"></button>
    <span id="run-msg" class="hint" style="margin-left:12px"></span>
    <div class="stagelist" id="stages"></div>
  </div>

  <div class="card step-card" id="report"><h2 data-i18n="repH"></h2>
    <p class="hint" data-i18n="repP"></p>
    <iframe class="viewer" id="report-frame" title="report"></iframe>
  </div>

  <div class="card step-card" id="script"><h2 data-i18n="scrH"></h2>
    <p class="hint" data-i18n="scrP"></p>
    <pre class="script" id="script-box" data-i18n="scrWait"></pre>
  </div>

  <div class="card step-card" id="audit"><h2 data-i18n="audH"></h2>
    <p class="hint" data-i18n="audP"></p>
    <div class="flex" id="audit-stats"></div>
  </div>
</main>
<footer data-i18n="footer"></footer>

<script>var BOOT=/*__BOOT__*/;</script>
<script>
var I18N={
 en:{title:"See it run in your browser",
   lead:"MetaGLens turns raw metagenomic reads into genomes, taxonomy and a self-contained report — as standalone, inspectable Bash you can run without MetaGLens. Below, the real 12-stage pipeline runs end to end in seconds.",
   honestyTag:"Demo note",
   honesty:"This demo uses stub tools and produces NO scientific results. What runs is the real control flow, state machine, product validation and report generator — only the slow bioinformatics tools are stubbed. That is exactly what proves the generated scripts are standalone.",
   nav:["Why","Configure","Run","Report","Script","Audit"],
   whyH:"Why it exists",
   whyP:"Lab servers often forbid installing metered AI agents. MetaGLens is the deterministic answer: no API key, no outbound calls during analysis, no per-use cost — just Bash you own.",
   cfgH:"1 · Configure",
   cfgP:"Pick a route. On the full tool it discovers samples, checks hardware and databases, and writes a shareable metaglens.yaml.",
   cfgProject:"Project", cfgRoute:"Route",
   runH:"2 · Run",
   runP:"Click run. The real stage scripts execute against a stub toolchain and finish in seconds.",
   runBtn:"Run the pipeline",
   repH:"3 · Report",
   repP:"A self-contained HTML report, generated from this run's outputs.",
   scrH:"4 · Read the script",
   scrP:"The rendered Bash for one stage. This is what MetaGLens delivers — readable, auditable, runnable without it.",
   scrWait:"(run the pipeline to see a generated script)",
   audH:"Built and reviewed by two AI agents",
   audP:"Two agents developed this in tandem and independently reviewed each other, with a full WORKLOG and git trail.",
   footer:"MetaGLens · deterministic shotgun-metagenomics · demo uses stub tools, no scientific output"},
 zh:{title:"在浏览器里看它跑起来",
   lead:"MetaGLens 把宏基因组原始 reads 变成基因组、物种分类和一份自包含报告——产物是可独立运行、可审计的 Bash 脚本,不依赖 MetaGLens 本身。下面这条真实的 12 阶段流程会在几秒内端到端跑完。",
   honestyTag:"演示说明",
   honesty:"本演示使用桩工具,不产生任何科学结果。真正运行的是完整的控制流、状态机、产物验证与报告生成器,只把耗时的生信工具换成了桩——这恰好证明了生成的脚本可以独立运行。",
   nav:["为什么","配置","运行","报告","脚本","审计"],
   whyH:"为什么需要它",
   whyP:"实验室服务器常常不允许安装计费型 AI agent。MetaGLens 是确定性的答案:零密钥、分析期间零外呼、零按次计费——只有你自己拥有的 Bash。",
   cfgH:"1 · 配置",
   cfgP:"选择一条分析路线。在完整工具上它会发现样本、检查硬件与数据库,并写出可分享的 metaglens.yaml。",
   cfgProject:"项目", cfgRoute:"路线",
   runH:"2 · 运行",
   runP:"点击运行。真实的阶段脚本对着桩工具链执行,几秒内完成。",
   runBtn:"运行流程",
   repH:"3 · 报告",
   repP:"一份自包含的 HTML 报告,由本次运行的产物生成。",
   scrH:"4 · 阅读脚本",
   scrP:"某个阶段渲染出的 Bash。这就是 MetaGLens 的交付物——可读、可审计、脱离它也能运行。",
   scrWait:"(运行流程后即可看到生成的脚本)",
   audH:"由两个 AI agent 协作开发并互审",
   audP:"两个 agent 并行开发、互相独立审查,全程留有 WORKLOG 与 git 审计轨迹。",
   footer:"MetaGLens · 确定性宏基因组流程 · 演示使用桩工具,不产生科学结果"}
};
var LANG="en";
function t(k){return (I18N[LANG]&&I18N[LANG][k])||k;}
function $(s){return document.querySelector(s);}
if(BOOT.logo){$("#logo").src=BOOT.logo;}else{$("#logo").replaceWith(Object.assign(document.createElement("span"),{textContent:"MetaGLens",style:"font-size:30px;font-weight:700;color:var(--brand)"}));}

// route select
(function(){var sel=$("#f-route");BOOT.routes.forEach(function(r){var o=document.createElement("option");o.value=r;o.textContent=r;sel.appendChild(o);});
  sel.onchange=renderYaml;})();
function renderYaml(){$("#cfg-yaml").textContent="route_name: "+$("#f-route").value+"  ·  conda_mode: reuse  ·  exec_env: local";}

// nav + i18n
function renderNav(){var nav=$("#nav");nav.innerHTML="";var ids=["why","configure","run","report","script","audit"];
  t("nav").forEach(function(label,i){var b=document.createElement("button");b.textContent=label;b.onclick=function(){document.getElementById(ids[i]).scrollIntoView({behavior:"smooth"});};nav.appendChild(b);});}
function applyI18n(){document.querySelectorAll("[data-i18n]").forEach(function(n){var k=n.getAttribute("data-i18n");var v=t(k);if(typeof v==="string")n.textContent=v;});renderNav();renderYaml();}
function setLang(l){LANG=(l==="zh")?"zh":"en";applyI18n();}

// audit stats (real numbers only)
(function(){var stats=[["tests","248"],["stages","12"],["stub demo","~9s"],["agents","2"]];
  var box=$("#audit-stats");stats.forEach(function(s){var d=document.createElement("div");d.className="stat";
    d.innerHTML='<div class="k">'+s[0]+'</div><div class="v">'+s[1]+'</div>';box.appendChild(d);});})();

// run flow
var POLL=null;
function setStages(list){var box=$("#stages");box.innerHTML="";(list||[]).forEach(function(s){
  var row=document.createElement("div");row.className="stagerow";
  var cls=s.status==="completed"?"done":(s.status==="running"?"running":(s.status==="failed"?"failed":""));
  row.innerHTML='<span class="dot '+cls+'"></span>'+s.step+' <span class="hint">'+(s.status||"")+'</span>';
  box.appendChild(row);});}
function startRun(){
  if(BOOT.static){ // backend-free export: show the pre-baked artefacts
    $("#run-msg").textContent="(static export — showing a pre-recorded run)";
    $("#report-frame").src="report.html";
    fetch("script.txt").then(function(r){return r.text();}).then(function(x){$("#script-box").textContent=x;}).catch(function(){});
    return;
  }
  var btn=$("#runbtn");btn.disabled=true;$("#run-msg").textContent="starting...";
  fetch("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({route:$("#f-route").value})}).then(function(r){return r.json();}).then(function(j){
    if(!j.ok){$("#run-msg").textContent=j.error||"busy";btn.disabled=false;return;}
    poll(j.id);
  }).catch(function(e){$("#run-msg").textContent="error: "+e;btn.disabled=false;});
}
function poll(id){
  if(POLL)clearInterval(POLL);
  POLL=setInterval(function(){
    fetch("/api/status?id="+encodeURIComponent(id)).then(function(r){return r.json();}).then(function(j){
      $("#run-msg").textContent=j.status+(j.elapsed?(" · "+j.elapsed+"s"):"");
      setStages(j.stages);
      if(j.status==="done"||j.status==="failed"||j.status==="timeout"){
        clearInterval(POLL);$("#runbtn").disabled=false;
        if(j.has_report){$("#report-frame").src="/api/report?id="+encodeURIComponent(id);}
        fetch("/api/script?id="+encodeURIComponent(id)).then(function(r){return r.text();}).then(function(x){if(x)$("#script-box").textContent=x;}).catch(function(){});
      }
    }).catch(function(){clearInterval(POLL);$("#runbtn").disabled=false;});
  },700);
}
applyI18n();
</script>
</body></html>
"""
