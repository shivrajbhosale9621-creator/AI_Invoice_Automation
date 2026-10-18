"""
run_30_graph_prompts.py - Sequential execution and verification of 32 Graph Prompts.

Logs full results to a summary file and stdout.
"""

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_agent import GraphAgent
from tests.test_graph_prompts import TEST_PROMPTS


def main():
    print("=" * 80)
    print("STARTING TEST SUITE: 32 DIVERSE GRAPH & CHART PROMPTS")
    print("=" * 80)

    ga = GraphAgent()
    results = []
    passed = 0
    failed = 0

    for i, (prompt, slug) in enumerate(TEST_PROMPTS, 1):
        print(f"\n[{i}/{len(TEST_PROMPTS)}] Prompt: \"{prompt}\" (slug: {slug})")
        t0 = time.time()
        try:
            res = ga.run(prompt)
            duration = time.time() - t0
            status = res.get("status")
            chart_type = res.get("chart_type")
            figs = res.get("figures", [])
            chart_paths = res.get("chart_paths", [])
            err = res.get("error")

            is_ok = (status == "success" and len(figs) > 0 and len(chart_paths) > 0)
            if is_ok:
                passed += 1
                disk_path = chart_paths[0].lstrip("/")
                file_size = os.path.getsize(disk_path) if os.path.exists(disk_path) else 0
                print(f"  --> PASSED ({duration:.1f}s) | Tool: {chart_type} | Figures: {len(figs)} | File: {chart_paths[0]} ({file_size} B)")
                results.append({
                    "index": i,
                    "prompt": prompt,
                    "slug": slug,
                    "status": "PASSED",
                    "tool": chart_type,
                    "figures": len(figs),
                    "chart_path": chart_paths[0],
                    "size_bytes": file_size,
                    "duration_sec": round(duration, 2),
                    "error": None
                })
            else:
                failed += 1
                print(f"  --> FAILED ({duration:.1f}s) | Status: {status} | Error: {err}")
                results.append({
                    "index": i,
                    "prompt": prompt,
                    "slug": slug,
                    "status": "FAILED",
                    "tool": chart_type,
                    "figures": len(figs),
                    "chart_path": chart_paths[0] if chart_paths else None,
                    "size_bytes": 0,
                    "duration_sec": round(duration, 2),
                    "error": str(err or res.get("message"))
                })
        except Exception as exc:
            duration = time.time() - t0
            failed += 1
            print(f"  --> EXCEPTION ({duration:.1f}s): {exc}")
            results.append({
                "index": i,
                "prompt": prompt,
                "slug": slug,
                "status": "EXCEPTION",
                "tool": "none",
                "figures": 0,
                "chart_path": None,
                "size_bytes": 0,
                "duration_sec": round(duration, 2),
                "error": str(exc)
            })

    print("\n" + "=" * 80)
    print(f"SUMMARY: Total Prompts: {len(TEST_PROMPTS)} | Passed: {passed} | Failed: {failed}")
    print("=" * 80)

    # Write markdown summary report
    report_path = "tests/graph_prompts_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 32 Graph Prompts Comprehensive Test Report\n\n")
        f.write(f"- **Total Prompts Tested**: {len(TEST_PROMPTS)}\n")
        f.write(f"- **Passed**: {passed}\n")
        f.write(f"- **Failed**: {failed}\n\n")
        f.write("| # | Prompt | Status | Tool | Figures | Duration | Chart File |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for r in results:
            chart_link = f"`{r['chart_path']}`" if r['chart_path'] else "None"
            f.write(f"| {r['index']} | {r['prompt']} | **{r['status']}** | `{r['tool']}` | {r['figures']} | {r['duration_sec']}s | {chart_link} |\n")
    print(f"Report written to: {report_path}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
