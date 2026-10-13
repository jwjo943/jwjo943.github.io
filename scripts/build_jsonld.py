"""중간 JSON -> schema.org JSON-LD 변환기.

사용:
    py scripts/build_jsonld.py
    py scripts/build_jsonld.py --base-url https://<계정>.github.io/hanwha-disclosure

출력:
    data/jsonld/catalog.jsonld          전체 카탈로그 1개 파일 (@graph)
    data/jsonld/products/<idx>.jsonld   상품 1건 (판매기간 전체 이력 포함)

스키마 선택 근거 (schema.org 확인, 2026-10-13):
  - schema.org 에 InsuranceProduct 타입은 **없다**. 보험상품은 FinancialProduct
    (⊂ Service) 로 모델링하는 것이 유일한 선택이다.
  - FinancialProduct 에는 dateModified 가 없다 (CreativeWork 속성이다). 수집
    시점은 Dataset 노드에만 적는다.
  - additionalProperty 도 FinancialProduct 에 **없다** (Product/Offer/Place/
    QualitativeValue/QuantitativeValue/MerchantReturnPolicy 전용). 판매채널과
    주보험/특약 구분은 category 문자열 계층("개인 > 보장성 > 주보험")과
    disambiguatingDescription 으로 표현한다. schema.org 가 category 설명에서
    ">" 로 계층을 표기하는 관례를 명시하고 있다.
  - inLanguage 역시 Service 계열에는 없다 (CreativeWork/Event/BroadcastService 등).
    Dataset 노드에만 쓴다.
  - 판매기간은 Offer.availabilityStarts / availabilityEnds 로 표현한다.

--base-url 을 주지 않으면 @id 가 urn: 으로 떨어진다. 사이트 주소가 확정되기
전에 임의 도메인을 지어내지 않기 위한 기본값이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "products.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "jsonld"

SCHEMA = "https://schema.org"
HANWHA_HOME = "https://www.hanwhalife.com/"

#: 판매기간 상태 -> schema.org ItemAvailability
AVAILABILITY_ON_SALE = f"{SCHEMA}/InStock"
AVAILABILITY_ENDED = f"{SCHEMA}/Discontinued"


def product_id(base_url: str | None, idx: int) -> str:
    if base_url:
        return f"{base_url}/products/{idx}/"
    return f"urn:hanwhalife:goods:{idx}"


def organization_node(source: dict[str, Any], base_url: str | None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@type": "Organization",
        "@id": f"{base_url}/#publisher" if base_url else "urn:hanwhalife:publisher",
        "name": "한화생명",
        "legalName": source["publisherLegalName"],
        "url": HANWHA_HOME,
        "identifier": {
            "@type": "PropertyValue",
            "propertyID": "사업자등록번호",
            "value": source["publisherBusinessNumber"],
        },
    }
    return node


def dataset_node(data: dict[str, Any], base_url: str | None, org_id: str) -> dict[str, Any]:
    counts = data["counts"]
    sell_label = "판매중" if data["sellFlag"] == "Y" else "판매중지"
    return {
        "@type": "Dataset",
        "@id": f"{base_url}/#dataset" if base_url else "urn:hanwhalife:dataset",
        "name": f"한화생명 {sell_label} 보험상품 공시 목록",
        "description": (
            f"한화생명 상품공시실 상품목록에서 수집한 {sell_label} 보험상품 "
            f"{counts['products']}건(특약 {counts['riders']}건 포함). 각 상품의 분류, "
            f"판매기간 이력 {counts['salesTerms']}구간, 공시문서 {counts['documents']}건의 "
            "목록을 담는다. 보험료·보장금액·해지환급률은 공시실 JSON 에 제공되지 않아 "
            "포함하지 않는다 (해당 수치는 상품요약서 PDF 안에만 있다)."
        ),
        "inLanguage": "ko",
        "dateModified": data["fetchedAt"],
        "isBasedOn": data["source"]["listPage"],
        "about": {"@id": org_id},
        "creator": {"@id": org_id},
        **({"url": f"{base_url}/"} if base_url else {}),
    }


def document_nodes(documents: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "@type": "DigitalDocument",
            "name": doc["kind"],
            "alternateName": doc["fileName"],
            "encodingFormat": "application/pdf",
        }
        for doc in documents
    ]


def offer_nodes(terms: list[dict[str, Any]], org_id: str) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    for term in terms:
        offer: dict[str, Any] = {
            "@type": "Offer",
            "availability": AVAILABILITY_ON_SALE if term["onSale"] else AVAILABILITY_ENDED,
            "availabilityStarts": term["sellStart"],
            "seller": {"@id": org_id},
        }
        if term["sellEnd"]:
            offer["availabilityEnds"] = term["sellEnd"]
        if term["documents"]:
            offer["subjectOf"] = document_nodes(term["documents"])
        offers.append(offer)
    return offers


def product_node(
    product: dict[str, Any],
    *,
    base_url: str | None,
    org_id: str,
    terms: list[dict[str, Any]],
) -> dict[str, Any]:
    """FinancialProduct 노드. terms 로 넘긴 판매기간만 offers 에 싣는다."""
    category = product["category"]
    structure = "특약" if product["isRider"] else "주보험"
    node: dict[str, Any] = {
        "@type": "FinancialProduct",
        "@id": product_id(base_url, product["idx"]),
        "name": product["name"],
        "category": f"{category['sellTypeName']} > {category['goodsTypeName']} > {structure}",
        "serviceType": category["goodsTypeName"],
        "disambiguatingDescription": (
            "특약 — 주계약에 부가하여 가입하는 상품으로 단독 가입 대상이 아니다."
            if product["isRider"]
            else f"주보험 — 판매채널 {category['sellTypeName']}, 상품종류 {category['goodsTypeName']}."
        ),
        "provider": {"@id": org_id},
        "areaServed": {"@type": "Country", "name": "대한민국"},
        "identifier": {
            "@type": "PropertyValue",
            "propertyID": "hanwhalife:goodsIndex",
            "value": str(product["idx"]),
        },
        "sameAs": product["sourceUrl"],
        "offers": offer_nodes(terms, org_id),
    }
    if base_url:
        node["url"] = node["@id"]
    return node


def build(data: dict[str, Any], base_url: str | None) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    org = organization_node(data["source"], base_url)
    org_id = org["@id"]
    dataset = dataset_node(data, base_url, org_id)

    catalog_products: list[dict[str, Any]] = []
    per_product: dict[int, dict[str, Any]] = {}

    for product in data["products"]:
        all_terms = product["terms"]
        # 카탈로그 1개 파일에 793구간 전부를 싣으면 수 MB 가 되어 크롤러가 끝까지
        # 읽지 않는다. 카탈로그에는 현재(또는 최신) 구간만 싣고, 전체 이력은
        # 상품별 파일에 둔다. 무엇을 잘랐는지는 아래에서 로그로 남긴다.
        current = [t for t in all_terms if t["onSale"]] or all_terms[:1]
        catalog_products.append(
            product_node(product, base_url=base_url, org_id=org_id, terms=current)
        )

        full = product_node(product, base_url=base_url, org_id=org_id, terms=all_terms)
        per_product[product["idx"]] = {
            "@context": SCHEMA,
            "@graph": [org, dataset, full],
        }

    item_list = {
        "@type": "ItemList",
        "@id": f"{base_url}/#catalog" if base_url else "urn:hanwhalife:catalog",
        "name": dataset["name"],
        "numberOfItems": len(catalog_products),
        "itemListOrder": f"{SCHEMA}/ItemListUnordered",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "item": {"@id": node["@id"]}}
            for i, node in enumerate(catalog_products, start=1)
        ],
    }

    catalog = {
        "@context": SCHEMA,
        "@graph": [org, dataset, item_list, *catalog_products],
    }
    return catalog, per_product


def write_json(path: Path, payload: dict[str, Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT,
                        help=f"fetch_products.py 출력 (기본 {DEFAULT_INPUT})")
    parser.add_argument("-o", "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"JSON-LD 출력 디렉터리 (기본 {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--base-url", default=None,
                        help="사이트 루트 URL (예: https://user.github.io/hanwha-disclosure). "
                             "생략하면 @id 를 urn: 으로 발급한다.")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"오류: 입력 파일이 없다 -> {args.input}", file=sys.stderr)
        print("      먼저 `py scripts/fetch_products.py` 를 실행해라.", file=sys.stderr)
        return 1

    base_url = args.base_url.rstrip("/") if args.base_url else None
    # utf-8-sig: 손으로 고친 입력에 BOM 이 붙어 있어도 받아들인다.
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if not data.get("products"):
        print(f"오류: {args.input} 에 products 가 비어 있다.", file=sys.stderr)
        return 1

    catalog, per_product = build(data, base_url)

    catalog_path = args.output_dir / "catalog.jsonld"
    catalog_bytes = write_json(catalog_path, catalog)

    product_bytes = 0
    for idx, payload in per_product.items():
        product_bytes += write_json(args.output_dir / "products" / f"{idx}.jsonld", payload)

    total_terms = data["counts"]["salesTerms"]
    catalog_terms = sum(len(n["offers"]) for n in catalog["@graph"] if n.get("@type") == "FinancialProduct")

    print(f"catalog.jsonld        {len(per_product)}상품 / {catalog_terms}구간 / {catalog_bytes:,} bytes "
          f"-> {catalog_path}")
    print(f"products/*.jsonld     {len(per_product)}파일 / {total_terms}구간 / {product_bytes:,} bytes "
          f"-> {args.output_dir / 'products'}")
    print(f"카탈로그에서 생략된 과거 판매기간: {total_terms - catalog_terms}구간 "
          f"(상품별 파일에는 전부 들어 있다)")
    if not base_url:
        print("@id 는 urn: 으로 발급했다. 사이트 주소가 정해지면 --base-url 로 다시 실행해라.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
