# 재개 지점 (2026-10-14 갱신)

이 레포가 소스 정본이다. 사이트: https://jwjo943.github.io/

목적: AI(ChatGPT·Claude·Perplexity 등)가 한화생명 보험상품 정보를 찾고 읽을 수 있게 한다.

## 0. 배포 구조

레포에는 **소스만** 있다. 생성물(`_site/`)은 커밋하지 않는다.

| 항목 | 값 |
|---|---|
| 배포 | `.github/workflows/refresh.yml` 이 `_site/` 를 만들어 GitHub Pages 로 올린다 |
| Pages 방식 | `workflow` (Actions 단독). 2026-10-14 에 `legacy` 에서 전환 |
| 실행 시점 | 매주 월 03:00 KST · `main` 푸시(`data/**` 만 바뀐 푸시 제외) · 수동 실행 |
| 워크플로가 커밋하는 것 | `data/products.json` 하나 (`[skip ci]`) |

확인 명령:

```
gh api repos/jwjo943/jwjo943.github.io/pages --jq .build_type    # workflow 여야 한다
```

`legacy` 로 되돌리면 안 된다. 레포 루트에 사이트 파일이 없어 **사이트 전체가 404** 가 된다
(2026-10-13 커밋 `87c4193` 에서 실제 발생).

## 1. (완료) Pages 를 Actions 단독 배포로 전환

2026-10-14 에 끝났다. 내용은 §0 으로 옮겼다. 번호는 다른 곳의 인용 때문에 남긴다.

## 2. 다른 PC 준비

필요: Python **3.12 이상**, git, gh

```
git clone https://github.com/jwjo943/jwjo943.github.io.git
cd jwjo943.github.io
python3 scripts/build_site.py --clean
```

마지막 줄 근처의 `HTML ...개` 와 `sitemap URL ...개` 가 같은 수인지 확인한다. 결과는 `_site/` 에 생긴다.

Windows 는 `python3` 대신 `py` 를 쓴다. 수집부터 다시 하려면 `build.bat`(Windows) 또는
`python3 scripts/fetch_products.py` 를 먼저 실행한다.

푸시하려면 `gh auth login` 으로 `jwjo943` 계정에 로그인해 둔다.

## 3. 현재 상태

| 항목 | 상태 |
|---|---|
| 사이트 | 첫 화면 1 + 구분 페이지 + 상품 페이지. 상품 수는 매주 수집으로 바뀐다 — `python3 scripts/build_site.py --clean` 출력의 `HTML` 줄을 본다 |
| 수록 범위 | **판매중 상품만.** 판매중지 1,430건(2026-10-14 수집)은 싣지 않기로 결정 |
| 구분 페이지 | `/categories/<판매채널코드>-<상품종류코드>/` (예: `/categories/sa-ga/` = 개인 > 보장성) |
| 소유 확인 | Google · Bing · 네이버 meta 태그, 전 페이지 (`site.config.json` 의 `verificationMetaTags`) |
| IndexNow | 배포 때마다 sitemap 전체를 자동 제출 (Bing·Yandex·Seznam·Naver) |
| AI 수집기 접근 | ClaudeBot·Claude-User·Claude-SearchBot·GPTBot·OAI-SearchBot·ChatGPT-User·PerplexityBot 이름으로 요청 시 200 (2026-10-14 실측) |

## 4. 미완 / 대기

| 항목 | 상태 |
|---|---|
| 보험료·보장금액·보장내역 | **미수록.** 공시실 조회 응답에 없고 공시문서 PDF 안에만 있다. AI 가 "무엇을 보장하나", "보험료가 얼마인가" 에 답하지 못하는 이유다. 사이트 목적을 가장 크게 좌우한다. PDF 서버가 자동 수집을 막고 있다 (§5-12) |
| 검색 색인 | 2026-10-14 `site:jwjo943.github.io` 검색 결과 0건. AI 검색은 검색 색인에 실린 페이지를 쓰므로 색인 전에는 노출되지 않는다 |
| Google sitemap | Search Console 에서 "성공", 발견된 페이지 117 (2026-10-14 확인). 발견은 색인과 다르다 — 색인 수는 Search Console "페이지" 메뉴의 "색인이 생성됨" |
| Bing Webmaster 등록 | 완료, sitemap 읽기 정상 (2026-10-14) |
| schema.org 검증 | 아래 §4-1 |

### 4-1. schema.org 검증

2026-10-14 에 상품·첫 화면 102페이지 중 **57페이지 통과(오류 0 · 경고 0), 45페이지 미검사.**
구분 15페이지도 미검사다.

`python3 scripts/validate_schema.py --all --delay 10` 은 **끝까지 돌지 못한다.** 실측:

| 실측 | 내용 |
|---|---|
| 10초 간격 | 57건 통과 뒤부터 계속 `429`(요청 한도 초과) |
| 30분 뒤 재시도 | 여전히 `429` |
| 약 2시간 뒤 | `https://www.google.com/sorry/...` 로 넘겨짐 (자동 요청으로 보고 막은 상태) |

한도가 언제 풀리는지는 미확인이다. 미검사 상품 번호:

```
2594 2327 2275 2293 2294 1431 2408 1420 1421 1339 1398 1334 2588 1394 2609
2611 2612 2602 2603 2604 2590 2591 2592 2579 2580 2581 2555 2477 2445 2444
2443 2308 2158 1865 1786 816 2550 2476 2449 2407 1484 1481 1482 1483 1465
```

특정 페이지만 검사:

```
python3 scripts/validate_schema.py https://jwjo943.github.io/products/2594/
```

## 5. 알려진 함정

1. **`getList.do` 무필터는 전체가 아니다** — 33건만 오고 이것은 첫 구분(개인/보장성)이다. 15개 구분을 전부 `getList2.do` 로 돌려야 전체가 나온다.
2. **`www.hanwhalife.com` 은 TLS legacy renegotiation 을 요구** — `ssl.OP_LEGACY_SERVER_CONNECT` 없이는 접속 불가.
3. **robots.txt 는 호스트 루트에서만 유효** — 그래서 프로젝트 사이트가 아닌 `<계정>.github.io` 유저 사이트여야 한다.
4. **GitHub Pages 는 커스텀 헤더 불가** — JSON-LD 파일을 `.jsonld` 가 아니라 `.json` 으로 쓴 이유.
5. **schema.org 에 `InsuranceProduct` 가 없다** — `FinancialProduct` 사용. `additionalProperty`·`inLanguage`·`dateModified` 는 FinancialProduct 에 **없는 속성**이므로 넣으면 안 된다.
6. **llms.txt 는 사실상 읽히지 않는다** — 넣어뒀지만 기대하지 말 것.
7. **Google 은 sitemap ping(폐지)과 IndexNow(미참여) 둘 다 경로가 없다** — robots.txt `Sitemap:` 줄과 Search Console 수동 제출만 남는다.
8. **PowerShell 5.1 은 BOM 없는 UTF-8 `.ps1` 을 CP949 로 파싱**한다. PowerShell 이 쓴 JSON 에는 BOM 이 붙는다 → 설정/데이터 JSON 은 `utf-8-sig` 로 읽는다.
9. **공시문서 PDF 는 안정적 GET URL 이 없다** — 다운로드가 POST 전용. 파일명만 싣고 출처는 `schText` 딥링크로 건다.
10. **robots.txt 에서 자기 이름의 묶음이 있는 수집기는 `User-agent: *` 묶음을 읽지 않는다** (RFC 9309 §2.2.1). 특정 경로를 막으려면 이름이 적힌 묶음마다 다시 적어야 한다.
11. **생명보험협회 공시실(`pub.insure.or.kr`)은 자동 수집 금지** — `robots.txt` 가 네이버·다음 외 전부 `Disallow:/` 다 (2026-10-14 확인).
12. **공시문서 PDF 서버(`file.hanwhalife.com`)는 자동 수집 금지** — `robots.txt` 가 `User-agent: *` / `Disallow: /` 다 (2026-10-14 확인). 받는 방법은 `https://file.hanwhalife.com/www/announce/goods/download_chk.asp` 에 `file_name=<FILE_NAME 값>` POST, 파일명은 **CP949** 로 인코딩 (UTF-8 이면 "파일이 존재하지 않아" HTML 이 온다). 사람이 직접 돌리는 `scripts/download_documents.py` 로만 받는다 — 빌드·워크플로에 넣지 않는다.
