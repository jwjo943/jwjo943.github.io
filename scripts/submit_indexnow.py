"""IndexNow 로 URL 변경을 검색엔진에 통보한다.

사용:
    py scripts/submit_indexnow.py --check      # 키 파일만 확인하고 제출 안 함
    py scripts/submit_indexnow.py              # sitemap.xml 의 전체 URL 제출
    py scripts/submit_indexnow.py https://.../products/2597/

IndexNow 는 계정·인증이 필요 없다. 키 파일 소유로 호스트를 증명한다.
api.indexnow.org 에 보내면 참여 검색엔진(Microsoft Bing, Yandex, Seznam, Naver 등)에
공유된다.

Google 은 IndexNow 에 참여하지 않는다. Google 의 sitemap ping 엔드포인트는
2023년 말 폐지되어 현재 404 이므로, Google 쪽은 robots.txt 의 Sitemap 지시자
(이미 넣어 둠)와 Search Console 수동 제출만 경로가 남아 있다.

공식 규격 (indexnow.org/documentation 확인 2026-10-13):
    POST https://api.indexnow.org/indexnow
    body: {"host","key","keyLocation","urlList"}  최대 10,000 URL
    200 OK / 202 키 검증 대기 / 400 형식 오류 / 403 키 무효
    422 URL 이 host 와 불일치 / 429 과다 요청
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS = 10_000
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "site.config.json"
USER_AGENT = "hanwha-disclosure-indexnow/1.0"

RESPONSE_MEANING = {
    200: "OK - 제출 성공",
    202: "Accepted - 키 검증 대기 중",
    400: "Bad request - 형식 오류",
    403: "Forbidden - 키가 무효하다 (키 파일이 루트에 없거나 내용이 다르다)",
    422: "Unprocessable Entity - URL 이 host 와 맞지 않는다",
    429: "Too Many Requests - 과다 요청으로 차단",
}

if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12 이상이 필요하다.")


def fetch(url: str, timeout: float = 30.0) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def sitemap_urls(base: str) -> list[str]:
    status, body = fetch(f"{base}/sitemap.xml")
    if status != 200:
        raise SystemExit(f"오류: {base}/sitemap.xml 이 {status} 를 반환했다.")
    root = ET.fromstring(body)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [el.text for el in root.findall("s:url/s:loc", ns) if el.text]


def check_key_file(base: str, key: str) -> bool:
    """키 파일이 루트에서 접근 가능하고 내용이 키와 같은지 확인한다.

    이게 통과하지 않으면 제출해도 403 이 난다.
    """
    url = f"{base}/{key}.txt"
    status, body = fetch(url)
    ok = status == 200 and body.strip() == key
    print(f"키 파일   {url}")
    print(f"          HTTP {status}" + ("" if status == 200 else "  <- 루트에서 서빙되지 않는다"))
    if status == 200:
        print(f"          내용 일치: {'예' if body.strip() == key else f'아니오 (받은 값 {body.strip()[:40]!r})'}")
    return ok


def submit(base: str, key: str, urls: list[str]) -> int:
    host = urllib.parse.urlparse(base).netloc
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{base}/{key}.txt",
        "urlList": urls,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, text = response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode("utf-8", errors="replace")

    print(f"POST {ENDPOINT}")
    print(f"     host={host}  urlList={len(urls)}개")
    print(f"     -> HTTP {status}  {RESPONSE_MEANING.get(status, '문서에 없는 코드')}")
    if text.strip():
        print(f"     body: {text.strip()[:300]}")
    return 0 if status in (200, 202) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="*", help="제출할 URL. 없으면 sitemap.xml 전체")
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--check", action="store_true", help="키 파일만 확인하고 제출하지 않는다")
    args = parser.parse_args(argv)

    cfg = json.loads(args.config.read_text(encoding="utf-8-sig"))
    base = cfg["baseUrl"].rstrip("/")
    key = (cfg.get("indexNowKey") or "").strip()
    if not key:
        print(f"오류: {args.config} 의 indexNowKey 가 비어 있다.", file=sys.stderr)
        return 1

    if not check_key_file(base, key):
        print("", file=sys.stderr)
        print("오류: 키 파일 확인 실패. 제출하면 403 이 난다.", file=sys.stderr)
        print("      빌드 후 커밋·푸시해서 키 파일이 라이브인지 먼저 확인해라.", file=sys.stderr)
        return 1
    print("키 파일 확인 통과\n")

    if args.check:
        return 0

    urls = args.urls or sitemap_urls(base)
    off_host = [u for u in urls if not u.startswith(base + "/") and u != base + "/"]
    if off_host:
        print(f"오류: host 와 맞지 않는 URL {len(off_host)}개 -> {off_host[:3]}", file=sys.stderr)
        return 1
    if len(urls) > MAX_URLS:
        print(f"오류: URL {len(urls)}개는 1회 한도({MAX_URLS})를 넘는다.", file=sys.stderr)
        return 1

    return submit(base, key, urls)


if __name__ == "__main__":
    raise SystemExit(main())
