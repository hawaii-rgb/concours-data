#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""이미 처리한 소스 기사 ID 상태 — 비용 최적화(새 기사만 딥판독).

매일 소스 목록/검색에서 얻은 기사 ID를 이 상태와 차집합 → **새 것만** 클로드가 깊게(요강·hwp·포스터)
읽는다. 이미 처리한 기사는 다시 딥판독하지 않아 시간·토큰이 '그날 새 공고 수'에 비례하게 된다.
(회차전환·일정변경 같은 '기존 기사 변경'은 이 차집합으로 안 잡히므로, targets[] 대회의 sourceUrl은
별도로 재확인한다 — ROUTINE 참조.)

sourceUrl → 기사 ID 규칙:
  kukak21      board.php?...wr_id=<id>
  gugaktimes   article.html?no=<id>
  contestkorea view.php?...str_no=<id>

상태파일: tools/monitor/seen_articles.json = { "<source>": { "<id>": "<title>" } }

사용:
  python3 article_state.py --seed-from competitions.json          # 기존 sourceUrl로 최초 시드
  echo '[{"id":"43471","title":".."}]' | python3 article_state.py --source kukak21 --diff  # 새 것만 출력
  echo '["43471","43300"]'            | python3 article_state.py --source kukak21 --mark   # 처리완료 기록
"""
import json
import re
import argparse
import sys
import io
import os

DEFAULT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_articles.json")

SRC_ID_RE = {
    "kukak21": r"wr_id=(\d+)",
    "gugaktimes": r"[?&]no=(\d+)",
    "contestkorea": r"str_no=(\w+)",
}
SOURCES = list(SRC_ID_RE.keys())


def _read_stdin_json():
    return json.loads(sys.stdin.buffer.read().decode("utf-8"))


def _write_stdout_json(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def load(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save(path, d):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")


def src_of_url(u):
    for s in SOURCES:
        if s in u:
            return s
    return None


def id_from_url(u):
    s = src_of_url(u or "")
    if not s:
        return None, None
    m = re.search(SRC_ID_RE[s], u)
    return (s, m.group(1)) if m else (s, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--source", choices=SOURCES)
    ap.add_argument("--diff", action="store_true", help="stdin[{id,title}] 중 상태에 없는 새 것만 출력")
    ap.add_argument("--mark", action="store_true", help="stdin[id 또는 {id,title}]를 처리완료로 기록")
    ap.add_argument("--seed-from", help="competitions.json의 sourceUrl로 상태 최초 시드")
    a = ap.parse_args()

    state = load(a.state)

    if a.seed_from:
        with io.open(a.seed_from, encoding="utf-8") as f:
            data = json.load(f)
        n = 0
        for c in data.get("competitions", []):
            s, i = id_from_url(c.get("sourceUrl", "") or "")
            if s and i:
                state.setdefault(s, {}).setdefault(i, c.get("officialName", ""))
                n += 1
        save(a.state, state)
        print("시드 완료: %d개 sourceUrl 반영 → %s" %
              (n, ", ".join("%s:%d" % (k, len(state.get(k, {}))) for k in SOURCES)))
        return

    if not a.source or not (a.diff or a.mark):
        ap.error("--diff/--mark 는 --source 와 함께 사용")

    seen = state.get(a.source, {})
    items = _read_stdin_json()

    if a.diff:
        new = [it for it in items
               if (it["id"] if isinstance(it, dict) else str(it)) not in seen]
        _write_stdout_json(new)
        return

    # --mark
    for it in items:
        if isinstance(it, dict):
            seen[str(it["id"])] = it.get("title", "")
        else:
            seen.setdefault(str(it), "")
    state[a.source] = seen
    save(a.state, state)
    print("기록: %s +%d (누적 %d)" % (a.source, len(items), len(seen)))


if __name__ == "__main__":
    main()
