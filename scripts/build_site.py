"""GitHub Pages 에 올릴 정적 사이트 생성기.

사용:
    py scripts/build_site.py                      # site.config.json 의 baseUrl 사용
    py scripts/build_site.py --base-url https://myaccount.github.io
    py scripts/build_site.py --out ../myaccount.github.io   # Pages 레포로 직접 빌드

출력(퍼블리시 루트):
    index.html                    상품 101건 색인
    products/<idx>/index.html     상품 1건
    data/catalog.json             전체 JSON-LD
    data/products/<idx>.json      상품별 JSON-LD
    robots.txt  sitemap.xml  llms.txt  .nojekyll

설계 제약 (실측·확인 2026-10-13):
  1. robots.txt 는 **호스트 루트에서만** 유효하다. 따라서 이 출력은
     <계정>.github.io 유저 사이트의 **레포 루트**에 그대로 올려야 한다.
     username.github.io/repo/robots.txt 는 어떤 크롤러도 가져가지 않는다.
  2. GitHub Pages 는 **커스텀 HTTP 헤더를 지원하지 않는다**. Content-Type 을
     지정할 수 없으므로 JSON-LD 파일 확장자를 .jsonld 가 아니라 .json 으로 쓴다
     (.jsonld 는 Pages 가 application/octet-stream 으로 줄 가능성이 있다 - 미검증).
     정본 JSON-LD 는 어차피 각 HTML 안에 <script type="application/ld+json"> 으로
     인라인되어 있으므로, 별도 파일은 보조 수단이다.
  3. AI 크롤러는 JS 를 실행하지 않는 것으로 전제한다. 생성물에 JS 는 0줄이다.
  4. llms.txt 는 넣되 기대하지 않는다. 2026년 실측상 거의 조회되지 않고 Google 은
     공식적으로 미사용을 밝혔다. 승부는 HTML 본문과 검색 인덱싱에서 난다.

사이트 소유 확인(Search Console / Bing Webmaster)은 두 경로를 지원한다.
--clean 으로 출력 디렉터리를 비워도 둘 다 매 빌드에 복원된다.

  A) meta 태그 - site.config.json 의 verificationMetaTags 에 name->content 로 적으면
     모든 페이지 <head> 에 들어간다. Google 은 `google-site-verification`,
     Bing 은 `msvalidate.01` 이다 (공식 문서 확인). Bing 은 전 페이지 삽입을 권장하고
     Google 은 홈페이지만 필요하므로 전 페이지에 넣어 둘 다 만족시킨다.

  B) 파일 - 프로젝트의 static/ 에 넣은 것은 퍼블리시 루트로 그대로 복사된다.
     Google 은 확인 파일의 "파일명과 내용을 수정하지 말라"고 명시하므로 내용을
     생성기가 만들지 않는다. 받은 파일을 static/ 에 그대로 두면 된다.
     CNAME, ads.txt 같은 것도 같은 경로로 처리한다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from build_jsonld import (
    SCHEMA,
    dataset_node,
    organization_node,
    product_node,
)
from hanwha_disclosure import LIST_PAGE

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "products.json"
DEFAULT_CONFIG = ROOT / "site.config.json"
DEFAULT_OUT = ROOT / "site"

PLACEHOLDER = "CHANGE-ME"

#: robots.txt 에 명시할 크롤러 토큰. 전부 각 벤더 공식 문서에서 확인한 값이다.
#: support.claude.com / developers.openai.com (2026-10-13)
AI_CRAWLERS: list[tuple[str, str]] = [
    ("Claude-SearchBot", "Claude 웹 검색 인덱싱 - 검색 노출의 핵심"),
    ("Claude-User", "사용자가 Claude 에게 이 페이지를 읽게 할 때"),
    ("ClaudeBot", "모델 학습용 수집"),
    ("OAI-SearchBot", "ChatGPT 검색 노출 - 검색 노출의 핵심"),
    ("ChatGPT-User", "ChatGPT 사용자 액션 (robots.txt 가 적용되지 않을 수 있음)"),
    ("GPTBot", "OpenAI 모델 학습용 수집"),
    ("PerplexityBot", "Perplexity 인덱싱 (토큰은 미검증)"),
    ("Google-Extended", "Gemini 학습 여부 제어 (검색 순위와 무관)"),
]

#: 학습용 수집 봇. allowTrainingCrawlers=false 면 이 토큰만 Disallow 로 뒤집는다.
TRAINING_CRAWLERS = {"ClaudeBot", "GPTBot", "Google-Extended"}

#: 레포 루트에 함께 올라가는 빌드 소스. 콘텐츠가 아니므로 robots.txt 에서 뺀다.
#: (Pages 가 legacy 모드로 레포 루트를 서빙할 때만 실제로 노출된다.)
SOURCE_PATHS = (
    "scripts/",
    "static/",
    "data/products.json",
    "site.config.json",
    "build.bat",
    "NEXT.md",
)

DISCLAIMER = (
    "이 사이트는 한화생명보험 주식회사가 운영하는 공식 사이트가 아니며, "
    "한화생명 상품공시실에 공시된 내용을 색인한 비공식 자료다. "
    "보험료·보장금액·해지환급률 등 구체적 수치는 담고 있지 않다. "
    "가입 판단은 반드시 공시실의 약관·상품요약서 원문과 "
    "한화생명 공식 안내를 확인한 뒤에 하라."
)

STYLE = """\
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;padding:1.5rem;font:16px/1.65 system-ui,"Segoe UI","Malgun Gothic",sans-serif;
max-width:62rem;margin-inline:auto}
h1{font-size:1.6rem;line-height:1.3;margin:0 0 .4rem}
h2{font-size:1.2rem;margin:2rem 0 .6rem;padding-bottom:.3rem;border-bottom:1px solid #8884}
h3{font-size:1rem;margin:1.4rem 0 .4rem}
a{color:#06c}
.meta{font-size:.9rem;opacity:.8;margin:0 0 1.2rem}
.disclaimer{font-size:.85rem;border:1px solid #8884;border-radius:.4rem;padding:.8rem 1rem;margin:1.5rem 0}
table{border-collapse:collapse;width:100%;font-size:.92rem}
caption{text-align:left;font-weight:600;padding:.4rem 0}
th,td{border:1px solid #8884;padding:.4rem .6rem;text-align:left;vertical-align:top}
th{background:#8881;white-space:nowrap}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.35rem 1rem;margin:0 0 1.2rem}
dt{font-weight:600;white-space:nowrap}
dd{margin:0}
ul.products{list-style:none;padding:0;margin:0}
ul.products li{padding:.25rem 0;border-bottom:1px solid #8882}
.wrap{overflow-x:auto}
nav.bc{font-size:.85rem;margin:0 0 1rem}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid #8884;font-size:.85rem;opacity:.8}
"""


def e(value: Any) -> str:
    """HTML 이스케이프."""
    return html.escape(str(value), quote=True)


def ld(payload: Any) -> str:
    """JSON-LD 를 <script> 에 안전하게 인라인한다.

    </script> 와 HTML 주석 시작 토큰만 유니코드 이스케이프로 치환한다.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return text.replace("</", "<\\/").replace("<!--", "\\u003C!--")


def verification_meta(cfg: dict) -> str:
    """사이트 소유 확인용 meta 태그. 값은 config 에서 그대로 가져온다.

    name 과 content 를 해석하거나 가공하지 않는다 - 각 제공자가 발급한 문자열을
    변형 없이 심어야 확인이 통과한다.
    """
    tags = cfg.get("verificationMetaTags") or {}
    return "".join(
        f'\n<meta name="{e(name)}" content="{e(content)}">'
        for name, content in tags.items()
        if str(content).strip()
    )


def page(
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    jsonld: Any,
    alternate: str,
    extra_head: str = "",
) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">{extra_head}
<link rel="canonical" href="{e(canonical)}">
<link rel="alternate" type="application/ld+json" href="{e(alternate)}">
<style>{STYLE}</style>
<script type="application/ld+json">
{ld(jsonld)}
</script>
</head>
<body>
{body}
<footer>
<p class="disclaimer">{e(DISCLAIMER)}</p>
<p>원본 출처: <a href="{e(LIST_PAGE)}">한화생명 상품공시실 &gt; 상품목록</a></p>
</footer>
</body>
</html>
"""


# -- 색인 페이지 ------------------------------------------------------------


def build_index(data: dict, cfg: dict, org: dict, dataset: dict, nodes: dict[int, dict]) -> str:
    base = cfg["baseUrl"]
    counts = data["counts"]
    groups: dict[str, list[dict]] = {}
    for product in data["products"]:
        groups.setdefault(product["category"]["label"], []).append(product)

    rows = "\n".join(
        f"<tr><td>{e(label)}</td><td>{len(items)}</td></tr>"
        for label, items in groups.items()
    )
    sections = []
    for label, items in groups.items():
        lis = "\n".join(
            f'<li><a href="{e(base)}/products/{p["idx"]}/">{e(p["name"])}</a></li>'
            for p in sorted(items, key=lambda x: x["name"])
        )
        sections.append(
            f"<h3>{e(label)} <small>({len(items)}건)</small></h3>\n"
            f'<ul class="products">\n{lis}\n</ul>'
        )

    item_list = {
        "@type": "ItemList",
        "@id": f"{base}/#catalog",
        "name": cfg["siteName"],
        "numberOfItems": len(data["products"]),
        "itemListOrder": f"{SCHEMA}/ItemListUnordered",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i,
                "url": f"{base}/products/{p['idx']}/",
                "name": p["name"],
            }
            for i, p in enumerate(data["products"], start=1)
        ],
    }

    body = f"""<main>
<h1>{e(cfg['siteName'])}</h1>
<p class="meta">기준일 {e(data['fetchedAt'])} · 공시 주체 {e(data['source']['publisherLegalName'])}
(사업자등록번호 {e(data['source']['publisherBusinessNumber'])}) ·
상품 {counts['products']}건 · 판매기간 {counts['salesTerms']}구간 · 공시문서 {counts['documents']}건</p>

<p>{e(cfg['siteDescription'])}</p>

<h2>담고 있는 항목 / 담고 있지 않은 항목</h2>
<div class="wrap">
<table>
<caption>수록 범위</caption>
<thead><tr><th>항목</th><th>수록</th><th>비고</th></tr></thead>
<tbody>
<tr><td>상품명</td><td>O</td><td>공시실 상품목록 기준</td></tr>
<tr><td>판매채널 / 상품종류</td><td>O</td><td>개인·단체·방카슈랑스·다이렉트·특약 × 보장성·연금·생사혼합·퇴직·장애인전용</td></tr>
<tr><td>판매기간 이력</td><td>O</td><td>개정 구간별 시작일·종료일</td></tr>
<tr><td>공시문서 목록</td><td>O</td><td>약관·상품요약서·사업방법서·주요내용요약서 파일명</td></tr>
<tr><td>보험료 / 보장금액 / 해지환급률</td><td>X</td><td>공시실 목록 API 에 없음. 상품요약서 PDF 내부에만 존재</td></tr>
<tr><td>가입연령 / 납입기간 / 보장내역</td><td>X</td><td>동일 (PDF 내부)</td></tr>
<tr><td>공시문서 PDF 직접 링크</td><td>X</td><td>공시실 다운로드가 POST 전용이라 안정적 URL 없음</td></tr>
</tbody>
</table>
</div>

<h2>구분별 상품 수</h2>
<div class="wrap">
<table>
<caption>판매채널 × 상품종류</caption>
<thead><tr><th>구분</th><th>상품 수</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>

<h2>상품 목록</h2>
{"".join(f"{s}\n" for s in sections)}
</main>"""

    return page(
        title=cfg["siteName"],
        description=cfg["siteDescription"],
        canonical=f"{base}/",
        alternate=f"{base}/data/catalog.json",
        jsonld={"@context": SCHEMA, "@graph": [org, dataset, item_list]},
        body=body,
        extra_head=verification_meta(cfg),
    )


# -- 상품 페이지 ------------------------------------------------------------


def build_product(product: dict, cfg: dict, data: dict, org: dict, node: dict) -> str:
    base = cfg["baseUrl"]
    url = f"{base}/products/{product['idx']}/"
    category = product["category"]
    structure = "특약" if product["isRider"] else "주보험"
    current = next((t for t in product["terms"] if t["onSale"]), None)
    status = f"판매중 (판매개시 {current['sellStart']})" if current else "판매중지"

    term_rows = []
    for term in product["terms"]:
        docs = ", ".join(e(d["kind"]) for d in term["documents"]) or "-"
        files = "<br>".join(f"<code>{e(d['fileName'])}</code>" for d in term["documents"]) or "-"
        term_rows.append(
            f"<tr><td>{e(term['sellStart'])}</td>"
            f"<td>{e(term['sellEnd']) if term['sellEnd'] else '판매중'}</td>"
            f"<td>{docs}</td><td>{files}</td></tr>"
        )

    breadcrumb = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": cfg["siteName"], "item": f"{base}/"},
            {"@type": "ListItem", "position": 2, "name": category["label"], "item": f"{base}/"},
            {"@type": "ListItem", "position": 3, "name": product["name"], "item": url},
        ],
    }

    description = (
        f"{product['name']} — {category['label']} {structure}. "
        f"{status}. 판매기간 {len(product['terms'])}구간, "
        f"공시문서 {sum(len(t['documents']) for t in product['terms'])}건. "
        f"기준일 {data['fetchedAt']}."
    )

    body = f"""<nav class="bc"><a href="{e(base)}/">{e(cfg['siteName'])}</a> &gt; {e(category['label'])}</nav>
<main>
<article>
<h1>{e(product['name'])}</h1>
<p class="meta">기준일 {e(data['fetchedAt'])} · 공시 주체 {e(data['source']['publisherLegalName'])}</p>

<dl>
<dt>판매채널</dt><dd>{e(category['sellTypeName'])}</dd>
<dt>상품종류</dt><dd>{e(category['goodsTypeName'])}</dd>
<dt>상품구성</dt><dd>{e(structure)}{' (주계약에 부가하여 가입하며 단독 가입 대상이 아니다)' if product['isRider'] else ''}</dd>
<dt>판매상태</dt><dd>{e(status)}</dd>
<dt>판매기간 구간</dt><dd>{len(product['terms'])}</dd>
<dt>공시실 상품번호</dt><dd>{product['idx']}</dd>
</dl>

<h2>판매기간 및 공시문서</h2>
<div class="wrap">
<table>
<caption>{e(product['name'])} 개정 이력</caption>
<thead><tr><th>판매시작</th><th>판매종료</th><th>공시문서</th><th>파일명</th></tr></thead>
<tbody>
{chr(10).join(term_rows)}
</tbody>
</table>
</div>
<p class="meta">공시문서 PDF 는 한화생명 공시실에서 내려받는다. 공시실 다운로드가 POST 전용이라
이 사이트는 직접 링크를 제공하지 않고 파일명만 싣는다.</p>

<h2>원문 확인</h2>
<p><a href="{e(product['sourceUrl'])}">한화생명 상품공시실에서 이 상품 보기</a></p>
</article>
</main>"""

    return page(
        title=f"{product['name']} · {cfg['siteName']}",
        description=description,
        canonical=url,
        alternate=f"{base}/data/products/{product['idx']}.json",
        jsonld={"@context": SCHEMA, "@graph": [org, node, breadcrumb]},
        body=body,
        extra_head=verification_meta(cfg),
    )


# -- 설정 파일들 ------------------------------------------------------------


def copy_static(static_dir: Path, out: Path) -> list[tuple[str, int]]:
    """static/ 의 모든 파일을 퍼블리시 루트로 그대로 복사한다.

    소유 확인 파일(google<토큰>.html, BingSiteAuth.xml), CNAME, ads.txt 처럼
    내용을 생성기가 만들면 안 되는 것들을 --clean 재빌드에도 살려두는 경로다.
    생성물과 같은 경로면 static 쪽이 이긴다 (덮어쓴다).
    """
    if not static_dir.is_dir():
        return []
    copied: list[tuple[str, int]] = []
    for src in sorted(static_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(static_dir)
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append((rel.as_posix(), src.stat().st_size))
    return copied


def build_robots(cfg: dict) -> str:
    allow_training = cfg.get("allowTrainingCrawlers", True)
    lines = [
        "# robots.txt - 호스트 루트에서만 유효하다.",
        "# 이 파일은 반드시 <계정>.github.io 레포 루트에 있어야 한다.",
        "# username.github.io/<repo>/robots.txt 는 크롤러가 가져가지 않는다.",
        "",
        "User-agent: *",
        "Allow: /",
        "",
        "# 생성물을 레포 루트에 함께 커밋하므로 빌드 소스도 같은 호스트에서 서빙된다.",
        "# 콘텐츠가 아니니 크롤 대상에서 뺀다. (같은 User-agent 그룹 안에 둔다 -",
        "#  그룹을 쪼개면 어느 규칙이 적용되는지 모호해진다.)",
        *(f"Disallow: /{path}" for path in SOURCE_PATHS),
        "",
    ]
    for token, note in AI_CRAWLERS:
        blocked = token in TRAINING_CRAWLERS and not allow_training
        lines += [
            f"# {note}",
            f"User-agent: {token}",
            f"{'Disallow' if blocked else 'Allow'}: /",
            "",
        ]
    lines += [
        "# 공시 원문(PDF)은 한화생명 서버에 있으므로 이 사이트에 크롤 대상이 없다.",
        "",
        f"Sitemap: {cfg['baseUrl']}/sitemap.xml",
        "",
    ]
    return "\n".join(lines)


def build_sitemap(data: dict, cfg: dict) -> str:
    base = cfg["baseUrl"]
    lastmod = data["fetchedAt"]
    urls = [f"{base}/"] + [f"{base}/products/{p['idx']}/" for p in data["products"]]
    entries = "\n".join(
        "  <url>\n"
        f"    <loc>{xml_escape(u)}</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n"
        f"    <changefreq>weekly</changefreq>\n"
        f"    <priority>{'1.0' if u == base + '/' else '0.7'}</priority>\n"
        "  </url>"
        for u in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


def build_llms_txt(data: dict, cfg: dict) -> str:
    base = cfg["baseUrl"]
    counts = data["counts"]
    groups: dict[str, list[dict]] = {}
    for product in data["products"]:
        groups.setdefault(product["category"]["label"], []).append(product)

    out = [
        f"# {cfg['siteName']}",
        "",
        f"> {cfg['siteDescription']}",
        "",
        f"- 기준일: {data['fetchedAt']}",
        f"- 공시 주체: {data['source']['publisherLegalName']} (사업자등록번호 {data['source']['publisherBusinessNumber']})",
        f"- 수록: 상품 {counts['products']}건 / 판매기간 {counts['salesTerms']}구간 / 공시문서 {counts['documents']}건",
        f"- 원본 출처: {LIST_PAGE}",
        "",
        "## 주의",
        "",
        DISCLAIMER,
        "",
        "보험료·보장금액·해지환급률·가입연령·보장내역은 이 사이트에 **없다**. "
        "공시실의 상품요약서 PDF 안에만 있다.",
        "",
        "## 기계 판독용",
        "",
        f"- [전체 JSON-LD 카탈로그]({base}/data/catalog.json): schema.org FinancialProduct {counts['products']}건",
        f"- [sitemap.xml]({base}/sitemap.xml)",
        "",
    ]
    for label, items in groups.items():
        out += [f"## {label} ({len(items)}건)", ""]
        for product in sorted(items, key=lambda x: x["name"]):
            current = next((t for t in product["terms"] if t["onSale"]), None)
            state = f"판매중 {current['sellStart']}~" if current else "판매중지"
            out.append(
                f"- [{product['name']}]({base}/products/{product['idx']}/): {state}, "
                f"판매기간 {len(product['terms'])}구간"
            )
        out.append("")
    return "\n".join(out)


# -- 진입점 ----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT,
                        help=f"퍼블리시 루트 (기본 {DEFAULT_OUT})")
    parser.add_argument("--base-url", default=None, help="site.config.json 의 baseUrl 을 덮어쓴다")
    parser.add_argument("--allow-placeholder", action="store_true",
                        help=f"baseUrl 에 {PLACEHOLDER} 가 남아 있어도 빌드한다 (시험용)")
    parser.add_argument("--clean", action="store_true", help="출력 디렉터리를 먼저 비운다")
    parser.add_argument("--static-dir", type=Path, default=None,
                        help=f"퍼블리시 루트로 그대로 복사할 디렉터리 (기본 {ROOT / 'static'}). "
                             "소유 확인 파일·CNAME 등을 여기 두면 --clean 에도 살아남는다.")
    args = parser.parse_args(argv)

    for path, hint in ((args.input, "py scripts/fetch_products.py"), (args.config, None)):
        if not path.exists():
            print(f"오류: 파일이 없다 -> {path}", file=sys.stderr)
            if hint:
                print(f"      먼저 `{hint}` 를 실행해라.", file=sys.stderr)
            return 1

    # utf-8-sig: 메모장이나 PowerShell Set-Content 로 config 를 고치면 BOM 이 붙는데
    # 순수 utf-8 로 읽으면 "Unexpected UTF-8 BOM" 으로 죽는다. BOM 유무 둘 다 받는다.
    cfg = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if args.base_url:
        cfg["baseUrl"] = args.base_url
    cfg["baseUrl"] = cfg["baseUrl"].rstrip("/")

    if PLACEHOLDER in cfg["baseUrl"] and not args.allow_placeholder:
        print(f"오류: baseUrl 이 아직 '{cfg['baseUrl']}' 다.", file=sys.stderr)
        print(f"      {args.config} 의 baseUrl 을 https://<GitHub계정>.github.io 로 바꾸거나",
              file=sys.stderr)
        print("      --base-url 로 넘겨라. canonical·sitemap·JSON-LD @id 가 전부 이 값을 쓴다.",
              file=sys.stderr)
        return 1

    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if not data.get("products"):
        print(f"오류: {args.input} 에 products 가 비어 있다.", file=sys.stderr)
        return 1

    out: Path = args.out
    if args.clean and out.exists():
        # --clean 은 출력 디렉터리를 통째로 지운다. 소스나 레포가 같은 곳에 있으면
        # 그것까지 날아가므로 거부한다. 출력은 항상 전용 디렉터리여야 한다.
        guarded = [name for name in (".git", ".github", "scripts", "site.config.json")
                   if (out / name).exists()]
        if guarded:
            print(f"오류: --clean 이 {out} 를 지우려 했으나 소스/레포가 들어 있다.", file=sys.stderr)
            print(f"      발견: {', '.join(guarded)}", file=sys.stderr)
            print("      출력은 전용 디렉터리로 지정해라 (예: --out _site).", file=sys.stderr)
            return 1
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    org = organization_node(data["source"], cfg["baseUrl"])
    dataset = dataset_node(data, cfg["baseUrl"], org["@id"])
    org_id = org["@id"]

    written: list[tuple[str, int]] = []

    def write(rel: str, text: str) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append((rel, len(text.encode("utf-8"))))

    # 상품별 JSON-LD 노드 (판매기간 전체 이력)
    nodes = {
        p["idx"]: product_node(p, base_url=cfg["baseUrl"], org_id=org_id, terms=p["terms"])
        for p in data["products"]
    }
    # 카탈로그에는 현재(또는 최신) 구간만 - 과거 이력까지 넣으면 수 MB 가 된다
    catalog_nodes = [
        product_node(
            p,
            base_url=cfg["baseUrl"],
            org_id=org_id,
            terms=[t for t in p["terms"] if t["onSale"]] or p["terms"][:1],
        )
        for p in data["products"]
    ]

    write("index.html", build_index(data, cfg, org, dataset, nodes))
    for product in data["products"]:
        write(
            f"products/{product['idx']}/index.html",
            build_product(product, cfg, data, org, nodes[product["idx"]]),
        )

    catalog = {
        "@context": SCHEMA,
        "@graph": [
            org,
            dataset,
            {
                "@type": "ItemList",
                "@id": f"{cfg['baseUrl']}/#catalog",
                "name": cfg["siteName"],
                "numberOfItems": len(catalog_nodes),
                "itemListOrder": f"{SCHEMA}/ItemListUnordered",
                "itemListElement": [
                    {"@type": "ListItem", "position": i, "item": {"@id": n["@id"]}}
                    for i, n in enumerate(catalog_nodes, start=1)
                ],
            },
            *catalog_nodes,
        ],
    }
    write("data/catalog.json", json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    for idx, node in nodes.items():
        write(
            f"data/products/{idx}.json",
            json.dumps({"@context": SCHEMA, "@graph": [org, dataset, node]},
                       ensure_ascii=False, indent=2) + "\n",
        )

    write("robots.txt", build_robots(cfg))
    write("sitemap.xml", build_sitemap(data, cfg))
    write("llms.txt", build_llms_txt(data, cfg))
    write(".nojekyll", "")
    # 생성물은 전부 LF 로 쓴다. Windows 의 core.autocrlf=true 가 체크아웃 때
    # CRLF 로 바꾸면서 git 이 파일마다 경고를 뿜으므로 명시적으로 고정한다.
    write(".gitattributes", "* -text\n")

    # IndexNow 키 파일. 내용은 키 문자열 그 자체여야 하고 루트에 있어야 한다
    # (공식 문서: "host {your-key}.txt at the root directory containing the key").
    index_now_key = (cfg.get("indexNowKey") or "").strip()
    if index_now_key:
        if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", index_now_key):
            print(f"오류: indexNowKey 가 규격 위반이다 -> {index_now_key!r}", file=sys.stderr)
            print("      8~128자, 영문 대소문자/숫자/하이픈만 허용된다.", file=sys.stderr)
            return 1
        write(f"{index_now_key}.txt", index_now_key)

    static_dir = args.static_dir or (ROOT / cfg.get("staticDir", "static"))
    generated = {rel for rel, _ in written}
    copied = copy_static(static_dir, out)

    total = sum(size for _, size in written) + sum(size for _, size in copied)
    html_files = sum(1 for rel, _ in written if rel.endswith(".html"))
    json_files = sum(1 for rel, _ in written if rel.endswith(".json"))
    meta_tags = cfg.get("verificationMetaTags") or {}
    active_tags = [n for n, v in meta_tags.items() if str(v).strip()]

    print(f"출력 루트          {out}")
    print(f"HTML               {html_files}개 (index 1 + 상품 {html_files - 1})")
    print(f"JSON-LD 파일       {json_files}개 (catalog 1 + 상품 {json_files - 1})")
    print(f"설정 파일          robots.txt / sitemap.xml / llms.txt / .nojekyll / .gitattributes")
    print(f"sitemap URL        {len(data['products']) + 1}개")
    if active_tags:
        print(f"소유확인 meta      {len(active_tags)}개 x 전 {html_files}페이지: {', '.join(active_tags)}")
    else:
        print(f"소유확인 meta      없음 (site.config.json 의 verificationMetaTags 에 추가하면 전 페이지에 삽입된다)")
    if copied:
        print(f"static 복사        {len(copied)}개 <- {static_dir}")
        for rel, size in copied:
            flag = "  [생성물 덮어씀]" if rel in generated else ""
            print(f"                   {rel}  {size:,} bytes{flag}")
    else:
        print(f"static 복사        없음 ({static_dir} 가 비었거나 존재하지 않음)")
    print(f"총 용량            {total:,} bytes")
    print(f"baseUrl            {cfg['baseUrl']}")
    if PLACEHOLDER in cfg["baseUrl"]:
        hits = sum(text.count(PLACEHOLDER)
                   for text in (p.read_text(encoding="utf-8")
                                for p in out.rglob("*") if p.is_file() and p.stat().st_size))
        print(f"경고               산출물에 {PLACEHOLDER} 가 {hits}곳 남아 있다. "
              f"실제 배포 전에 baseUrl 을 고쳐 다시 빌드해라.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
