"""Local web configuration (approach B): ``metaglens configure``.

A tiny standard-library HTTP service (no Flask/FastAPI) that serves a
self-contained configuration page and a handful of read-only JSON endpoints
backed by the sense/decide layers. Submitting the form writes ``metaglens.yaml``
through the *same* :class:`Config` + ``validate()`` the terminal wizard uses, so
both entry points produce identical configs.

Security posture (single-user, shared-server aware): bind ``127.0.0.1`` only,
let the OS pick the port, and gate every request on a one-time token carried in
the URL. Nothing here writes to a database directory or makes network calls.

The request logic is factored into pure functions (``api_*`` / ``save_config``)
so it can be unit-tested without starting a server.
"""

from __future__ import annotations

import dataclasses
import json
import secrets
import threading
import typing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from ..config import Config
from .. import routes
from .._theme import REPORT_CSS, LENS_SVG


# --------------------------------------------------------------------------- #
# Payload coercion + save (shared with any programmatic caller)
# --------------------------------------------------------------------------- #
def _field_types() -> Dict[str, Any]:
    return {f.name: f.type for f in dataclasses.fields(Config)}


def _coerce_value(name: str, value: Any, type_hint: Any) -> Any:
    """Coerce a JSON/form value to the type declared on the Config field."""
    th = str(type_hint)
    if "bool" in th:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if "int" in th:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    if "List" in th or "list" in th:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [s.strip() for s in str(value).split(",") if s.strip()]
    return "" if value is None else str(value)


def coerce_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only known Config fields and coerce them to declared types.

    Non-Config keys (e.g. ``lang``, ``token``) are dropped, which is what makes
    the produced YAML independent of the UI language.
    """
    types = _field_types()
    out: Dict[str, Any] = {}
    for name, value in payload.items():
        if name in types:
            out[name] = _coerce_value(name, value, types[name])
    return out


def save_config(payload: Dict[str, Any], out_path: str) -> Tuple[bool, List[str], str]:
    """Build a Config from ``payload``, validate, and write YAML if valid.

    An optional ``samples`` key (the possibly renamed / filtered rows from the
    web table) is written as a validated ``samples.tsv`` beside the config and
    referenced via ``sample_manifest``. Returns ``(ok, errors, out_path)``; on
    failure nothing is written.
    """
    fields = coerce_payload(payload)
    rows = payload.get("samples")

    manifest_path = ""
    manifest_rows = []
    if rows:
        from .. import samples as samples_mod
        try:
            manifest_rows = [
                samples_mod.Sample(str(r["sample_id"]).strip(),
                                   str(r["r1"]), str(r["r2"]))
                for r in rows
            ]
        except (KeyError, TypeError) as exc:
            return False, [f"invalid samples table: {exc}"], out_path
        if not manifest_rows:
            return False, ["the samples table is empty"], out_path
        try:
            samples_mod._validate(manifest_rows)
        except samples_mod.SampleDiscoveryError as exc:
            return False, [str(exc)], out_path
        manifest_path = str(Path(out_path).expanduser().resolve().parent / "samples.tsv")
        fields["sample_manifest"] = manifest_path

    cfg = Config(**fields)
    errors = cfg.validate()
    if errors:
        return False, errors, out_path

    if manifest_rows:
        from .. import samples as samples_mod
        samples_mod.write_manifest(manifest_rows, manifest_path)
    cfg.to_yaml(out_path)
    return True, [], out_path


# --------------------------------------------------------------------------- #
# Read-only API backends (pure; no server needed to test)
# --------------------------------------------------------------------------- #
def api_hardware() -> Dict[str, Any]:
    from ..sense import hardware
    info = hardware.probe(".")
    return {
        "cores": info.cores,
        "ram_gb": round(info.ram_gb, 1),
        "disk_free_gb": round(info.disk_free_gb, 1),
        "in_container": info.in_container,
        "summary": info.summary(),
    }


def api_plan(cores: int, ram_gb: float, n_samples: int) -> Dict[str, Any]:
    from ..decide import planner
    plan = planner.recommend_parallel(cores, ram_gb, n_samples)
    return {
        "parallel_jobs": plan.jobs,
        "threads_per_job": plan.threads_per_job,
        "memory_capped": plan.memory_capped,
        "reason": plan.reason,
    }


def api_samples(directory: str) -> Dict[str, Any]:
    from .. import samples as samples_mod
    try:
        found, pattern, layout, id_source = samples_mod.discover(directory)
    except Exception as exc:  # discovery raises a typed error; surface as message
        return {"ok": False, "error": str(exc), "samples": [],
                "pattern": "", "layout": "", "id_source": ""}
    return {
        "ok": True,
        "pattern": pattern,
        "layout": layout,
        "id_source": id_source,
        "samples": [
            {"sample_id": s.sample_id,
             "dir": Path(s.r1).parent.name,
             "r1": s.r1,
             "r2": s.r2,
             "r1_name": Path(s.r1).name,
             "r2_name": Path(s.r2).name}
            for s in found
        ],
    }


def _cfg_from_query(params: Dict[str, str]) -> Config:
    """Minimal Config for DB discovery/requirement queries."""
    known = {f.name for f in dataclasses.fields(Config)}
    kw = {k: v for k, v in params.items() if k in known}
    return Config(**coerce_payload(kw))


def api_db(name: str, path: str = "", params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    from ..sense import database
    if name not in database.REGISTRY:
        return {"ok": False, "error": f"unknown database '{name}'"}
    if path:
        ok, detail = database.validate(name, path)
        return {"ok": ok, "state": "ready" if ok else "wrong_path",
                "path": str(Path(path).expanduser()), "detail": detail}
    cfg = _cfg_from_query(params or {})
    st = database.discover(name, cfg)
    return {
        "ok": st.state == "ready",
        "state": st.state,
        "path": st.path,
        "version": st.version,
        "source": st.source,
        "detail": st.detail,
    }


def api_required_dbs(params: Dict[str, str]) -> Dict[str, Any]:
    from ..sense import database
    cfg = _cfg_from_query(params)
    need = database.required_databases(cfg)
    out = {}
    for db_name, reason in need.items():
        st = database.discover(db_name, cfg)
        out[db_name] = {"reason": reason, "state": st.state,
                        "path": st.path, "version": st.version,
                        "detail": st.detail}
    return {"required": out}


# --------------------------------------------------------------------------- #
# Page rendering
# --------------------------------------------------------------------------- #
def _load_logo_b64() -> str:
    """Single logo asset shared with the report. Replace the asset to rebrand."""
    try:
        return resources.files("metaglens.templates").joinpath(
            "report_logo.b64").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def build_page(token: str, lang: str = "zh", logo_b64: str = "") -> str:
    """Return the self-contained configuration HTML (shared skin, bilingual)."""
    if not logo_b64:
        logo_b64 = _load_logo_b64()
    logo_src = f"data:image/png;base64,{logo_b64}" if logo_b64 else ""
    route_names = [r for r in routes.ROUTES] + ["custom"]
    boot = {
        "token": token,
        "lang": lang if lang in ("zh", "en") else "zh",
        "routes": route_names,
        "logo": logo_src,
    }
    return _PAGE_TEMPLATE.replace("/*__CSS__*/", REPORT_CSS) \
        .replace("<!--__LENS__-->", LENS_SVG) \
        .replace("/*__BOOT__*/", json.dumps(boot, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# HTTP adapter
# --------------------------------------------------------------------------- #
class _ConfigServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, *, token: str, out_path: str, lang: str):
        super().__init__(addr, handler)
        self.token = token
        self.out_path = out_path
        self.lang = lang
        self.saved_ok = False


class _Handler(BaseHTTPRequestHandler):
    server_version = "MetaGLensConfig/1.0"

    # Silence default request logging to keep the terminal clean.
    def log_message(self, *args) -> None:  # noqa: D401
        return

    # -- helpers ---------------------------------------------------------- #
    def _token_ok(self, query: Dict[str, List[str]]) -> bool:
        supplied = (query.get("token", [""])[0])
        return secrets.compare_digest(supplied, self.server.token)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _flat(self, query: Dict[str, List[str]]) -> Dict[str, str]:
        return {k: v[0] for k, v in query.items() if v}

    # -- routing ---------------------------------------------------------- #
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._token_ok(query):
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        path = parsed.path
        try:
            if path == "/":
                html = build_page(self.server.token, self.server.lang)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/hardware":
                self._json(api_hardware())
            elif path == "/api/plan":
                q = self._flat(query)
                self._json(api_plan(int(q.get("cores", 1)),
                                    float(q.get("ram", 0)),
                                    int(q.get("n", 1))))
            elif path == "/api/samples":
                self._json(api_samples(self._flat(query).get("dir", "")))
            elif path == "/api/db":
                q = self._flat(query)
                self._json(api_db(q.get("name", ""), q.get("path", ""), q))
            elif path == "/api/required-dbs":
                self._json(api_required_dbs(self._flat(query)))
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:  # never leak a traceback to the browser
            self._json({"ok": False, "error": str(exc)}, code=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._token_ok(query):
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        if parsed.path != "/save":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json({"ok": False, "errors": ["invalid JSON payload"]}, code=400)
            return
        ok, errors, out_path = save_config(payload, self.server.out_path)
        if ok:
            self.server.saved_ok = True
        self._json({"ok": ok, "errors": errors, "path": out_path},
                   code=200 if ok else 422)


def serve(config_path: str = "metaglens.yaml", host: str = "127.0.0.1",
          lang: str = "zh", open_browser: bool = True) -> str:
    """Start the config server (blocking). Returns the URL that was served.

    Binds ``host`` (default loopback) on an OS-assigned port and prints a
    token-bearing URL. ``Ctrl-C`` stops it. Safe on headless hosts: pass
    ``open_browser=False`` and port-forward the printed URL.
    """
    token = secrets.token_urlsafe(24)
    server = _ConfigServer((host, 0), _Handler, token=token,
                           out_path=config_path, lang=lang)
    port = server.server_address[1]
    url = f"http://{host}:{port}/?token={token}"
    print(f"MetaGLens config server: {url}")
    print("Bound to loopback only; the token above is required. Ctrl-C to stop.")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while True:
            thread.join(0.5)
    except KeyboardInterrupt:
        print("\nStopping config server.")
    finally:
        server.shutdown()
        server.server_close()
    return url


# --------------------------------------------------------------------------- #
# Page template — self-contained; shares REPORT_CSS + LENS_SVG with the report.
# --------------------------------------------------------------------------- #
_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>MetaGLens — Configure</title>
<style>/*__CSS__*/
.cfg-group{max-width:1180px;margin:0 auto;}
.row{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin:10px 0;}
.row label{min-width:230px;font-weight:700;color:var(--ink-soft);}
.row input[type=text],.row input[type=number],.row select{min-width:280px;}
.help{color:var(--muted);font-size:14px;margin:2px 0 0 230px;}
.langsw{margin-left:auto;}
.reason{background:rgba(59,125,224,.07);border:1px solid rgba(59,125,224,.25);border-radius:10px;padding:10px 14px;margin:8px 0;color:var(--ink-soft);}
.errs{color:var(--bad);font-weight:700;}
.ok-box{background:rgba(42,157,143,.1);border:1px solid var(--good);border-radius:10px;padding:14px;color:var(--ink);}
</style></head><body>
<!--__LENS__-->
<header>
  <img class="logo" id="logo-img" alt="MetaGLens"/>
  <div class="headline"><div class="t" data-i18n="title">MetaGLens 配置</div>
  <div class="d" data-i18n="subtitle">本地网页配置 · 仅限本机</div></div>
  <div class="langsw"><button class="btn" onclick="setLang('zh')">中文</button>
  <button class="btn" onclick="setLang('en')">English</button></div>
</header>
<main><div class="cfg-group" id="form"></div>
<div class="row"><button class="btn" id="saveBtn" onclick="save()" data-i18n="save">保存配置</button></div>
<div id="result"></div></main>
<script>var BOOT=/*__BOOT__*/;</script>
<script>
var I18N={
 zh:{title:"MetaGLens 配置",subtitle:"本地网页配置 · 仅限本机",save:"保存配置",
   project_name:"项目名称",work_dir:"工作目录",raw_data_dir:"原始数据目录",
   route_name:"分析路线",total_threads:"总线程数",taxonomy_tool:"MAG 分类工具",
   contig_taxonomy:"Contig 分类",use_eggnog:"eggNOG 功能注释",
   samples:"发现的样本",plan:"并行建议",dbs:"所需数据库",
   include:"使用",sample_id:"样本 ID",directory:"目录",
   edithint:"可直接修改样本 ID，或取消勾选以排除；保存时会写出 samples.tsv。",
   saved:"配置已保存到",nexthint:"下一步:metaglens run"},
 en:{title:"MetaGLens Configuration",subtitle:"Local web config · loopback only",save:"Save config",
   project_name:"Project name",work_dir:"Work directory",raw_data_dir:"Raw-data directory",
   route_name:"Analysis route",total_threads:"Total threads",taxonomy_tool:"MAG taxonomy tool",
   contig_taxonomy:"Contig taxonomy",use_eggnog:"eggNOG annotation",
   samples:"Discovered samples",plan:"Parallel recommendation",dbs:"Required databases",
   include:"Use",sample_id:"Sample ID",directory:"Directory",
   edithint:"Edit a sample ID directly, or untick to exclude; a samples.tsv is written on save.",
   saved:"Configuration saved to",nexthint:"Next: metaglens run"}
};
var LANG=BOOT.lang||"zh";
function t(k){return (I18N[LANG]&&I18N[LANG][k])||k;}
function api(path){return path+(path.indexOf("?")<0?"?":"&")+"token="+encodeURIComponent(BOOT.token);}
if(BOOT.logo){document.getElementById("logo-img").src=BOOT.logo;}
function el(tag,attrs,html){var e=document.createElement(tag);attrs=attrs||{};for(var k in attrs)e.setAttribute(k,attrs[k]);if(html!=null)e.innerHTML=html;return e;}
function field(key,type,val,opts){
  var row=el("div",{"class":"row"});
  row.appendChild(el("label",{},t(key)));
  var inp;
  if(type==="select"){inp=el("select",{id:key});(opts||[]).forEach(function(o){var op=el("option",{value:o},o);if(o===val)op.setAttribute("selected","1");inp.appendChild(op);});}
  else if(type==="checkbox"){inp=el("input",{type:"checkbox",id:key});if(val)inp.setAttribute("checked","1");}
  else{inp=el("input",{type:type,id:key,value:val==null?"":val});}
  row.appendChild(inp);return row;
}
function render(){
  var f=document.getElementById("form");f.innerHTML="";
  f.appendChild(field("project_name","text",""));
  f.appendChild(field("raw_data_dir","text",""));
  f.appendChild(field("work_dir","text",""));
  f.appendChild(field("route_name","select","mag_per_sample",BOOT.routes));
  f.appendChild(field("total_threads","number",16));
  f.appendChild(field("taxonomy_tool","select","gtdbtk",["gtdbtk","kraken2"]));
  f.appendChild(field("contig_taxonomy","select","none",["none","kraken2"]));
  f.appendChild(field("use_eggnog","checkbox",true));
  document.querySelectorAll('[data-i18n]').forEach(function(n){n.textContent=t(n.getAttribute("data-i18n"));});
  refreshSamples();refreshPlan();refreshDbs();
}
function val(id){var e=document.getElementById(id);if(!e)return null;return e.type==="checkbox"?e.checked:e.value;}
var SAMPLES=[];
function refreshSamples(){var d=val("raw_data_dir");if(!d)return;fetch(api("/api/samples?dir="+encodeURIComponent(d))).then(function(r){return r.json();}).then(function(j){
  var box=document.getElementById("samples-box")||el("div",{id:"samples-box","class":"card"});box.id="samples-box";
  if(!j.ok){SAMPLES=[];box.innerHTML='<div class="empty">'+t("samples")+": "+(j.error||"")+'</div>';
    document.getElementById("form").appendChild(box);return;}
  SAMPLES=j.samples;
  var h='<div class="hint">'+t("samples")+": "+j.samples.length
       +" · "+j.pattern+" · layout: "+j.layout+" · ids from: "+j.id_source+'</div>';
  h+='<div class="heat-wrap"><table><thead><tr><th>'+t("include")+'</th><th>'+t("sample_id")
   +'</th><th>'+t("directory")+'</th><th>R1</th><th>R2</th></tr></thead><tbody>';
  j.samples.forEach(function(s,i){
    h+='<tr><td><input type="checkbox" class="s-inc" data-i="'+i+'" checked/></td>'
     +'<td><input type="text" class="s-id" data-i="'+i+'" value="'+s.sample_id+'"/></td>'
     +'<td>'+(s.dir||"")+'</td><td class="mono">'+s.r1_name+'</td><td class="mono">'+s.r2_name+'</td></tr>';});
  h+='</tbody></table></div><div class="hint">'+t("edithint")+'</div>';
  box.innerHTML=h;document.getElementById("form").appendChild(box);});}
function refreshPlan(){fetch(api("/api/hardware")).then(function(r){return r.json();}).then(function(hw){
  var n=SAMPLES.length||1;
  fetch(api("/api/plan?cores="+hw.cores+"&ram="+hw.ram_gb+"&n="+n)).then(function(r){return r.json();}).then(function(p){
    var box=document.getElementById("plan-box")||el("div",{id:"plan-box","class":"reason"});box.id="plan-box";
    box.textContent=t("plan")+": "+p.parallel_jobs+" x "+p.threads_per_job+" — "+p.reason;
    document.getElementById("form").appendChild(box);});});}
function refreshDbs(){var q="taxonomy_tool="+val("taxonomy_tool")+"&contig_taxonomy="+val("contig_taxonomy")+"&use_eggnog="+val("use_eggnog")+"&route_name="+val("route_name");
  fetch(api("/api/required-dbs?"+q)).then(function(r){return r.json();}).then(function(j){
    var box=document.getElementById("dbs-box")||el("div",{id:"dbs-box","class":"reason"});box.id="dbs-box";
    var lines=[];for(var k in j.required){var d=j.required[k];lines.push(k+": "+d.state+(d.version?(" "+d.version):"")+" — "+d.detail);}
    box.innerHTML=t("dbs")+":<br>"+(lines.join("<br>")||"—");
    document.getElementById("form").appendChild(box);});}
function collect(){var o={project_name:val("project_name"),raw_data_dir:val("raw_data_dir"),
  work_dir:val("work_dir"),route_name:val("route_name"),total_threads:parseInt(val("total_threads")||"16",10),
  taxonomy_tool:val("taxonomy_tool"),contig_taxonomy:val("contig_taxonomy"),use_eggnog:val("use_eggnog")};
  var rows=[];document.querySelectorAll(".s-inc").forEach(function(cb){
    if(!cb.checked)return;var i=cb.getAttribute("data-i");
    var idInp=document.querySelector('.s-id[data-i="'+i+'"]');
    var src=SAMPLES[i];if(!src)return;
    rows.push({sample_id:(idInp?idInp.value:src.sample_id).trim(),r1:src.r1,r2:src.r2});});
  if(rows.length)o.samples=rows;
  return o;}
function save(){fetch(api("/save"),{method:"POST",headers:{"Content-Type":"application/json"},
  body:JSON.stringify(collect())}).then(function(r){return r.json();}).then(function(j){
   var box=document.getElementById("result");
   if(j.ok){box.innerHTML='<div class="ok-box">'+t("saved")+" "+j.path+"<br>"+t("nexthint")+'</div>';}
   else{box.innerHTML='<div class="errs">'+(j.errors||[]).join("<br>")+'</div>';}});}
function setLang(l){LANG=l;render();}
render();
</script>
</body></html>
"""
