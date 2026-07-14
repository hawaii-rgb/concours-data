#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""제안된 변경(신규/갱신)을 competitions.json에 '안전하게' 반영 (가드레일).

승인 단계 없이 매일 자동 push되는 파이프라인이므로, 잘못된 스캔이 실사용자 앱을
훼손하지 못하도록 하드 가드레일을 강제한다:
  · 사실 필드만 갱신(날짜·장소·부문·접수·회차). 그 외 키는 무시.
  · noticeImages / noticeText(큐레이션·포스터)는 절대 불가침.
  · 기존 항목 삭제 불가(이 스크립트는 add/update만).
  · 반영 전 백업(.bak.monitorYYYYMMDD) 생성 + JSON 왕복 검증.
  · 무엇이 바뀌었는지 요약 출력(커밋 메시지·사후 검토용).

입력(--changes changes.json) 형식:
{
  "new": [
    { "officialName": "제1회 ○○ 전국국악경연대회", "region": "전북", "host": "...",
      "round": "제1회", "month": "2026-09", "genres": ["판소리"], "levels": ["일반부"],
      "sourceUrl": "https://<공홈>", "details": { "competitionDate":"2026-09-05",
      "applyStart":"2026-08-01", "applyEnd":"2026-08-20", "venue":"..." } }
  ],
  "update": [
    // 회차 전환/일정 변경: 기존 항목을 지목해 사실 필드만 교체
    { "id": "제35회 ○○|제35회|2025-08",           // 정확 id, 또는
      "match_base": "○○전국국악경연대회",           // 정규화 base로 지목(회차 무관)
      "fields":  { "officialName":"제36회 ○○ 전국국악경연대회", "round":"제36회", "month":"2026-08" },
      "details": { "competitionDate":"2026-08-22", "applyEnd":"2026-08-01" } }
  ]
}

사용:
  python3 safe_merge.py --file data/competitions.json --changes changes.json [--dry-run]
"""
import json
import re
import argparse
import datetime
import shutil
import sys

# 갱신 허용 = 사실 필드만. 이 목록 밖의 키는 무시된다(불가침).
ALLOWED_TOP = {"officialName", "region", "city", "month", "round", "host", "sourceUrl", "genres", "levels"}
ALLOWED_DETAIL = {"competitionDate", "dateEnd", "dateText", "venue",
                  "applyStart", "applyEnd", "applyText", "registrationOrder", "contact"}
# 절대 건드리지 않음(신규 항목 생성 시에도 스캔값 대신 빈 값 유지):
FORBIDDEN = {"noticeImages", "noticeText"}


def norm(name: str) -> str:
    k = re.sub(r"\(.*?\)", "", name or "")
    k = re.sub(r"통합\s*\d+\s*회", "", k)
    k = re.sub(r"제?\s*\d+\s*회", "", k)
    k = re.sub(r"\d{4}\s*년?", "", k)
    return re.sub(r"\s", "", k)


def cid(c: dict) -> str:
    return f"{c.get('officialName','')}|{c.get('round','')}|{c.get('month','')}"


def apply_fields(entry: dict, fields: dict, details: dict, log: list):
    changed = []
    for k, v in (fields or {}).items():
        if k in FORBIDDEN:
            continue
        if k in ALLOWED_TOP and entry.get(k) != v:
            entry[k] = v
            changed.append(k)
    if details:
        d = entry.setdefault("details", {})
        for k, v in details.items():
            if k in ALLOWED_DETAIL and d.get(k) != v:
                d[k] = v
                changed.append(f"details.{k}")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="data/competitions.json")
    ap.add_argument("--changes", required=True)
    ap.add_argument("--dry-run", action="store_true", help="반영 없이 요약만 출력")
    a = ap.parse_args()

    with open(a.file, encoding="utf-8") as f:
        root = json.load(f)
    comps = root["competitions"]
    with open(a.changes, encoding="utf-8") as f:
        changes = json.load(f)

    by_id = {cid(c): c for c in comps}
    by_base = {}
    for c in comps:
        by_base.setdefault(norm(c["officialName"]), []).append(c)

    summary = {"added": [], "updated": [], "skipped": []}

    # --- 신규 ---
    for item in changes.get("new", []):
        name = item.get("officialName", "").strip()
        det = item.get("details", {}) or {}
        has_date = bool(det.get("competitionDate") or det.get("applyEnd") or item.get("month"))
        if not name or not has_date:
            summary["skipped"].append(f"신규 스킵(이름/날짜 부족): {name or '(무명)'}")
            continue
        # 중복 방지: 동일 id 또는 (동일 base + 동일 회차)면 스킵
        if cid(item) in by_id:
            summary["skipped"].append(f"신규 스킵(중복 id): {name}")
            continue
        base = norm(name)
        rnd = item.get("round", "")
        if any(e.get("round", "") == rnd for e in by_base.get(base, [])):
            summary["skipped"].append(f"신규 스킵(동일 회차 존재): {name}")
            continue
        # 불가침 필드는 스캔값 무시하고 빈 값으로 생성
        clean = {k: v for k, v in item.items() if k not in FORBIDDEN}
        clean.setdefault("noticeImages", [])
        clean.setdefault("noticeText", "")
        comps.append(clean)
        by_id[cid(clean)] = clean
        by_base.setdefault(base, []).append(clean)
        summary["added"].append(name)

    # --- 갱신(회차 전환/일정 변경) ---
    for upd in changes.get("update", []):
        target = None
        if upd.get("id") and upd["id"] in by_id:
            target = by_id[upd["id"]]
        elif upd.get("match_base"):
            cands = by_base.get(upd["match_base"].strip(), [])
            if cands:
                # 회차 지정 없으면 최신 회차 항목을 대상으로
                def rn(c):
                    m = re.search(r"(\d+)", c.get("round", "") or "")
                    return int(m.group(1)) if m else 0
                target = max(cands, key=rn)
        if target is None:
            summary["skipped"].append(f"갱신 스킵(대상 못 찾음): {upd.get('id') or upd.get('match_base')}")
            continue
        before = target.get("officialName", "")
        changed = apply_fields(target, upd.get("fields", {}), upd.get("details", {}), None)
        if changed:
            summary["updated"].append(f"{before} → [{', '.join(changed)}]")
        else:
            summary["skipped"].append(f"갱신 무변화: {before}")

    # --- 검증 & 쓰기 ---
    text = json.dumps(root, ensure_ascii=False, indent=2)
    json.loads(text)  # 왕복 검증

    n_add, n_upd = len(summary["added"]), len(summary["updated"])
    print("=== safe_merge 요약 ===")
    print(f"신규 {n_add} · 갱신 {n_upd} · 스킵 {len(summary['skipped'])}")
    for x in summary["added"]:
        print(f"  + 신규: {x}")
    for x in summary["updated"]:
        print(f"  ~ 갱신: {x}")
    for x in summary["skipped"]:
        print(f"  - 스킵: {x}")

    if a.dry_run:
        print("\n[dry-run] 파일 미변경.")
        return

    if n_add == 0 and n_upd == 0:
        print("\n변경 없음 — 파일·백업 미생성.")
        return

    stamp = datetime.date.today().strftime("%Y%m%d")
    shutil.copyfile(a.file, f"{a.file}.bak.monitor{stamp}")
    with open(a.file, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\n반영 완료 → {a.file} (백업: {a.file}.bak.monitor{stamp})")
    # 커밋 메시지 한 줄 제안(루틴이 그대로 사용 가능)
    bits = []
    if n_add:
        bits.append(f"신규 {n_add}")
    if n_upd:
        bits.append(f"갱신 {n_upd}")
    print(f"COMMIT_MSG: 자동 모니터링: {' · '.join(bits)} ({stamp})")


if __name__ == "__main__":
    main()
