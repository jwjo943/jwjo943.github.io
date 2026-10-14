"""data/products.json 의 판매중 공시문서 PDF 를 내려받는다.

사용:
    python3 scripts/download_documents.py                     # 전부, pdf/ 에 저장
    python3 scripts/download_documents.py --kind 상품요약서    # 종류 하나만
    python3 scripts/download_documents.py --idx 2617 2598     # 상품 몇 개만
    python3 scripts/download_documents.py --out ../pdf --delay 2

저장 위치: <out>/<상품번호>/<공시문서 파일명>. 파일명은 공시실 값을 그대로 쓴다
(`.pdf.pdf` 처럼 이상해 보여도 서버가 그 이름으로만 찾는다).

- 이미 받은 파일(`%PDF-` 로 시작)은 건너뛴다. 끊겨도 다시 돌리면 이어서 받는다.
- 응답이 PDF 가 아니면(오류 화면 HTML) 저장하지 않고 실패로 센다.
- 실패가 하나라도 있으면 종료코드 1.

주의: 파일 서버(file.hanwhalife.com)의 robots.txt 는 `User-agent: *` / `Disallow: /`
다 (2026-10-14 확인). 빌드·워크플로에 넣지 않고 사람이 직접 돌리는 용도로만 둔다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from hanwha_disclosure import (
    DOCUMENT_DOWNLOAD_ENDPOINT,
    DOCUMENT_FILENAME_ENCODING,
    USER_AGENT,
    _ssl_context,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "products.json"
DEFAULT_OUT = ROOT / "pdf"

#: 공시실 화면에서 다운로드 버튼을 누를 때의 Referer (상품목록 페이지).
REFERER = "https://www.hanwhalife.com/main/disclosure/goods/goodslist/DF_GDGL000_P10000.do"


def jobs(products: list[dict], kinds: set[str] | None, idxs: set[int] | None) -> list[tuple[int, str]]:
    """판매중 판매기간의 (상품번호, 파일명) 목록."""
    out = []
    for product in products:
        if idxs and product["idx"] not in idxs:
            continue
        for term in product["terms"]:
            if not term.get("onSale"):
                continue
            for doc in term["documents"]:
                if doc.get("fileName") and (not kinds or doc["kind"] in kinds):
                    out.append((product["idx"], doc["fileName"]))
    return out


def download(file_name: str, *, timeout: float, retries: int) -> bytes:
    body = "file_name=" + urllib.parse.quote(file_name, encoding=DOCUMENT_FILENAME_ENCODING)
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    context = _ssl_context()
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            DOCUMENT_DOWNLOAD_ENDPOINT, data=body.encode("ascii"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"요청 실패: {last}") from last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--kind", nargs="+", help="상품요약서 / 사업방법서 / 약관 / 주요내용요약서")
    parser.add_argument("--idx", nargs="+", type=int, help="상품번호")
    parser.add_argument("--delay", type=float, default=1.0, help="요청 간격(초). 기본 1")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    products = data["products"] if isinstance(data, dict) else data
    todo = jobs(products, set(args.kind or []), set(args.idx or []))
    if not todo:
        print("받을 문서가 없다 (--kind / --idx 값을 확인).", file=sys.stderr)
        return 1

    ok = skip = 0
    failures: list[tuple[int, str, str]] = []
    for n, (idx, name) in enumerate(todo, 1):
        dest = args.out / str(idx) / name
        if dest.is_file() and dest.read_bytes()[:5] == b"%PDF-":
            skip += 1
            continue
        try:
            blob = download(name, timeout=args.timeout, retries=args.retries)
        except RuntimeError as exc:
            failures.append((idx, name, str(exc)))
        else:
            if blob[:5] == b"%PDF-":
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
                ok += 1
            else:
                # 오류 화면은 CP949 HTML 의 alert 문구다.
                failures.append((idx, name, blob[:200].decode("cp949", "replace").strip()))
        print(f"[{n}/{len(todo)}] 받음 {ok} · 건너뜀 {skip} · 실패 {len(failures)}  {idx} {name}",
              file=sys.stderr, flush=True)
        time.sleep(args.delay)

    print(f"\n전체 {len(todo)} · 받음 {ok} · 건너뜀 {skip} · 실패 {len(failures)}  ->  {args.out}",
          file=sys.stderr)
    for idx, name, reason in failures:
        print(f"  실패 {idx} {name}: {reason}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
