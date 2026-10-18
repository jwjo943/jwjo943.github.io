"""data/products.json 의 git 이력을 훑어 "공시 변경 이력"을 만든다.

사용:
    py scripts/history.py                  # 변경 이력 요약 출력
    py scripts/history.py --json           # 생성기가 쓰는 형태 그대로

이것이 왜 있나:
    한화생명 공시실은 **현재 상태만** 보여준다. "이 상품이 언제 판매중단됐는지",
    "이번 주 신규가 무엇인지"는 원문에 없다. 주 1회 스냅샷이 git 에 쌓이므로
    그 차이가 이 사이트만 답할 수 있는 정보다.

전제 (중요):
    git 이력 전체가 있어야 한다. actions/checkout 은 기본이 fetch-depth:1 이라
    얕은 클론에서는 리비전이 1개뿐이고 변경 이력이 빈 상태로 나온다.
    .github/workflows/refresh.yml 에서 fetch-depth: 0 을 지정한다.
    git 이 없거나 얕아도 **예외를 던지지 않는다** - 이력이 비면 사이트는
    변경 이력 섹션만 생략하고 정상 빌드된다.

스냅샷 순서:
    git 리비전(오래된 것 -> 최신) + 마지막에 작업트리 파일.
    워크플로가 '빌드 -> 커밋' 순서라 빌드 시점의 최신 데이터는 아직 커밋 전이다.
    그래서 작업트리를 항상 마지막 스냅샷으로 넣는다 (직전 리비전과 내용이
    같으면 넣지 않는다).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_PATH = "data/products.json"

#: 변경 종류. 사이트 표기와 정렬 순서를 여기서만 정한다.
KIND_LABEL = {
    "added": "신규",
    "removed": "목록에서 사라짐",
    "suspended": "판매중단",
    "resumed": "판매재개",
    "terms": "판매기간 구간 변경",
    "documents": "공시문서 변경",
}
KIND_ORDER = list(KIND_LABEL)


def _git(args: list[str], repo: Path) -> str | None:
    """git 호출. 실패하면 None (git 없음·얕은 클론·경로 없음 모두 포함)."""
    try:
        done = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, encoding="utf-8",
        )
    except FileNotFoundError:
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def revisions(repo: Path, path: str) -> list[str]:
    """path 를 건드린 커밋 해시. 오래된 것부터."""
    out = _git(["log", "--format=%H", "--", path], repo)
    if not out:
        return []
    return list(reversed(out.split()))


def load_rev(repo: Path, rev: str, path: str) -> dict | None:
    out = _git(["show", f"{rev}:{path}"], repo)
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _state(payload: dict) -> dict[int, dict]:
    """상품별 비교용 상태. 비교에 쓰는 값만 뽑는다."""
    state = {}
    for p in payload.get("products", []):
        terms = p.get("terms") or []
        state[p["idx"]] = {
            "name": p["name"],
            "terms": len(terms),
            "documents": sum(len(t.get("documents") or []) for t in terms),
            "onSale": any(t.get("onSale") for t in terms),
        }
    return state


def diff_state(old: dict[int, dict], new: dict[int, dict]) -> list[dict]:
    """두 스냅샷 사이의 상품별 변경. 종류별로 한 건씩."""
    events: list[dict] = []

    for idx in sorted(new.keys() - old.keys()):
        events.append({"idx": idx, "name": new[idx]["name"], "kind": "added"})
    for idx in sorted(old.keys() - new.keys()):
        events.append({"idx": idx, "name": old[idx]["name"], "kind": "removed"})

    for idx in sorted(old.keys() & new.keys()):
        o, n = old[idx], new[idx]
        if o["onSale"] and not n["onSale"]:
            events.append({"idx": idx, "name": n["name"], "kind": "suspended"})
        elif not o["onSale"] and n["onSale"]:
            events.append({"idx": idx, "name": n["name"], "kind": "resumed"})
        if o["terms"] != n["terms"]:
            events.append({
                "idx": idx, "name": n["name"], "kind": "terms",
                "from": o["terms"], "to": n["terms"],
            })
        if o["documents"] != n["documents"]:
            events.append({
                "idx": idx, "name": n["name"], "kind": "documents",
                "from": o["documents"], "to": n["documents"],
            })

    events.sort(key=lambda ev: (KIND_ORDER.index(ev["kind"]), ev["name"]))
    return events


def build(
    repo: Path | None = None,
    path: str = DEFAULT_PATH,
    current: dict | None = None,
) -> dict[str, Any]:
    """변경 이력을 만든다.

    반환:
        {
          "revisions": 비교에 쓴 스냅샷 수,
          "timeline": [{"asof", "prevAsof", "events": [...]}, ...]   최신 먼저
          "byProduct": {idx: [{"asof", "kind", ...}, ...]}           최신 먼저
        }
    이력을 만들 수 없으면 timeline·byProduct 가 빈 상태로 돌아온다.
    """
    repo = repo or Path(__file__).resolve().parent.parent

    snapshots: list[dict] = []
    for rev in revisions(repo, path):
        payload = load_rev(repo, rev, path)
        if payload:
            snapshots.append(payload)

    # 작업트리(= 이번 빌드가 쓰는 데이터)를 마지막 스냅샷으로.
    if current is None:
        file_path = repo / path
        if file_path.exists():
            current = json.loads(file_path.read_text(encoding="utf-8-sig"))
    if current and (not snapshots or _state(snapshots[-1]) != _state(current)):
        snapshots.append(current)

    timeline: list[dict] = []
    by_product: dict[int, list[dict]] = {}
    for old, new in zip(snapshots, snapshots[1:]):
        events = diff_state(_state(old), _state(new))
        if not events:
            continue
        asof = new.get("fetchedAt", "")
        timeline.append({
            "asof": asof,
            "prevAsof": old.get("fetchedAt", ""),
            "events": events,
        })
        for ev in events:
            by_product.setdefault(ev["idx"], []).append({**ev, "asof": asof})

    timeline.reverse()
    for events in by_product.values():
        events.reverse()
    return {"revisions": len(snapshots), "timeline": timeline, "byProduct": by_product}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    args = parser.parse_args(argv)

    hist = build(path=args.path)
    if args.json:
        print(json.dumps(hist, ensure_ascii=False, indent=2))
        return 0

    print(f"스냅샷 {hist['revisions']}개 / 변경이 있는 회차 {len(hist['timeline'])}개")
    if hist["revisions"] < 2:
        print("비교할 스냅샷이 부족하다. 얕은 클론(fetch-depth:1)이 아닌지 확인해라.",
              file=sys.stderr)
    for entry in hist["timeline"]:
        print(f"\n[{entry['asof']}] (직전 {entry['prevAsof']}) 변경 {len(entry['events'])}건")
        for ev in entry["events"][:20]:
            extra = (f"  {ev['from']} -> {ev['to']}" if "from" in ev else "")
            print(f"  {KIND_LABEL[ev['kind']]:<16} {ev['idx']:>5}  {ev['name']}{extra}")
        if len(entry["events"]) > 20:
            print(f"  ... 그 외 {len(entry['events']) - 20}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
