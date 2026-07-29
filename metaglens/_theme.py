"""Shared visual identity for all MetaGLens HTML surfaces.

Single source of truth for the poster theme so the delivery report
(``report.py``), the web config page (``express/webconfig.py``), and the live
monitor page (``observe/monitor.py``) never drift apart. The only thing that
should differ between surfaces is the logo.

``REPORT_CSS`` and ``LENS_SVG`` are the exact strings previously inlined in
``report.py`` and are re-exported verbatim to keep report output byte-identical.
"""

from __future__ import annotations

# Palette + base poster theme + report component styles. Kept as one block so
# the delivery report renders identically after extraction; the config and
# monitor pages reuse the same palette/base for a consistent skin.
REPORT_CSS = r"""
:root{--bg1:#eff6ff;--bg2:#dbe6f5;--panel:#fff;--ink:#33406a;--ink-soft:#4a5578;--muted:#7d8ca0;--line:#e4eaf3;--blue:#3b7de0;--blue-strong:#2f6fd0;--blue-soft:#cfe0f6;--navy:#1e2a66;--good:#2a9d8f;--warn:#d98a24;--bad:#e5556e;--brand:#38A8F0;}
*{box-sizing:border-box;} html,body{height:100%;}
body{margin:0;color:var(--ink);background:linear-gradient(160deg,var(--bg1),var(--bg2));background-attachment:fixed;font-family:"Times New Roman",Times,serif;font-size:17px;line-height:1.55;}
code,.mono,input,select,button,table{font-family:"Times New Roman",Times,serif;}
.bg-lens{position:fixed;z-index:0;pointer-events:none;}
#lensTR{top:-190px;right:-170px;width:640px;opacity:.09;transform:rotate(8deg);}
#lensBL{bottom:-240px;left:-210px;width:760px;opacity:.06;transform:rotate(-14deg);}
header,.meta,nav,main,footer{position:relative;z-index:1;}
header{padding:22px 34px 8px;display:flex;align-items:center;gap:20px;flex-wrap:wrap;}
.logo{height:88px;width:auto;display:block;}
.headline{margin-left:auto;text-align:right;}
.headline .t{font-size:22px;font-weight:700;}
.headline .d{font-size:15px;color:var(--muted);}
.meta{max-width:1180px;margin:0 auto;padding:6px 34px;display:flex;flex-wrap:wrap;gap:11px;}
.chip{background:rgba(255,255,255,.8);border:1px solid var(--line);border-radius:10px;padding:8px 15px;font-size:15px;color:var(--ink-soft);}
.chip b{color:var(--blue-strong);font-weight:700;}
nav{max-width:1180px;margin:12px auto 0;padding:0 24px;display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--line);}
nav button{background:none;border:none;color:var(--muted);padding:13px 18px;font-size:17px;cursor:pointer;border-bottom:3px solid transparent;font-weight:700;transition:all .2s;}
nav button:hover{color:var(--ink);} nav button.active{color:var(--blue-strong);border-bottom-color:var(--blue-strong);}
main{max-width:1180px;margin:0 auto;padding:24px 34px 8px;}
section{display:none;animation:fadeIn .3s ease;} section.active{display:block;}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}
h2{font-size:24px;margin:0 0 4px;} .hint{color:var(--muted);font-size:15.5px;margin:0 0 16px;}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:18px;box-shadow:0 8px 24px rgba(40,70,120,.06);}
.controls{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:14px;}
label.ctl{font-size:15.5px;color:var(--muted);display:flex;gap:8px;align-items:center;}
select,input[type="search"]{background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:8px 12px;font-size:15px;}
table{width:100%;border-collapse:collapse;font-size:15.5px;} th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);}
th{color:var(--muted);font-weight:700;cursor:pointer;white-space:nowrap;} th:hover{color:var(--ink);}
tbody tr:hover{background:rgba(59,125,224,.05);} td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;}
.bar-cell{position:relative;} .barfill{position:absolute;left:0;top:3px;bottom:3px;background:var(--blue-soft);border-right:2px solid var(--blue);border-radius:5px;} .barval{position:relative;padding-left:6px;}
.tag{display:inline-block;padding:1px 9px;border-radius:6px;font-size:13.5px;} .tag.dir{background:rgba(59,125,224,.12);color:var(--blue-strong);}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:14px;font-size:14.5px;} .legend span{display:inline-flex;align-items:center;gap:6px;color:var(--muted);} .sw{width:13px;height:13px;border-radius:3px;display:inline-block;}
.flex{display:flex;gap:16px;flex-wrap:wrap;} .stat{flex:1;min-width:150px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 8px 24px rgba(40,70,120,.05);}
.stat .k{font-size:14.5px;color:var(--muted);} .stat .v{font-size:32px;font-weight:800;color:var(--blue-strong);margin-top:2px;}
.source-note{background:rgba(59,125,224,.07);border:1px solid rgba(59,125,224,.25);border-radius:10px;padding:11px 15px;font-size:15.5px;margin-bottom:14px;color:var(--ink-soft);} .source-note b{color:var(--blue-strong);}
.heat-wrap{overflow-x:auto;} svg text{fill:var(--muted);font-size:13px;font-family:"Times New Roman",Times,serif;}
.empty{color:var(--muted);font-size:15.5px;padding:8px 2px;}
.btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink-soft);cursor:pointer;font-size:14px;transition:all .15s;} .btn:hover{border-color:var(--blue);color:var(--blue);}
.tl-row{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--line);} .tl-step{min-width:140px;font-weight:700;} .tl-bar{flex:1;height:8px;border-radius:4px;background:var(--line);position:relative;} .tl-fill{height:100%;border-radius:4px;} .tl-meta{min-width:180px;font-size:14px;color:var(--muted);text-align:right;}
footer{color:var(--muted);font-size:14px;padding:24px 34px 32px;text-align:center;}
@media(max-width:700px){header{padding:16px;} .meta{padding:6px 16px;} main{padding:18px 16px;} nav{padding:0 10px;} nav button{padding:10px 12px;font-size:15px;} .stat .v{font-size:24px;}}
"""

# The two hexagonal-aperture lens SVGs used as fixed background decoration.
LENS_SVG = (
    '<div class="bg-lens" id="lensTR" aria-hidden="true"><svg viewBox="0 0 200 200" width="100%" height="100%">'
    '<g fill="#3b7de0" stroke="#fff" stroke-width="0.8" stroke-opacity="0.5" stroke-linejoin="round">'
    '<polygon points="100,4 183.14,52 131.18,82 100,64" opacity="0.5"/>'
    '<polygon points="183.14,52 183.14,148 131.18,118 131.18,82" opacity="0.68"/>'
    '<polygon points="183.14,148 100,196 100,136 131.18,118" opacity="0.85"/>'
    '<polygon points="100,196 16.86,148 68.82,118 100,136" opacity="1"/>'
    '<polygon points="16.86,148 16.86,52 68.82,82 68.82,118" opacity="0.8"/>'
    '<polygon points="16.86,52 100,4 100,64 68.82,82" opacity="0.62"/></g></svg></div>'
    '<div class="bg-lens" id="lensBL" aria-hidden="true"><svg viewBox="0 0 200 200" width="100%" height="100%">'
    '<g fill="#2f6fd0" stroke="#fff" stroke-width="0.8" stroke-opacity="0.5" stroke-linejoin="round">'
    '<polygon points="100,4 183.14,52 131.18,82 100,64" opacity="0.5"/>'
    '<polygon points="183.14,52 183.14,148 131.18,118 131.18,82" opacity="0.68"/>'
    '<polygon points="183.14,148 100,196 100,136 131.18,118" opacity="0.85"/>'
    '<polygon points="100,196 16.86,148 68.82,118 100,136" opacity="1"/>'
    '<polygon points="16.86,148 16.86,52 68.82,82 68.82,118" opacity="0.8"/>'
    '<polygon points="16.86,52 100,4 100,64 68.82,82" opacity="0.62"/></g></svg></div>'
)
