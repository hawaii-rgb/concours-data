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


def rnum(c: dict) -> str:
    """회차 숫자만(표기 차이 흡수). 없으면 빈 문자열."""
    m = re.search(r"(\d+)", c.get("round", "") or "")
    return m.group(1) if m else ""


def url_round_key(c: dict):
    """(sourceUrl, 회차번호) — 같은 값이면 같은 대회로 본다(표기·id 달라도).
    임실 이중등록·대전/大田 중복이 정확히 이 신호로 잡힌다. sourceUrl 없으면 None."""
    u = (c.get("sourceUrl", "") or "").strip()
    return (u, rnum(c)) if u else None


def entry_keys(c: dict):
    """이 항목을 대표하는 정규화 키 = 정식명 + aka 별칭(표기 변형). aka에 기록해 둔 변형이
    재등장해도 신규가 아닌 기존으로 매칭된다(재중복·재판단 방지)."""
    keys = {norm(c.get("officialName", ""))}
    for a in c.get("aka", []) or []:
        if a:
            keys.add(norm(a))
    return keys


# --- 날짜 정합성 게이트 ---
WD = ["월", "화", "수", "목", "금", "토", "일"]


def _iso(s):
    try:
        return datetime.datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def date_ordering_errors(det: dict):
    """접수시작≤마감≤대회일≤종료 위반(순수 산술 — 오탐 없음). 위반 시 신규/갱신 차단용."""
    aS, aE = _iso(det.get("applyStart")), _iso(det.get("applyEnd"))
    cD, dE = _iso(det.get("competitionDate")), _iso(det.get("dateEnd"))
    errs = []
    if aS and aE and aS > aE:
        errs.append(f"접수시작>마감({aS}>{aE})")
    if aE and cD and aE > cD:
        errs.append(f"접수마감>대회일({aE}>{cD})")
    if cD and dE and cD > dE:
        errs.append(f"대회시작>종료({cD}>{dE})")
    return errs


def date_warnings(c: dict):
    """월↔대회일 불일치 + 요일 정합성(요강 명시 요일 vs 실제 계산). 파싱 의존이라 경고만(차단 아님).
    요일 불일치는 연도 오류의 결정타 — 자동으로 짚어 준다."""
    det = c.get("details", {})
    warns = []
    cD = _iso(det.get("competitionDate"))
    dEnd = _iso(det.get("dateEnd"))
    mo = c.get("month", "") or ""
    mm = re.match(r"\d{4}-(\d{2})", mo) or re.search(r"(\d{1,2})\s*월", mo)
    if cD and mm:
        try:
            # 대회가 여러 달 걸치면(예: 5.23~6.7) month는 시작·종료 어느 달과 맞아도 정상.
            months = {cD.month} | ({dEnd.month} if dEnd else set())
            if int(mm.group(1)) not in months:
                warns.append(f"month({mo})↔대회일({cD}) 월 불일치")
        except ValueError:
            pass
    base = cD or _iso(det.get("applyEnd")) or _iso(det.get("applyStart"))
    year = base.year if base else None
    if year:
        for fld in ("dateText", "applyText"):
            yr = year
            for m in re.finditer(r"(20\d{2})|(\d{1,2})\s*[.월]\s*(\d{1,2})\s*일?\s*\(([월화수목금토일])\)",
                                 det.get(fld, "") or ""):
                if m.group(1):
                    yr = int(m.group(1))
                    continue
                try:
                    d = datetime.date(yr, int(m.group(2)), int(m.group(3)))
                except ValueError:
                    continue
                if WD[d.weekday()] != m.group(4):
                    warns.append(f"{fld} 요일불일치 {m.group(2)}.{m.group(3)}({m.group(4)})→실제{WD[d.weekday()]}")
    return warns


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
        for k in entry_keys(c):                     # 정식명 + aka 별칭 모두로 색인(재중복 방지)
            by_base.setdefault(k, []).append(c)
    # aka 전용 색인 — 큐레이션된 변형이라, 여기에 걸리면 회차 무관 '기존 대회'로 확정(업데이트 대상).
    by_aka = {}
    for c in comps:
        for ak in c.get("aka", []) or []:
            if ak:
                by_aka[norm(ak)] = c
    # (sourceUrl, 회차) → 항목들. 같은 키 = 동일대회 신호(표기·id 무관).
    by_url_round = {}
    for c in comps:
        k = url_round_key(c)
        if k:
            by_url_round.setdefault(k, []).append(c)

    summary = {"added": [], "updated": [], "skipped": [], "warnings": []}

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
        # aka 변형은 회차가 달라도 기존 대회다 → 신규로 만들지 말고 업데이트로 처리(재중복 방지).
        if base in by_aka:
            summary["skipped"].append(
                f"신규 스킵(기존 대회의 aka 변형 → update로 처리해야 함): {name} = {by_aka[base].get('officialName','')}")
            continue
        # ★ 동일대회 중복 차단: 같은 sourceUrl+회차인 기존 항목이 있으면(표기만 달라도) 이중등록이다.
        urk = url_round_key(item)
        if urk and by_url_round.get(urk):
            summary["skipped"].append(
                f"신규 스킵(동일 sourceUrl+회차 → 동일대회 중복 방지): {name} = {by_url_round[urk][0].get('officialName','')}")
            continue
        # ★ 날짜 순서 게이트: 접수시작>마감 등 논리 오류면 차단(잘못된 날짜가 앱에 노출되는 것 방지).
        oe = date_ordering_errors(det)
        if oe:
            summary["skipped"].append(f"신규 스킵(날짜 순서 오류: {', '.join(oe)}): {name}")
            continue
        # 불가침 필드는 스캔값 무시하고 빈 값으로 생성
        clean = {k: v for k, v in item.items() if k not in FORBIDDEN}
        clean.setdefault("noticeImages", [])
        clean.setdefault("noticeText", "")
        comps.append(clean)
        by_id[cid(clean)] = clean
        by_base.setdefault(base, []).append(clean)
        if urk:
            by_url_round.setdefault(urk, []).append(clean)
        summary["added"].append(name)
        # 골격 경고: 접수마감만 있고 접수시작이 비었는데 partial 표기도 없음 → "한 방에 완성" 규칙 위반
        cd = clean.get("details", {}) or {}
        if cd.get("applyEnd") and not cd.get("applyStart") and cd.get("confidence") != "partial":
            summary["warnings"].append(
                f"골격 신규(접수시작 없음·partial 아님 → 요강/공홈 재추출 또는 confidence=partial 필요): {name}")

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
        # ★ 결과 id 충돌 가드: 이 갱신이 officialName/round/month를 바꿔 '다른 기존 항목'과
        #   동일 id가 되면(= 중복 생성) 반영하지 않는다. (임실 핑퐁 사고 방지: 한 공고가
        #   plain·prefixed 두 base를 각각 같은 회차로 만들어 이중등록되던 문제.)
        f = upd.get("fields", {}) or {}
        old_id = cid(target)
        prospective = "{}|{}|{}".format(
            f.get("officialName", target.get("officialName", "")),
            f.get("round", target.get("round", "")),
            f.get("month", target.get("month", "")),
        )
        if prospective != old_id:
            other = by_id.get(prospective)
            if other is not None and other is not target:
                summary["skipped"].append(
                    f"갱신 스킵(결과 id가 기존 다른 항목과 중복 → 이중등록 방지): "
                    f"{target.get('officialName','')} ⇒ {prospective}")
                continue
        # ★ 날짜 순서 게이트: 갱신 적용 후(기존+변경 병합) 날짜 논리 오류면 차단.
        eff = dict(target.get("details", {}) or {})
        eff.update(upd.get("details", {}) or {})
        oe = date_ordering_errors(eff)
        if oe:
            summary["skipped"].append(f"갱신 스킵(날짜 순서 오류: {', '.join(oe)}): {target.get('officialName','')}")
            continue
        before = target.get("officialName", "")
        changed = apply_fields(target, upd.get("fields", {}), upd.get("details", {}), None)
        if changed:
            new_id = cid(target)
            if new_id != old_id:                       # id가 바뀌면 인덱스도 갱신
                by_id.pop(old_id, None)
                by_id[new_id] = target
            summary["updated"].append(f"{before} → [{', '.join(changed)}]")
        else:
            summary["skipped"].append(f"갱신 무변화: {before}")

    # --- 검증 & 쓰기 ---
    from collections import Counter
    # 경고(차단 아님): 같은 sourceUrl+회차인데 서로 다른 항목 = 동일대회 중복 의심(표기·id 달라
    #   앱 목록에 두 번 뜸). 핑퐁(동일 id)과 달리 표시 중복이라 하드차단 대신 로그로 정리 유도.
    ur_index = {}
    for c in comps:
        k = url_round_key(c)
        if k:
            ur_index.setdefault(k, []).append(c.get("officialName", ""))
    for (u, r), names in ur_index.items():
        uniq = sorted(set(names))
        if len(uniq) > 1:
            summary["warnings"].append(
                f"동일 sourceUrl+제{r}회 중복 의심(정리 대상): {' / '.join(uniq)}  [{u}]")

    # 경고: 날짜 정합성(월↔대회일, 요일 불일치=연도 오류 신호). 파싱 의존이라 차단 아닌 경고.
    for c in comps:
        for w in date_warnings(c):
            summary["warnings"].append(f"{(c.get('officialName', '') or '')[:24]}: {w}")

    # ★ 하드 백스톱: 동일 id(officialName|round|month) 중복이 하나라도 있으면 절대 쓰지 않는다.
    #   동일 id 두 항목은 앱 스냅샷 슬롯을 공유해 '접수중↔예정 무한 NEW 핑퐁'을 일으킨다.
    #   (프로즈 규칙만으론 못 막던 실사고를 코드로 원천 차단 — dry-run에서도 검사.)
    dup_ids = [k for k, v in Counter(cid(c) for c in comps).items() if v > 1]
    if dup_ids:
        print("=== ⛔ 중단: 동일 id 중복 발생(핑퐁 유발) — 파일 미변경 ===", file=sys.stderr)
        for k in dup_ids:
            print(f"  중복 id: {k}", file=sys.stderr)
        print("한 대회가 이중등록됨. 같은 시리즈의 두 항목을 하나로 병합한 뒤 재실행하라.", file=sys.stderr)
        sys.exit(2)

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
    for x in summary["warnings"]:
        print(f"  ⚠ 경고: {x}")

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
