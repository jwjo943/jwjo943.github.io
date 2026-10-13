"""한화생명 상품공시실 조회 클라이언트.

역할: HTTP 통신과 JSON 파싱만 담당한다. 수집 흐름은 fetch_products.py,
JSON-LD 변환은 build_jsonld.py 가 담당한다.

검증 시점: 2026-10-13. 표준 라이브러리만 사용한다 (설치 의존성 없음).

엔드포인트 (공개, 인증 불필요, 전부 POST form-urlencoded):
    getList.do   상품구분 목록(list1) + 상품명 목록(list2)
    getList2.do  sellType+goodsType 으로 필터한 상품명 목록(list2)
    getList3.do  goodsIndex 하나의 판매기간 이력 + 공시문서 파일명(list3)

!! 함정 (실측 2026-10-13) !!
getList.do 를 sellType/goodsType 없이 호출하면 33건이 돌아오는데, 이것은
전체 상품이 아니라 **첫 번째 구분(개인/보장성) 하나**다. 전체(101건)를 얻으려면
list1 의 15개 구분을 전부 getList2.do 로 돌려야 한다. GoodsListClient.fetch_all()
이 그렇게 동작한다.
"""

from __future__ import annotations

import gzip
import http.cookiejar
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

if sys.version_info < (3, 12):
    raise SystemExit(
        f"Python 3.12 이상이 필요하다 (현재 {sys.version_info.major}.{sys.version_info.minor})."
    )

BASE = "https://www.hanwhalife.com"
API_PATH = "/main/disclosure/goods/goodslist/"

#: 세션 쿠키를 받고 Referer 로도 쓰는 상품목록 페이지.
LIST_PAGE = (
    BASE + "/main/disclosure/goods/disclosurenotice/DF_GDDN000_P10000.do"
    "?MENU_ID1=DF_GDGL000&MENU_ID2=DF_GDGL000_P10000"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: FILE_NAME1~4 가 각각 어떤 공시문서인지. 화면 표 헤더(상품요약서/사업방법서/약관)와
#: FILE_NAME4(주요내용요약서)를 실측해서 확인한 매핑이다.
DOCUMENT_KINDS: dict[str, str] = {
    "FILE_NAME1": "상품요약서",
    "FILE_NAME2": "사업방법서",
    "FILE_NAME3": "약관",
    "FILE_NAME4": "주요내용요약서",
}

#: 공시문서 PDF 는 POST 전용 엔드포인트로만 내려온다 (안정적인 GET URL 이 없다).
DOCUMENT_DOWNLOAD_ENDPOINT = BASE + "/www/announce/goods/download_chk.asp"

#: 공시실 푸터에서 확인한 공시 주체 (hw 상품목록 페이지 HTML, 2026-10-13).
PUBLISHER_LEGAL_NAME = "한화생명보험 주식회사"
PUBLISHER_BUSINESS_NUMBER = "116-81-11757"


def _ssl_context() -> ssl.SSLContext:
    """www.hanwhalife.com 용 TLS 컨텍스트.

    실측 2026-10-13: 기본 컨텍스트로 접속하면 OpenSSL 3 이
    `[SSL: UNSAFE_LEGACY_RENEGOTIATION_DISABLED]` 로 끊는다. 서버가 RFC 5746
    secure renegotiation 확장 없이 재협상을 요구하므로 해당 옵션을 열어야 한다.
    인증서 검증과 호스트명 확인은 그대로 켜 둔다 (끄지 않는다).
    """
    context = ssl.create_default_context()
    context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return context


class DisclosureError(RuntimeError):
    """공시실 응답이 기대한 형태가 아닐 때."""


@dataclass(frozen=True, slots=True)
class Category:
    """상품구분(판매채널) x 상품종류 한 쌍."""

    sell_type: str
    sell_type_name: str
    goods_type: str
    goods_type_name: str

    @property
    def label(self) -> str:
        return f"{self.sell_type_name} > {self.goods_type_name}"


@dataclass(frozen=True, slots=True)
class Document:
    """판매기간 한 구간에 걸린 공시문서 1건."""

    kind: str
    file_name: str


@dataclass(frozen=True, slots=True)
class SalesTerm:
    """판매기간 한 구간. sell_end 가 비어 있으면 현재 판매중이다."""

    sell_start: str
    sell_end: str
    documents: tuple[Document, ...]

    @property
    def on_sale(self) -> bool:
        return not self.sell_end


@dataclass(slots=True)
class Product:
    idx: int
    name: str
    category: Category
    terms: list[SalesTerm] = field(default_factory=list)

    @property
    def current_term(self) -> SalesTerm | None:
        """판매중인 구간. 없으면 가장 최근 구간을 반환한다."""
        for term in self.terms:
            if term.on_sale:
                return term
        return self.terms[0] if self.terms else None

    @property
    def is_rider(self) -> bool:
        """특약 여부. SD 가 특약 채널이다."""
        return self.category.sell_type == "SD"

    def source_url(self) -> str:
        """공시실 상품 검색 딥링크.

        상품목록 페이지가 검색엔진 유입 시 schText 쿼리스트링을 받는 구조라
        상품명으로 바로 검색된 상태를 가리킬 수 있다.
        """
        return LIST_PAGE + "&" + urllib.parse.urlencode({"schText": self.name})


class GoodsListClient:
    """상품공시실 세션 하나를 들고 세 엔드포인트를 호출한다."""

    def __init__(self, *, delay: float = 0.3, timeout: float = 40.0, retries: int = 3) -> None:
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
            urllib.request.HTTPSHandler(context=_ssl_context()),
        )
        self._delay = delay
        self._timeout = timeout
        self._retries = retries
        self._warmed = False

    # -- 내부 --------------------------------------------------------------

    def _open(self, url: str, data: bytes | None = None) -> str:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": LIST_PAGE,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Encoding": "gzip, identity",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            headers["X-Requested-With"] = "XMLHttpRequest"

        last: Exception | None = None
        for attempt in range(1, self._retries + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    raw = response.read()
                    if response.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return raw.decode("utf-8", errors="replace")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
                if attempt < self._retries:
                    time.sleep(self._delay * (2**attempt))
        raise DisclosureError(f"{url} 요청 실패: {last}") from last

    def _warm(self) -> None:
        """세션 쿠키를 먼저 받아둔다. 이것 없이 API 를 때리면 빈 응답이 올 수 있다."""
        if not self._warmed:
            self._open(LIST_PAGE)
            self._warmed = True
            time.sleep(self._delay)

    def _post_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        self._warm()
        body = urllib.parse.urlencode(params, encoding="utf-8").encode("utf-8")
        text = self._open(BASE + API_PATH + endpoint, body)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DisclosureError(f"{endpoint} 응답이 JSON 이 아니다: {text[:200]!r}") from exc

        status = payload.get("status") or {}
        if status.get("code") not in (None, "0000"):
            raise DisclosureError(f"{endpoint} 오류 응답: {status}")
        time.sleep(self._delay)
        return payload

    @staticmethod
    def _form(sell_flag: str, **overrides: str) -> dict[str, str]:
        form = {
            "PType": "1",
            "sellFlag": sell_flag,
            "sellType": "",
            "goodsType": "",
            "goodsIndex": "",
            "schText": "",
        }
        form.update(overrides)
        return form

    # -- 공개 API ----------------------------------------------------------

    def categories(self, sell_flag: str = "Y") -> list[Category]:
        """상품구분 x 상품종류 목록."""
        payload = self._post_json("getList.do", self._form(sell_flag))
        rows = payload.get("list1")
        if not rows:
            raise DisclosureError("getList.do 응답에 list1 이 없다.")
        return [
            Category(
                sell_type=row["SELL_TYPE"],
                sell_type_name=row["SELL_TYPE_NM"],
                goods_type=row["GOODS_TYPE"],
                goods_type_name=row["GOODS_TYPE_NM"],
            )
            for row in rows
        ]

    def products_in(self, category: Category, sell_flag: str = "Y") -> list[tuple[int, str]]:
        """구분 하나에 속한 (goodsIndex, 상품명) 목록."""
        payload = self._post_json(
            "getList2.do",
            self._form(sell_flag, sellType=category.sell_type, goodsType=category.goods_type),
        )
        return [(int(row["IDX"]), row["GOODS_NAME"]) for row in payload.get("list2") or []]

    def terms_of(self, goods_index: int, sell_flag: str = "Y") -> list[SalesTerm]:
        """상품 하나의 판매기간 이력과 각 구간의 공시문서."""
        payload = self._post_json(
            "getList3.do", self._form(sell_flag, goodsIndex=str(goods_index))
        )
        terms: list[SalesTerm] = []
        for row in payload.get("list3") or []:
            documents = tuple(
                Document(kind=kind, file_name=(row.get(key) or "").strip())
                for key, kind in DOCUMENT_KINDS.items()
                if (row.get(key) or "").strip()
            )
            terms.append(
                SalesTerm(
                    sell_start=(row.get("SELL_START_DT") or "").strip(),
                    sell_end=(row.get("SELL_END_DT") or "").strip(),
                    documents=documents,
                )
            )
        return terms

    def fetch_all(
        self, sell_flag: str = "Y", *, on_progress=None
    ) -> Iterator[Product]:
        """모든 구분을 순회해 상품을 전부 수집한다.

        getList.do 무필터 결과는 첫 구분 하나뿐이므로 쓰지 않는다 (모듈 docstring 참고).
        goodsIndex 가 중복되면 먼저 만난 구분으로 확정한다.
        """
        seen: set[int] = set()
        for category in self.categories(sell_flag):
            rows = self.products_in(category, sell_flag)
            if on_progress:
                on_progress("category", category, len(rows))
            for goods_index, name in rows:
                if goods_index in seen:
                    continue
                seen.add(goods_index)
                product = Product(idx=goods_index, name=name, category=category)
                product.terms = self.terms_of(goods_index, sell_flag)
                if on_progress:
                    on_progress("product", product, len(product.terms))
                yield product
