#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""연중개최예상 기반 '오늘의 감시 타깃' 생성 (결정적).

competitions.json + 오늘 날짜로:
  1) 곧 새 회차가 뜰 대회(개최월이 향후 N개월 이내)를 우선순위로 뽑고,
  2) 전체 대회의 정규화 키 인덱스(신규 vs 기존 판별용)를 함께 출력한다.

일일 모니터링 루틴이 매일 실행 → 이 출력의 targets를 조준해 3소스를 뒤진다.
출력: JSON to stdout.

사용:
  python3 targets.py --file data/competitions.json [--today 2026-07-14] [--lookahead-months 3]
"""
import json
import re
import argparse
import datetime


def norm(name: str) -> str:
    """회차·연도·괄호·공백 제거 → 같은 대회를 한 키로 묶는 정규화 (앱 dedupe와 동일 규칙)."""
    k = re.sub(r"\(.*?\)", "", name)
    k = re.sub(r"통합\s*\d+\s*회", "", k)
    k = re.sub(r"제?\s*\d+\s*회", "", k)
    k = re.sub(r"\d{4}\s*년?", "", k)
    return re.sub(r"\s", "", k)


def expected_month(c: dict):
    d = c.get("details", {}).get("competitionDate", "") or ""
    if len(d) >= 7:
        try:
            return int(d[5:7])
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})", c.get("month", "") or "")
    return int(m.group(1)) if m else None


def round_num(c: dict):
    m = re.search(r"(\d+)", c.get("round", "") or "")
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/competitions.json")
    ap.add_argument("--today", help="YYYY-MM-DD (기본: 시스템 오늘)")
    ap.add_argument("--lookahead-months", type=int, default=3,
                    help="개최월이 오늘부터 이 개월 수 이내인 대회를 타깃으로 (기본 3)")
    a = ap.parse_args()

    today = datetime.date.fromisoformat(a.today) if a.today else datetime.date.today()
    with open(a.file, encoding="utf-8") as f:
        comps = json.load(f)["competitions"]

    # 전체 정규화 키 인덱스 — 스캔 결과가 신규인지 기존인지 판별하는 데 씀
    index = {}
    for c in comps:
        index.setdefault(norm(c["officialName"]), []).append({
            "officialName": c["officialName"],
            "roundNum": round_num(c),
            "month": c.get("month", ""),
            "region": c.get("region", ""),
            "sourceUrl": c.get("sourceUrl", ""),
        })

    # 조준 창: 오늘 월 ~ +lookahead 월 (연말 wrap 포함)
    win = {((today.month - 1 + k) % 12) + 1 for k in range(a.lookahead_months + 1)}

    targets, seen = [], set()
    for c in comps:
        em = expected_month(c)
        if em not in win:
            continue
        key = norm(c["officialName"])
        if key in seen:
            continue
        seen.add(key)
        latest = max(index[key], key=lambda e: (e["roundNum"] or 0))
        targets.append({
            "base": key,
            "officialName": latest["officialName"],
            "region": latest["region"],
            "lastRound": latest["roundNum"],
            "expectedMonth": em,
            "lastSourceUrl": latest["sourceUrl"],
        })
    targets.sort(key=lambda t: (t["expectedMonth"], t["region"]))

    out = {
        "today": today.isoformat(),
        "window_months": sorted(win),
        "total_known": len(comps),
        "target_count": len(targets),
        "targets": targets,
        "all_keys": sorted(index.keys()),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
