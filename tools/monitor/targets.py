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
    mo = c.get("month", "") or ""
    m = re.match(r"\d{4}-(\d{2})", mo)          # "YYYY-MM" → 월 (구버그: \d{1,2}가 "20"을 잡던 것 수정)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{1,2})\s*월", mo)         # "9월"
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,2})\b", mo)          # 맨 숫자 "11"
    return int(m.group(1)) if m else None


def entry_keys(c: dict):
    """이 항목을 대표하는 정규화 키들 = 정식명 + aka 별칭들(표기 변형).
    aka에 기록된 변형은 all_keys/타깃 매칭에 포함돼, 그 변형이 재등장해도 신규가 아닌 기존으로 잡힌다."""
    keys = {norm(c["officialName"])}
    for a in c.get("aka", []) or []:
        if a:
            keys.add(norm(a))
    return keys


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

    # 전체 정규화 키 인덱스 — 스캔 결과가 신규인지 기존인지 판별하는 데 씀.
    # 각 항목을 정식명 + aka 별칭들로 등록 → all_keys에 표기 변형까지 포함(재중복 방지).
    index = {}
    for c in comps:
        info = {
            "officialName": c["officialName"],
            "roundNum": round_num(c),
            "month": c.get("month", ""),
            "region": c.get("region", ""),
            "sourceUrl": c.get("sourceUrl", ""),
        }
        for k in entry_keys(c):
            index.setdefault(k, []).append(info)

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
