"""Terminal-language selection for interactive output.

Only the conversation is translated. Deliverables — ``run_log.md``,
``methods.md``, ``report.html`` — stay English by contract, so results remain
comparable and quotable regardless of who ran them.

Message keys resolve through the requested language, then English, then the key
itself, so a missing translation degrades to something readable rather than
raising.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

SUPPORTED = ("en", "zh")
DEFAULT = "en"

_ACTIVE = DEFAULT

# Interactive strings only. Anything written into a deliverable must not appear
# here — see the module docstring.
MESSAGES: Dict[str, Dict[str, str]] = {
    "en": {
        "cfg.not_found": "Config not found",
        "cfg.run_init": "Run 'metaglens init' to create one.",
        "run.finished": "Pipeline finished.",
        "run.already_done": "already completed, skipping.",
        "run.materialize": "Materialize",
        "run.execute": "Execute pipeline",
        "doctor.no_problems": "No blocking problems for this route.",
        "doctor.tools": "Tools",
        "doctor.not_needed": "not needed by this route",
        "doctor.databases": "Databases (required by this route)",
        "db.none_needed": "This route needs no reference databases.",
        "db.space_ok": "Space check passed.",
        "db.space_short": "Not enough free space — choose a destination on a larger filesystem.",
        "gate.all_passed": "All applicable gates passed.",
        "gate.warn_not_blocking": "Warnings present but not blocking.",
        "gate.use_strict": "Use --strict to treat them as errors.",
        "plan.ready": "Nothing blocking — ready to run.",
        "plan.coarse": "Estimates are coarse",
        "diag.no_failure": "No failed stage recorded.",
        "diag.unknown": ("No known signature matched, so no cause is being "
                         "guessed — the evidence above is what was recorded."),
        "demo.stub_notice": ("Stub tools stand in for the real toolchain — this "
                             "checks the plumbing only and produces NO "
                             "scientific results."),
        "demo.passed": "Self-check passed",
        "monitor.stopped": "Monitor stopped (monitor.html keeps its last state).",
        "watch.quit_hint": "Press q to leave this view — the run keeps going.",
        "label.why": "Why",
        "label.next": "Next",
        "label.cause": "Cause",
        "label.evidence": "Evidence",
        "label.log": "Log",
    },
    "zh": {
        "cfg.not_found": "未找到配置文件",
        "cfg.run_init": "运行 'metaglens init' 创建一个。",
        "run.finished": "流程结束。",
        "run.already_done": "已完成，跳过。",
        "run.materialize": "生成脚本",
        "run.execute": "执行流程",
        "doctor.no_problems": "本路线没有阻塞性问题。",
        "doctor.tools": "工具",
        "doctor.not_needed": "本路线不需要",
        "doctor.databases": "数据库（本路线需要）",
        "db.none_needed": "本路线不需要参考数据库。",
        "db.space_ok": "空间检查通过。",
        "db.space_short": "剩余空间不足 —— 请换一个更大的分区作为目标目录。",
        "gate.all_passed": "所有适用的质量门禁均通过。",
        "gate.warn_not_blocking": "存在警告，但不阻塞运行。",
        "gate.use_strict": "加 --strict 可将警告视为错误。",
        "plan.ready": "没有阻塞项 —— 可以开始运行。",
        "plan.coarse": "以下为粗略估算",
        "diag.no_failure": "没有记录到失败的阶段。",
        "diag.unknown": "没有匹配到已知的失败特征，因此不猜测原因 —— 上面是实际记录到的证据。",
        "demo.stub_notice": "使用桩工具替代真实工具链 —— 只验证流程连通性，不产生任何科学结果。",
        "demo.passed": "自检通过",
        "monitor.stopped": "监控已停止（monitor.html 保留最后状态）。",
        "watch.quit_hint": "按 q 退出此界面 —— 运行会继续进行。",
        "label.why": "原因",
        "label.next": "下一步",
        "label.cause": "归因",
        "label.evidence": "证据",
        "label.log": "日志",
    },
}


def normalise(lang: Optional[str]) -> str:
    """Map anything user-supplied onto a supported language code."""
    if not lang:
        return DEFAULT
    code = str(lang).strip().lower().replace("_", "-")
    if code in SUPPORTED:
        return code
    prefix = code.split("-")[0]
    if prefix in SUPPORTED:
        return prefix
    if prefix in ("cn", "chs", "hans"):
        return "zh"
    return DEFAULT


def detect(cli_lang: Optional[str] = None,
           accept_language: Optional[str] = None,
           env: Optional[Dict[str, str]] = None) -> str:
    """Resolve the language: CLI flag > Accept-Language > locale env > default."""
    if cli_lang:
        return normalise(cli_lang)
    if accept_language:
        first = accept_language.split(",")[0].split(";")[0]
        resolved = normalise(first)
        if resolved != DEFAULT or first.lower().startswith(("en",)):
            return resolved
    environ = env if env is not None else os.environ
    for key in ("METAGLENS_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = (environ.get(key) or "").strip()
        if value:
            code = value.split(".")[0]
            resolved = normalise(code)
            if resolved != DEFAULT or code.lower().startswith("en"):
                return resolved
    return DEFAULT


def set_language(lang: Optional[str]) -> str:
    """Set the process-wide interactive language; returns what was applied."""
    global _ACTIVE
    _ACTIVE = normalise(lang)
    return _ACTIVE


def language() -> str:
    return _ACTIVE


def t(key: str, lang: Optional[str] = None) -> str:
    """Translate ``key``, degrading to English and then to the key itself."""
    code = normalise(lang) if lang else _ACTIVE
    return (MESSAGES.get(code, {}).get(key)
            or MESSAGES[DEFAULT].get(key)
            or key)
