#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查思源桥待处理事项：遥测反馈 + GitHub open issues。

用法：
    python scripts/check_feedback.py [--days 365]

输出：
    1. 遥测反馈中 status != done 的条目（open=待处理，ignored=已忽略）
    2. 仓库 open issues

依赖：gh CLI 已登录（用于 issues）；遥测接口无需认证。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request

ENDPOINT = "https://siyuanbridgetelemetry.zingerplayground.top"
REPO = "alone-tree/siyuan-bridge"


def fetch_feedbacks(days: int) -> list[dict]:
    url = f"{ENDPOINT}/api/feedbacks?days={days}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "siyuan-bridge-feedback-check/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("feedbacks", [])


def fetch_open_issues() -> list[dict]:
    proc = subprocess.run(
        ["gh", "issue", "list", "--repo", REPO, "--state", "open",
         "--limit", "50", "--json", "number,title,createdAt,labels"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh issue list 失败: {proc.stderr.strip()}")
    return json.loads(proc.stdout or "[]")


def main() -> int:
    parser = argparse.ArgumentParser(description="检查思源桥未处理反馈与 open issues")
    parser.add_argument("--days", type=int, default=365, help="遥测反馈查询天数（默认 365）")
    args = parser.parse_args()

    print("=" * 60)
    print("1. 遥测反馈（status != done）")
    print("=" * 60)

    try:
        feedbacks = fetch_feedbacks(args.days)
    except Exception as exc:  # noqa: BLE001
        print(f"  [失败] 无法获取遥测反馈: {exc}")
        feedbacks = []

    pending = [f for f in feedbacks if f.get("status") != "done"]
    if not pending:
        print("  （无）")
    else:
        for f in sorted(pending, key=lambda x: x.get("ts", "")):
            print(f"  [{f.get('status', '?')}] #{f.get('id')} {f.get('ts', '')}")
            print(f"      {f.get('type', '?')} | {f.get('title', '')}")
            note = f.get("note")
            if note:
                print(f"      note: {note}")
            print()

    print("=" * 60)
    print(f"2. GitHub open issues（{REPO}）")
    print("=" * 60)

    try:
        issues = fetch_open_issues()
    except Exception as exc:  # noqa: BLE001
        print(f"  [失败] 无法获取 issues: {exc}")
        issues = []

    if not issues:
        print("  （无）")
    else:
        for i in sorted(issues, key=lambda x: x.get("createdAt", "")):
            labels = ",".join(l["name"] for l in i.get("labels", [])) or "-"
            print(f"  #{i['number']} {i['title']}  [{labels}]")
            print(f"      created: {i.get('createdAt', '')}")
            print()

    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
