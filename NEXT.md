# 재개 지점 (2026-10-13 중단)

이 레포가 소스 정본이다. 사이트: https://jwjo943.github.io/

## 0. 2026-10-13 마지막에 일어난 일 (해결됨)

Pages 가 legacy 모드라 **모든 푸시에 legacy 빌드가 돈다.** 소스/산출물을 분리하면서 레포 루트를
비웠더니 legacy 가 Actions 배포보다 나중에 끝나는 푸시에서 **사이트 전체가 404** 가 됐다
(커밋 `87c4193`, `cb743c3`). `/NEXT.md` 와 `/scripts/build_site.py` 만 200 이었다.

복구 수단이 둘 다 막혀 있었다 — 레포 소유자의 gh OAuth 토큰으로는:

| 시도 | 결과 |
|---|---|
| `PUT /repos/{o}/{r}/pages` (build_type 전환) | **404** |
| `gh workflow run refresh.yml` (푸시 없이 재배포) | **403 Must have admin rights** |

그래서 **생성물을 레포 루트에도 커밋**하는 방식으로 전환했다. legacy 와 Actions 어느 쪽이
이겨도 올바른 사이트가 나오므로 경쟁이 무해해진다. 지금은 푸시해도 안전하다.

- 워크플로가 `_site` 로 빌드한 뒤 `rsync -a --delete` 로 루트에 반영한다 (소스는 제외).
  `--delete` 때문에 사라진 상품의 낡은 페이지도 정리된다.
- 봇 커밋 메시지에 `[skip ci]` 를 붙여 자기 재트리거(무한 루프)를 막는다. legacy 빌드는
  Actions 가 아니라서 그대로 돌아 사이트를 올린다.
- 루트에 올라간 빌드 소스는 `robots.txt` 의 `Disallow` 로 크롤에서 뺀다.

## 1. 선택 — 원래 구조(소스/산출물 분리)로 되돌리려면

지금은 생성물이 레포 루트에 함께 커밋되어 있어 안전하다. 대가는 두 가지다 — 빌드 소스가
공개 URL 로 서빙되고(크롤은 `robots.txt` `Disallow` 로 차단), 갱신마다 200여 파일이 커밋된다.
깔끔하게 하려면 Pages 를 Actions 단독 배포로 바꾸면 된다. **급하지 않다.**

`build_type` 이 `legacy` 로 남아 있고 `actions/configure-pages@v5` 의 `enablement: true` 로는
전환되지 않았다.

### 조치 (사람이 해야 함 — CLI 로는 404/403)

```
https://github.com/jwjo943/jwjo943.github.io/settings/pages
→ Build and deployment → Source → "GitHub Actions"
```

`PUT /repos/{owner}/{repo}/pages` 는 gh OAuth 토큰으로 404 다. CLI 로는 안 된다.

전환 후 확인:

```
gh api repos/jwjo943/jwjo943.github.io/pages --jq .build_type        # workflow 여야 함
gh api repos/jwjo943/jwjo943.github.io/pages/builds?per_page=1       # 새 legacy 빌드가 없어야 함
gh workflow run refresh.yml                                          # 재배포
curl -sI https://jwjo943.github.io/ | head -1                        # 200 확인
```

전환했다면 워크플로의 `Sync built site into repo root` 단계와 루트의 생성물을 지워도 된다
(그때는 `robots.txt` 의 `Disallow` 와 `SOURCE_PATHS` 도 같이 정리).
`build_site.py --clean` 은 `.git`/`.github`/`scripts`/`site.config.json` 이 있는 디렉터리를
지우려 하면 거부하므로, 루트에 직접 `--clean` 을 걸 수는 없다.

## 2. 다른 PC 준비

필요: Python **3.12 이상**, git, gh (`winget install --id GitHub.cli`)

```
git clone https://github.com/jwjo943/jwjo943.github.io.git
cd jwjo943.github.io
py scripts/build_site.py --clean --out _site
```

`_site/` 와 `data/jsonld/` 는 `.gitignore` 대상이다. 수집부터 다시 하려면 `build.bat`
(Windows) 또는 `py scripts/fetch_products.py` 먼저.

## 3. 현재 상태

| 항목 | 상태 |
|---|---|
| 사이트 배포 | 완료 — 102페이지 (index 1 + 상품 101) |
| 수집 데이터 | 상품 101건(주보험 70 + 특약 31) / 판매기간 793구간 / 공시문서 2,141건 · 기준일 2026-10-13 |
| Google 소유 확인 | 완료 (meta 태그, 전 102페이지) |
| IndexNow | 완료 — 102 URL, HTTP 200 (Bing·Yandex·Seznam·Naver) |
| 자동 갱신 | 동작 확인 — 월 03:00 KST + 소스 변경 + 수동 dispatch |

## 4. 미완 / 대기

| 항목 | 상태 |
|---|---|
| Google sitemap 가져오기 | "가져올 수 없음". 사이트·sitemap 은 실측 정상(200, 형식 위반 0) → Google 측 타이밍. URL 검사 실시간 테스트 결과 미확인 |
| schema.org validator 전수(102) | **429 로 차단**(탐색 호출로 IP 한도 소진). 표본 5건 오류 0·경고 0. 재시도: `py scripts/validate_schema.py --all --delay 10` |
| Bing Webmaster 등록 | 미착수. Search Console 인증 끝났으니 "Import from Google Search Console" 로 가능 |
| 판매중지 상품 | `--sell-flag N` 구현됐으나 미실행. 건수 미확인 |
| 카테고리 페이지 15개 | 미착수. 현재 분류는 index 섹션뿐 |
| 보험료·보장금액·보장내역 | **미수록.** 공시실 JSON 에 없고 2,141건 PDF 안에만 있다. PDF 파싱은 별개 프로젝트 규모. 사이트 가치를 가장 크게 좌우하는 항목 |

## 5. 알려진 함정

코드 주석에 근거를 남겼지만 요약하면:

1. **`getList.do` 무필터는 전체가 아니다** — 33건만 오고 이건 첫 구분(개인/보장성)이다. 15개 구분을 전부 `getList2.do` 로 돌려야 101건이 나온다.
2. **`www.hanwhalife.com` 은 TLS legacy renegotiation 을 요구** — `ssl.OP_LEGACY_SERVER_CONNECT` 없이는 접속 불가. Windows·ubuntu 양쪽 동일.
3. **robots.txt 는 호스트 루트에서만 유효** — 그래서 프로젝트 사이트가 아닌 `<계정>.github.io` 유저 사이트여야 한다.
4. **GitHub Pages 는 커스텀 헤더 불가** — JSON-LD 파일을 `.jsonld` 가 아니라 `.json` 으로 쓴 이유. 배포 후 실측 `application/json; charset=utf-8`.
5. **schema.org 에 `InsuranceProduct` 가 없다** — `FinancialProduct` 사용. 그리고 `additionalProperty`·`inLanguage`·`dateModified` 는 FinancialProduct 에 **없는 속성**이므로 넣으면 안 된다.
6. **llms.txt 는 사실상 읽히지 않는다** — 넣어뒀지만 기대하지 말 것. Google 공식 미사용.
7. **Google 은 sitemap ping(2023년 폐지, 404)과 IndexNow(미참여) 둘 다 경로가 없다** — robots.txt `Sitemap:` 지시자와 Search Console 수동 제출만 남는다.
8. **PowerShell 5.1 은 BOM 없는 UTF-8 `.ps1` 을 CP949 로 파싱**한다. 반대로 PowerShell 이 쓴 JSON 에는 BOM 이 붙어 Python 이 죽는다 → 설정/데이터 JSON 은 `utf-8-sig` 로 읽는다. PowerShell 에서 bash heredoc 은 파서 오류다.
9. **공시문서 PDF 는 안정적 GET URL 이 없다** — 다운로드가 POST 전용. 파일명만 싣고 출처는 `schText` 딥링크로 건다.
