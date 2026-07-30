"""``python3 -m metaglens.demo`` — run the self-check without the CLI deps.

The CLI needs typer/rich; this entry point needs nothing beyond the standard
library, which is what makes it usable as a CI gate.
"""

from __future__ import annotations

import argparse
import json
import sys

from .runner import DEMO_ROUTES, run_demo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m metaglens.demo",
        description="Offline end-to-end self-check with a stub toolchain "
                    "(produces no scientific results).",
    )
    parser.add_argument("--route", default="all",
                        help="Route name, or 'all' for every demo route.")
    parser.add_argument("--keep", action="store_true",
                        help="Keep the temporary directory.")
    parser.add_argument("--verbose", action="store_true",
                        help="Stream stub output.")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Machine-readable output.")
    args = parser.parse_args(argv)

    targets = list(DEMO_ROUTES) if args.route == "all" else [args.route]
    results = []
    for target in targets:
        if not args.as_json:
            print(f"==> {target}")
        result = run_demo(target, keep=args.keep, verbose=args.verbose)
        results.append(result)
        if args.as_json:
            continue
        for stage in result["stages"]:
            mark = "ok " if stage["status"] == "completed" else "FAIL"
            print(f"    [{mark}] {stage['step']} "
                  f"(exit {stage['exit_code']}, {stage['status']})")
        if result["ok"]:
            print(f"    PASS: {target}")
        else:
            for problem in result["errors"]:
                print(f"    ERROR: {problem}")
            for missing in result["missing"]:
                print(f"    MISSING: {missing}")
            print(f"    left for inspection: {result['root']}")

    ok = all(r["ok"] for r in results)
    if args.as_json:
        print(json.dumps({"ok": ok, "runs": results}, ensure_ascii=False))
    else:
        print("PASS" if ok else "FAIL",
              f"— {len(results)} route(s); stub tools, no scientific output.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
