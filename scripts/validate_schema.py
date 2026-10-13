"""schema.org 공식 validator 로 배포된 페이지를 검사한다.

사용:
    py scripts/validate_schema.py                              # index + 상품 3건 표본
    py scripts/validate_schema.py --all                         # sitemap 의 전체 URL
    py scripts/validate_schema.py https://.../products/2597/    # 특정 URL

엔드포인트 (실측 2026-10-13, 비공개 API 라 변경될 수 있음):
    POST https://validator.schema.org/validate   body: url=<검사할 URL>
    응답은 XSSI 방어 접두사 ")]}'" 가 붙은 JSON 이라 잘라내고 파싱한다.

종료코드: 오류가 하나라도 있으면 1.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ENDPOINT = "https://validator.schema.org/validate"
XSSI_PREFIX = ")]}'"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "site.config.json"

if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12 이상이 필요하다.")


def validate(url: str, *, timeout: float = 60.0, retries: int = 5) -> dict:
    """한 URL 을 검사한다. 429(Too Many Requests) 는 지수 백오프로 재시도한다.

    실측 2026-10-13: 1초 간격으로 연속 요청하면 429 가 난다. Retry-After 헤더를
    주면 그 값을, 없으면 4초부터 2배씩 늘려 기다린다.
    """
    body = urllib.parse.urlencode({"url": url}).encode()
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(
            ENDPOINT,
            data=body,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
            if text.startswith(XSSI_PREFIX):
                text = text[len(XSSI_PREFIX):]
            return json.loads(text)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code != 429 or attempt == retries:
                raise
            wait = float(exc.headers.get("Retry-After") or 0) or 4.0 * (2 ** (attempt - 1))
            print(f"      429 - {wait:.0f}초 대기 후 재시도 ({attempt}/{retries - 1})", flush=True)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt == retries:
                raise
            time.sleep(2.0 * attempt)
    raise RuntimeError(f"{url} 검사 실패: {last}")


def summarise(payload: dict) -> dict:
    """validator 응답을 집계한다.

    판정은 응답 최상위의 totalNumErrors / totalNumWarnings 가 기준이다. 노드를
    직접 순회해 errors 를 긁으면 중첩 노드를 놓쳐 "오류 0" 으로 잘못 읽을 수 있다
    (실측 2026-10-13: 중첩 노드가 nodeProperties[].nodes 가 아닌 곳에 담겨
    provider/offers 가 빈 배열처럼 보였다).

    타입은 응답 전체를 재귀로 훑어 typeGroup 을 세므로 Organization·Offer·
    DigitalDocument 처럼 중첩된 것까지 집계된다.
    """
    types: Counter[str] = Counter()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            group = value.get("typeGroup")
            if group:
                types[group] += 1
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    groups = payload.get("tripleGroups") or []
    walk(groups)

    messages = [str(err) for err in (payload.get("errors") or [])]
    for group in groups:
        if group.get("numErrors"):
            messages.append(
                f"{group.get('type')}: numErrors={group['numErrors']} "
                f"(오류 노드 {group.get('numNodesWithError')}개)"
            )

    return {
        "rendered": payload.get("isRendered"),
        "types": types,
        "topLevel": [g.get("type") for g in groups],
        "numErrors": payload.get("totalNumErrors", 0),
        "numWarnings": payload.get("totalNumWarnings", 0),
        "errors": messages,
        "numObjects": payload.get("numObjects"),
    }


def sitemap_urls(base: str) -> list[str]:
    with urllib.request.urlopen(
        urllib.request.Request(f"{base}/sitemap.xml", headers={"User-Agent": USER_AGENT}),
        timeout=40,
    ) as response:
        root = ET.fromstring(response.read())
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [el.text for el in root.findall("s:url/s:loc", ns) if el.text]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="*", help="검사할 URL. 없으면 표본을 자동 선택")
    parser.add_argument("--all", action="store_true", help="sitemap.xml 의 모든 URL 검사")
    parser.add_argument("--sample", type=int, default=3, help="상품 페이지 표본 수 (기본 3)")
    parser.add_argument("--delay", type=float, default=4.0,
                        help="요청 간 대기 초 (기본 4.0). 1초로 줄이면 429 가 난다")
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)

    if args.urls:
        targets = args.urls
    else:
        base = json.loads(args.config.read_text(encoding="utf-8-sig"))["baseUrl"].rstrip("/")
        every = sitemap_urls(base)
        if args.all:
            targets = every
        else:
            products = [u for u in every if "/products/" in u]
            step = max(1, len(products) // args.sample)
            targets = [f"{base}/"] + products[::step][: args.sample]

    print(f"schema.org validator: {len(targets)}개 URL 검사\n")
    total_errors = 0
    total_warnings = 0
    failed: list[str] = []
    for i, url in enumerate(targets, start=1):
        try:
            result = summarise(validate(url))
        except Exception as exc:  # noqa: BLE001
            # 요청 실패는 스키마 오류가 아니다. 따로 센다.
            print(f"[{i}/{len(targets)}] {url}\n    요청 실패: {exc}")
            failed.append(url)
            continue
        kinds = ", ".join(f"{k}x{v}" if v > 1 else k for k, v in sorted(result["types"].items()))
        print(f"[{i}/{len(targets)}] {url}")
        print(f"    오류 {result['numErrors']}  경고 {result['numWarnings']}  "
              f"렌더링={result['rendered']}  최상위 아이템={result['numObjects']}")
        print(f"    최상위: {', '.join(t for t in result['topLevel'] if t)}")
        print(f"    전체 타입: {kinds}")
        for err in result["errors"][:10]:
            print(f"      ! {err}")
        total_errors += result["numErrors"]
        total_warnings += result["numWarnings"]
        if i < len(targets):
            time.sleep(args.delay)

    checked = len(targets) - len(failed)
    print(f"\n검사 성공 {checked}/{len(targets)} URL · 스키마 오류 {total_errors}건 · 경고 {total_warnings}건")
    if failed:
        print(f"요청 실패 {len(failed)}건 (스키마 오류 아님): {', '.join(failed[:5])}")
    return 1 if (total_errors or failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
