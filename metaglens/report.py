"""Self-contained interactive HTML report generator.

Produces a single report.html populated from the pipeline results — same visual
language as the skill-generated report (Times New Roman, light poster theme,
blue-navy palette) but with additional tabs and features:

  * Pipeline timeline (stage durations, attempts)
  * QC statistics (fastp JSON parsed)
  * Community bar chart + data table with CSV export
  * MAG abundance heatmap
  * MAG quality table (sortable)
  * Delivery file index (searchable)
  * Logo placeholder (falls back to text when no image provided)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_tsv(path: Path) -> List[List[str]]:
    if not path.is_file():
        return []
    rows: List[List[str]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            rows.append(line.rstrip("\n").split("\t"))
    return rows


def _to_floats(cells: List[str]) -> List[float]:
    out: List[float] = []
    for v in cells:
        try:
            out.append(float(v))
        except ValueError:
            out.append(0.0)
    return out


def _parse_fastp_reports(qc_dir: Path) -> List[Dict[str, Any]]:
    """Extract per-sample summary from fastp JSON reports in 01_qc/."""
    reports: List[Dict[str, Any]] = []
    if not qc_dir.is_dir():
        return reports
    # 01_quality_control.sh writes "<sample>_fastp.json" into 01_qc/.
    for fp in sorted(qc_dir.glob("*_fastp.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            summary = data.get("summary", {})
            before = summary.get("before_filtering", {})
            after = summary.get("after_filtering", {})
            reports.append({
                "sample": fp.stem[: -len("_fastp")],
                "raw_reads": before.get("total_reads", 0),
                "raw_bases": before.get("total_bases", 0),
                "clean_reads": after.get("total_reads", 0),
                "clean_bases": after.get("total_bases", 0),
                "q30_rate": after.get("q30_rate", 0),
                "gc": after.get("gc_content", 0),
                "adapter_rate": data.get("adapter_cutting", {}).get("adapter_trimmed_reads", 0)
                    / max(before.get("total_reads", 1), 1) * 100,
            })
        except Exception:
            continue
    return reports


def _collect_pipeline_timeline(status: Dict) -> List[Dict[str, Any]]:
    timeline: List[Dict[str, Any]] = []
    steps = status.get("steps", {})
    for step_id in status.get("selected_steps", []):
        info = steps.get(step_id, {})
        timeline.append({
            "step": step_id,
            "status": info.get("status", "pending"),
            "started": info.get("started", ""),
            "finished": info.get("finished", ""),
            "attempts": info.get("attempts", 0),
        })
    return timeline


def generate_report(results_dir: Path, logo_b64: str = "",
                    raw_data_dir: str = "") -> Path:
    """Generate delivery/report.html from pipeline results. Returns path."""
    delivery = results_dir / "delivery"
    delivery.mkdir(parents=True, exist_ok=True)
    status_path = results_dir / "pipeline_status.json"
    status: Dict = {}
    if status_path.is_file():
        status = json.loads(status_path.read_text(encoding="utf-8"))

    project = status.get("project_name", "")
    route = status.get("route_name", "")
    basis = status.get("analysis_basis", "")
    # Prefer an explicitly passed value; fall back to the status file if a
    # future 00_setup records it. Kept optional so the report is self-contained.
    rawdata = raw_data_dir or status.get("raw_data_dir", "")
    samples = status.get("samples", [])
    parallel = status.get("parallel", {})
    parallel_str = "%s jobs x %s threads (%s)" % (
        parallel.get("parallel_jobs", "?"),
        parallel.get("threads_per_job", "?"),
        parallel.get("exec_env", "local"),
    )

    # --- Community data ---
    taxa: List[List] = []
    comm_samples: List[str] = []
    cm = delivery / "community" / "community_matrix.tsv"
    rows = _read_tsv(cm)
    if rows:
        comm_samples = rows[0][1:]
        for r in rows[1:]:
            if len(r) >= 2:
                taxa.append([r[0], _to_floats(r[1:])])

    # --- MAG abundance ---
    mag_ab: Dict[str, Dict[str, float]] = {}
    mag_samples: List[str] = []
    ma = delivery / "tables" / "mag_relative_abundance.tsv"
    rows = _read_tsv(ma)
    if rows:
        mag_samples = rows[0][1:]
        for r in rows[1:]:
            if len(r) >= 2:
                mag_ab[r[0]] = dict(zip(mag_samples, _to_floats(r[1:])))

    run_samples = comm_samples or mag_samples or samples

    # --- CheckM quality ---
    checkm: Dict[str, tuple] = {}
    q = delivery / "tables" / "quality_report_filtered.tsv"
    for r in _read_tsv(q)[1:]:
        if len(r) >= 3:
            try:
                checkm[r[0]] = (float(r[1]), float(r[2]))
            except ValueError:
                pass

    mags: List[List] = []
    for name in sorted(set(checkm) | set(mag_ab)):
        comp, cont = checkm.get(name, (None, None))
        vals = [mag_ab.get(name, {}).get(s, 0.0) for s in run_samples]
        mags.append([name, comp, cont, vals])
    mags.sort(key=lambda m: (-sum(m[3]), m[0]))

    # --- Community source ---
    source_file = delivery / "community" / "SOURCE.txt"
    community_source = ""
    if source_file.is_file():
        community_source = source_file.read_text(encoding="utf-8").strip()

    # --- File dictionary ---
    dictrows: List[List] = []
    for dp, _dirs, fs in os.walk(delivery):
        for f in sorted(fs):
            if f in ("DATA_DICTIONARY.md", "report.html"):
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, delivery)
            dictrows.append([rel, _describe(rel), os.path.getsize(full)])
    dictrows.sort()

    # --- QC stats ---
    qc_stats = _parse_fastp_reports(results_dir / "01_qc")

    # --- Pipeline timeline ---
    timeline = _collect_pipeline_timeline(status)

    # --- Logo ---
    if not logo_b64:
        logo_path = results_dir / "report_logo.b64"
        if logo_path.is_file():
            logo_b64 = logo_path.read_text(encoding="utf-8").strip()
    if not logo_b64:
        try:
            from ._resources import read_resource
            logo_b64 = read_resource(
                "metaglens.templates", "report_logo.b64").strip()
        except Exception:
            logo_b64 = ""

    # --- Build data payload ---
    import datetime
    data = {
        "run": {
            "project": project, "route": route, "basis": basis,
            "rawdata": rawdata, "samples": run_samples,
            "communitySource": community_source,
            "parallel": parallel_str,
            "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "logo": f"data:image/png;base64,{logo_b64}" if logo_b64 else "",
        },
        "taxa": taxa,
        "mags": mags,
        "dict": dictrows,
        "qc": qc_stats,
        "timeline": timeline,
        "gates": status.get("gates", {}),
    }

    html = _build_html(data, project)
    out_path = delivery / "report.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _describe(rel: str) -> str:
    n = os.path.basename(rel)
    if rel.startswith("genomes/"):
        return "Dereplicated representative genome (MAG), FASTA."
    if n == "quality_report_filtered.tsv":
        return "CheckM2 report for retained MAGs (completeness/contamination)."
    if n == "quality_report.tsv":
        return "CheckM2 quality report for all bins."
    if n == "mag_relative_abundance.tsv":
        return "MAG x sample relative abundance (%)."
    if n == "mag_abundance_mean_depth.tsv":
        return "MAG x sample mean coverage depth."
    if n.startswith("gtdbtk.") and n.endswith(".summary.tsv"):
        return "GTDB-Tk taxonomy summary."
    if n == "eggnog_results.emapper.annotations":
        return "eggNOG-mapper functional annotations."
    if n.endswith("_proteins.faa"):
        return "Prodigal predicted proteins."
    if n.endswith("_genes.gff"):
        return "Prodigal gene coordinates (GFF)."
    if n == "contig_coverage.tsv":
        return "Contig x sample coverage matrix."
    if n.endswith("_contig_report.txt"):
        return "Kraken2 contig taxonomy report."
    if n == "community_matrix.tsv":
        return "Full community table (taxon x sample)."
    if n.startswith("community_top") and n.endswith(".tsv"):
        return "Top-N taxa subset of the community table."
    if n == "SOURCE.txt":
        return "Abundance source used for the community tables."
    if n == "tool_versions.txt":
        return "Software versions used in this run."
    return "Delivered analysis file."


# --------------------------------------------------------------------------- #
# HTML assembly
# --------------------------------------------------------------------------- #
def _build_html(data: Dict, project: str) -> str:
    import html as _h
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8"/>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0"/>\n'
        f'<title>{_h.escape(project)} - MetaGLens Report</title>\n'
        f'<style>{_CSS}</style>\n</head>\n<body>\n'
        f'{_LENS}\n{_BODY}\n'
        f'<script>window.__MG__={json.dumps(data, ensure_ascii=False)};</script>\n'
        f'<script>{_JS}</script>\n</body>\n</html>\n'
    )


# --------------------------------------------------------------------------- #
# Embedded CSS / HTML body / JS — matching the family visual identity
# --------------------------------------------------------------------------- #
# The palette + poster theme + lens SVG live in the shared visual module so the
# report, the web config page, and the monitor page can never drift apart.
from ._theme import REPORT_CSS as _CSS, LENS_SVG as _LENS

_BODY = r"""
<header>
  <img class="logo" id="logo-img" alt="MetaGLens" />
  <div class="headline"><div class="t">MetaGLens Delivery Report</div><div class="d">Analysis-ready package</div></div>
</header>
<div class="meta">
  <div class="chip">Project: <b id="m-project"></b></div>
  <div class="chip">Route: <b id="m-route"></b></div>
  <div class="chip">Basis: <b id="m-basis"></b></div>
  <div class="chip" id="chip-rawdata" style="display:none">Raw data: <b id="m-rawdata"></b></div>
  <div class="chip">Samples: <b id="m-nsamp"></b></div>
  <div class="chip">Generated: <b id="m-gen"></b></div>
</div>
<nav id="nav"></nav>
<main>
  <section id="tab-overview"><h2>Overview</h2><p class="hint">Summary of the delivered analysis package.</p><div class="source-note">Community abundance source: <b id="comm-source2"></b></div><div class="flex" id="stats"></div></section>
  <section id="tab-pipeline"><h2>Pipeline</h2><p class="hint">Stage execution timeline and status.</p><div class="card" id="tl-card"></div></section>
  <section id="tab-gates"><h2>Quality Gates</h2><p class="hint">Scientific metric checks (thresholds configured in decide/rules/gates.yaml).</p><div class="card" id="gates-card"></div></section>
  <section id="tab-qc"><h2>QC Statistics</h2><p class="hint">Per-sample read quality control summary (fastp).</p><div class="card"><div class="heat-wrap"><table id="qc-table"></table></div></div><div style="margin-top:8px"><button class="btn" onclick="exportCSV('qc-table','qc_stats.csv')">Export CSV</button></div></section>
  <section id="tab-community"><h2>Community</h2><p class="hint">Cross-sample taxonomic relative abundance.</p><div class="source-note">Source: <b id="comm-source"></b></div><div class="controls"><label class="ctl">Top:<select id="topn"><option value="10">10</option><option value="15" selected>15</option><option value="999">All</option></select></label><label class="ctl">View:<select id="commview"><option value="stack">Chart</option><option value="table">Table</option></select></label><button class="btn" onclick="exportCSV('comm-table','community.csv')">Export CSV</button></div><div class="card" id="comm-chart-card"><div class="heat-wrap"><div id="comm-chart"></div></div><div class="legend" id="comm-legend"></div></div><div class="card" id="comm-table-card" style="display:none"><div class="heat-wrap"><table id="comm-table"></table></div></div></section>
  <section id="tab-mag"><h2>MAG Abundance</h2><p class="hint">Representative genome x sample relative abundance (%).</p><div class="controls"><label class="ctl">Sort:<select id="magsort"><option value="abund">Abundance</option><option value="name">Name</option></select></label></div><div class="card heat-wrap"><div id="mag-heat"></div></div></section>
  <section id="tab-quality"><h2>MAG Quality</h2><p class="hint">CheckM2 completeness / contamination. Click header to sort.</p><div class="card"><div class="heat-wrap"><table id="qual-table"></table></div></div><div style="margin-top:8px"><button class="btn" onclick="exportCSV('qual-table','mag_quality.csv')">Export CSV</button></div></section>
  <section id="tab-files"><h2>Files</h2><p class="hint">All delivered files.</p><div class="controls"><input type="search" id="filesearch" placeholder="Search..." style="min-width:280px" /></div><div class="card"><div class="heat-wrap"><table id="files-table"></table></div></div></section>
</main>
<footer>MetaGLens · Self-contained delivery report · Values computed from delivered tables.</footer>
"""

_JS = r"""
var MG=window.__MG__,RUN=MG.run,TAXA=MG.taxa,MAGS=MG.mags,FILES=MG.dict,QC=MG.qc||[],TL=MG.timeline||[];
var PALETTE=["#38A8F0","#3b7de0","#5f97dd","#7fb0e8","#9cc4ec","#2f6fd0","#6aa2e3","#4cc9f0","#8fbce8","#1e2a66","#4a89dc","#a7cbf0","#bcd8f2","#5878c8","#79a6e6","#2a9d8f"];
var $=function(s){return document.querySelector(s);};
function el(t,a,h){var e=document.createElement(t);a=a||{};for(var k in a)e.setAttribute(k,a[k]);if(h!=null)e.innerHTML=h;return e;}

// Logo
if(RUN.logo){$("#logo-img").src=RUN.logo;}else{var w=$("#logo-img");var s=el("span",{},"MetaGLens");s.style.cssText="font-size:30px;font-weight:700;color:var(--brand)";w.parentNode.replaceChild(s,w);}

// Meta
$("#m-project").textContent=RUN.project;$("#m-route").textContent=RUN.route;$("#m-basis").textContent=RUN.basis;
if(RUN.rawdata){$("#m-rawdata").textContent=RUN.rawdata;$("#chip-rawdata").style.display="";}
$("#m-nsamp").textContent=RUN.samples.length;$("#m-gen").textContent=RUN.generated;
$("#comm-source").textContent=RUN.communitySource||"(none)";$("#comm-source2").textContent=RUN.communitySource||"(none)";

// Tabs
var TABS=[["overview","Overview"],["pipeline","Pipeline"],["gates","Gates"],["qc","QC"],["community","Community"],["mag","MAG Abundance"],["quality","MAG Quality"],["files","Files"]];
var nav=$("#nav");TABS.forEach(function(t,i){var b=el("button",{},t[1]);if(i===0)b.classList.add("active");b.onclick=function(){document.querySelectorAll("nav button").forEach(function(x){x.classList.remove("active");});document.querySelectorAll("section").forEach(function(x){x.classList.remove("active");});b.classList.add("active");$("#tab-"+t[0]).classList.add("active");};nav.appendChild(b);});
$("#tab-overview").classList.add("active");

// Stats cards
var stats=[["Samples",RUN.samples.length],["MAGs",MAGS.length],["Taxa",TAXA.length],["Execution",RUN.parallel||"-"]];
stats.forEach(function(s){var d=el("div",{"class":"stat"});d.appendChild(el("div",{"class":"k"},s[0]));d.appendChild(el("div",{"class":"v"},""+s[1]));$("#stats").appendChild(d);});

// Pipeline timeline
(function(){var card=$("#tl-card");if(!TL.length){card.innerHTML='<div class="empty">No pipeline data.</div>';return;}
var colors={"completed":"var(--good)","running":"var(--warn)","failed":"var(--bad)","pending":"var(--line)"};
TL.forEach(function(t){var row=el("div",{"class":"tl-row"});row.innerHTML='<div class="tl-step">'+t.step+'</div><div class="tl-bar"><div class="tl-fill" style="width:'+(t.status==="completed"?"100%":t.status==="running"?"50%":"0%")+';background:'+colors[t.status]+'"></div></div><div class="tl-meta">'+(t.started||"—")+' → '+(t.finished||"—")+' (×'+t.attempts+')</div>';card.appendChild(row);});})();

// Quality gates
(function(){var card=$("#gates-card");var G=(MG.gates&&MG.gates.gates)||[];
if(!G.length){card.innerHTML='<div class="empty">No gate results recorded. Run <b>metaglens gate</b>.</div>';return;}
var colors={"pass":"var(--good)","warn":"var(--warn)","block":"var(--bad)","unknown":"var(--muted)"};
var labels={"pass":"pass","warn":"warning","block":"blocking","unknown":"n/a"};
var c=(MG.gates&&MG.gates.counts)||{};
var h='<div class="hint">'+(c.pass||0)+" passed \u00b7 "+(c.warn||0)+" warning(s) \u00b7 "+(c.block||0)+" blocking \u00b7 "+(c.unknown||0)+' n/a</div>';
h+='<table><thead><tr><th>Gate</th><th>Stage</th><th>Value</th><th>Status</th></tr></thead><tbody>';
G.forEach(function(g){h+="<tr><td class='mono'>"+g.id+"</td><td>"+g.stage+"</td><td>"+g.detail+
  "</td><td style='color:"+(colors[g.status]||"var(--muted)")+";font-weight:700'>"+(labels[g.status]||g.status)+"</td></tr>";});
h+="</tbody></table>";
G.forEach(function(g){if((g.status==="warn"||g.status==="block")&&g.hint){
  h+='<div class="source-note" style="margin-top:12px"><b>'+g.id+'</b> — '+g.hint+'</div>';}});
card.innerHTML=h;})();

// QC table
(function(){var t=$("#qc-table");if(!QC.length){t.innerHTML="<tbody><tr><td class='empty'>No QC data available (fastp reports not found).</td></tr></tbody>";return;}
var h="<thead><tr><th>Sample</th><th class='num'>Raw reads</th><th class='num'>Clean reads</th><th class='num'>Clean bases</th><th class='num'>Q30 %</th><th class='num'>GC %</th><th class='num'>Adapter %</th></tr></thead><tbody>";
QC.forEach(function(q){h+="<tr><td>"+q.sample+"</td><td class='num'>"+q.raw_reads.toLocaleString()+"</td><td class='num'>"+q.clean_reads.toLocaleString()+"</td><td class='num'>"+q.clean_bases.toLocaleString()+"</td><td class='num'>"+(q.q30_rate*100).toFixed(1)+"</td><td class='num'>"+(q.gc*100).toFixed(1)+"</td><td class='num'>"+q.adapter_rate.toFixed(1)+"</td></tr>";});
h+="</tbody>";t.innerHTML=h;})();

// Community chart
function topTaxa(n){var rows=TAXA.map(function(t){return{name:t[0],vals:t[1],sum:t[1].reduce(function(a,b){return a+b;},0)};});rows.sort(function(a,b){return b.sum-a.sum;});if(n>=rows.length)return{rows:rows,other:null};var top=rows.slice(0,n),rest=rows.slice(n);var other=RUN.samples.map(function(_,i){return rest.reduce(function(a,r){return a+r.vals[i];},0);});return{rows:top,other:other};}
function renderCommunityChart(){if(!TAXA.length){$("#comm-chart").innerHTML='<div class="empty">No community table.</div>';return;}var n=parseInt($("#topn").value,10);var r=topTaxa(n);var series=r.other?r.rows.concat([{name:"Other",vals:r.other}]):r.rows.slice();var W=Math.max(760,RUN.samples.length*70+60),H=380,padL=46,padB=42,padT=10,padR=10;var gap=(W-padL-padR)/RUN.samples.length,bw=gap*0.58;var colTot=RUN.samples.map(function(_,i){return series.reduce(function(a,s){return a+s.vals[i];},0);});var svg='<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'">';for(var g=0;g<=100;g+=25){var y=padT+(H-padT-padB)*(1-g/100);svg+='<line x1="'+padL+'" y1="'+y+'" x2="'+(W-padR)+'" y2="'+y+'" stroke="#e4eaf3"/><text x="6" y="'+(y+4)+'">'+g+'%</text>';}RUN.samples.forEach(function(s,i){var x=padL+gap*i+(gap-bw)/2,acc=0;series.forEach(function(ser,si){var val=colTot[i]?ser.vals[i]/colTot[i]*100:0,h2=(H-padT-padB)*val/100,yy=padT+(H-padT-padB)-acc-h2;var c=ser.name==="Other"?"#c3ccda":PALETTE[si%PALETTE.length];svg+='<rect x="'+x+'" y="'+yy+'" width="'+bw+'" height="'+h2+'" rx="3" fill="'+c+'"><title>'+ser.name+': '+val.toFixed(1)+'%</title></rect>';acc+=h2;});svg+='<text x="'+(x+bw/2)+'" y="'+(H-padB+18)+'" text-anchor="middle" style="fill:#33406a">'+s+'</text>';});svg+='</svg>';$("#comm-chart").innerHTML=svg;var leg=$("#comm-legend");leg.innerHTML="";series.forEach(function(ser,si){var c=ser.name==="Other"?"#c3ccda":PALETTE[si%PALETTE.length];var sp=el("span");sp.innerHTML='<span class="sw" style="background:'+c+'"></span>'+ser.name;leg.appendChild(sp);});}
function renderCommunityTable(){if(!TAXA.length){$("#comm-table").innerHTML="";return;}var n=parseInt($("#topn").value,10);var r=topTaxa(n);var h="<thead><tr><th>Taxon</th>"+RUN.samples.map(function(s){return'<th class="num">'+s+'</th>';}).join("")+"</tr></thead><tbody>";var mx=Math.max.apply(null,r.rows.map(function(x){return Math.max.apply(null,x.vals);}));r.rows.forEach(function(row){h+="<tr><td>"+row.name+"</td>"+row.vals.map(function(v){var w=(mx?v/mx*100:0).toFixed(0);return'<td class="num bar-cell"><span class="barfill" style="width:'+w+'%"></span><span class="barval">'+v.toFixed(1)+'</span></td>';}).join("")+"</tr>";});h+="</tbody>";$("#comm-table").innerHTML=h;}
function renderCommunity(){var v=$("#commview").value;$("#comm-chart-card").style.display=v==="stack"?"block":"none";$("#comm-table-card").style.display=v==="table"?"block":"none";if(v==="stack")renderCommunityChart();else renderCommunityTable();}
$("#topn").onchange=renderCommunity;$("#commview").onchange=renderCommunity;renderCommunity();

// MAG heatmap
function hcolor(v,max){var t=max?v/max:0;var c1=[233,240,249],c2=[56,168,240];var r2=c1.map(function(a,i){return Math.round(a+(c2[i]-a)*t);});return"rgb("+r2[0]+","+r2[1]+","+r2[2]+")";}
function renderMagHeat(){if(!MAGS.length){$("#mag-heat").innerHTML='<div class="empty">No MAGs delivered.</div>';return;}var mm=MAGS.map(function(m){return{name:m[0],comp:m[1],cont:m[2],vals:m[3],sum:m[3].reduce(function(a,b){return a+b;},0)};});if($("#magsort").value==="abund")mm.sort(function(a,b){return b.sum-a.sum;});else mm.sort(function(a,b){return a.name.localeCompare(b.name);});var mx=Math.max.apply(null,mm.map(function(m){return Math.max.apply(null,m.vals);}));if(!isFinite(mx)||mx<=0)mx=1;var cell=38,labelW=230,top2=32,sw2=cell;var W=labelW+RUN.samples.length*sw2+10,H=top2+mm.length*cell+10;var svg='<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'">';RUN.samples.forEach(function(s,j){svg+='<text x="'+(labelW+j*sw2+sw2/2)+'" y="'+(top2-10)+'" text-anchor="middle" style="fill:#33406a">'+s+'</text>';});mm.forEach(function(m,i){var y=top2+i*cell;svg+='<text x="4" y="'+(y+cell/2+5)+'" style="fill:#33406a">'+m.name+'</text>';m.vals.forEach(function(v,j){var x=labelW+j*sw2;svg+='<rect x="'+x+'" y="'+y+'" width="'+(sw2-3)+'" height="'+(cell-3)+'" rx="5" fill="'+hcolor(v,mx)+'" stroke="#e4eaf3"><title>'+m.name+' @ '+RUN.samples[j]+': '+v.toFixed(2)+'%</title></rect>';svg+='<text x="'+(x+(sw2-3)/2)+'" y="'+(y+cell/2+5)+'" text-anchor="middle" style="fill:'+(v/mx>0.5?"#fff":"#8695a8")+'">'+(v?v.toFixed(0):"")+'</text>';});});svg+='</svg>';$("#mag-heat").innerHTML=svg;}
$("#magsort").onchange=renderMagHeat;renderMagHeat();

// Quality table
var qs={col:4,dir:-1};
function renderQual(){if(!MAGS.length){$("#qual-table").innerHTML="<tbody><tr><td class='empty'>No MAGs.</td></tr></tbody>";return;}var cols=[["MAG",0,"t"],["Completeness %",1,"n"],["Contamination %",2,"n"],["Total abund. %",4,"n"]];var rows=MAGS.map(function(m){return[m[0],m[1],m[2],null,m[3].reduce(function(a,b){return a+b;},0)];});rows.sort(function(a,b){var c=qs.col,av=a[c],bv=b[c];if(av==null)av=-1;if(bv==null)bv=-1;return(typeof av==="number"?av-bv:(""+av).localeCompare(""+bv))*qs.dir;});var h="<thead><tr>"+cols.map(function(c){return'<th class="'+(c[2]==="n"?"num":"")+'" data-col="'+c[1]+'">'+c[0]+"</th>";}).join("")+"</tr></thead><tbody>";rows.forEach(function(r){var comp=r[1],cont=r[2];var cc=comp==null?"var(--muted)":(comp>=90?"var(--good)":comp>=70?"var(--warn)":"var(--bad)");var tc=cont==null?"var(--muted)":(cont<=5?"var(--good)":cont<=10?"var(--warn)":"var(--bad)");h+="<tr><td class='mono'>"+r[0]+"</td><td class='num' style='color:"+cc+";font-weight:700'>"+(comp==null?"-":comp.toFixed(1))+"</td><td class='num' style='color:"+tc+";font-weight:700'>"+(cont==null?"-":cont.toFixed(1))+"</td><td class='num'>"+r[4].toFixed(2)+"</td></tr>";});h+="</tbody>";var t=$("#qual-table");t.innerHTML=h;t.querySelectorAll("th").forEach(function(th){th.onclick=function(){var c=parseInt(th.dataset.col,10);qs.dir=qs.col===c?-qs.dir:(c>=1?-1:1);qs.col=c;renderQual();};});}
renderQual();

// Files table
function fmtSize(b){if(b<1024)return b+" B";if(b<1048576)return(b/1024).toFixed(1)+" KB";return(b/1048576).toFixed(1)+" MB";}
function renderFiles(flt){flt=(flt||"").toLowerCase();var rows=FILES.filter(function(d){return d[0].toLowerCase().indexOf(flt)>=0||d[1].toLowerCase().indexOf(flt)>=0;});var h="<thead><tr><th>File</th><th>Description</th><th class='num'>Size</th></tr></thead><tbody>";if(!rows.length)h+="<tr><td colspan='3' class='empty'>No files.</td></tr>";rows.forEach(function(d){var dir=d[0].indexOf("/")>=0?d[0].split("/")[0]:"";h+="<tr><td class='mono'>"+(dir?'<span class="tag dir">'+dir+'</span> ':"")+d[0]+"</td><td>"+d[1]+"</td><td class='num'>"+fmtSize(d[2])+"</td></tr>";});h+="</tbody>";$("#files-table").innerHTML=h;}
$("#filesearch").oninput=function(e){renderFiles(e.target.value);};renderFiles();

// CSV export
function exportCSV(tableId,filename){var t=document.getElementById(tableId);if(!t)return;var csv=[];t.querySelectorAll("tr").forEach(function(row){var cols=[];row.querySelectorAll("th,td").forEach(function(c){cols.push('"'+c.textContent.replace(/"/g,'""')+'"');});csv.push(cols.join(","));});var blob=new Blob([csv.join("\n")],{type:"text/csv"});var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=filename;a.click();}
"""
