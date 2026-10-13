"""갱신된 data/products.json 과 커밋된 직전 버전을 비교해 변경 내역을 찍는다.

사용:
    py scripts/diff_products.py                     # HEAD 와 작업트리 비교
    py scripts/diff_products.py --ref HEAD~1
    py scripts/diff_products.py --old a.json --new b.json

GitHub Actions 로그에서 "이번 갱신으로 무엇이 바뀌었는지"를 바로 보기 위한 것이다.
변경이 없으면 아무 것도 출력하지 않고 종료코드 0.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_PATH = "data/products.json"


def load_git(ref: str, path: str) -> dict | None:
    try:
        out = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True, check=True, encoding="utf-8",
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return json.loads(out)


def index(payload: dict) -> dict[int, str]:
    return {p["idx"]: p["name"] for p in payload["products"]}


def term_count(payload: dict) -> dict[int, int]:
    return {p["idx"]: len(p["terms"]) for p in payload["products"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="HEAD", help="비교 기준 git ref (기본 HEAD)")
    parser.add_argument("--path", default=DEFAULT_PATH, help=f"레포 내 경로 (기본 {DEFAULT_PATH})")
    parser.add_argument("--old", type=Path, help="git 대신 파일로 비교")
    parser.add_argument("--new", type=Path, help="git 대신 파일로 비교")
    args = parser.parse_args(argv)

    if args.old:
        old = json.loads(args.old.read_text(encoding="utf-8-sig"))
    else:
        old = load_git(args.ref, args.path)
    new_path = args.new or Path(args.path)
    if not new_path.exists():
        print(f"오류: {new_path} 가 없다.", file=sys.stderr)
        return 1
    new = json.loads(new_path.read_text(encoding="utf-8-sig"))

    if old is None:
        print(f"비교 대상 없음 ({args.ref}:{args.path}). 최초 수집으로 본다.")
        print(f"상품 {new['counts']['products']}건 / 판매기간 {new['counts']['salesTerms']}구간 "
              f"/ 공시문서 {new['counts']['documents']}건")
        return 0

    oc, nc = old["counts"], new["counts"]
    changed = False
    for label, key in (("상품", "products"), ("판매기간", "salesTerms"), ("공시문서", "documents")):
        if oc.get(key) != nc.get(key):
            changed = True
            delta = nc[key] - oc.get(key, 0)
            print(f"{label:<6} {oc.get(key)} -> {nc[key]}  ({delta:+d})")

    o, n = index(old), index(new)
    for idx in sorted(n.keys() - o.keys()):
        changed = True
        print(f"  + 신규   {idx}  {n[idx]}")
    for idx in sorted(o.keys() - n.keys()):
        changed = True
        print(f"  - 사라짐 {idx}  {o[idx]}")

    ot, nt = term_count(old), term_count(new)
    for idx in sorted(o.keys() & n.keys()):
        if ot[idx] != nt[idx]:
            changed = True
            print(f"  ~ 개정   {idx}  {n[idx]}  (판매기간 {ot[idx]} -> {nt[idx]})")

    if old.get("fetchedAt") != new.get("fetchedAt"):
        print(f"기준일   {old.get('fetchedAt')} -> {new.get('fetchedAt')}")

    if not changed:
        print("공시 내용 변경 없음 (기준일만 갱신)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
