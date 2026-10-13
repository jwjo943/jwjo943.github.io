"""한화생명 상품공시실 -> 중간 JSON 수집기.

사용:
    py scripts/fetch_products.py                      # 판매중 상품
    py scripts/fetch_products.py --sell-flag N        # 판매중지 상품
    py scripts/fetch_products.py -o data/products.json

출력은 JSON-LD 가 아니라 원본에 가까운 중간 포맷이다. JSON-LD 변환은
build_jsonld.py 가 이 파일을 입력으로 받아 수행한다 (수집과 변환을 분리해
공시실을 다시 때리지 않고 스키마만 고칠 수 있게 했다).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from hanwha_disclosure import (
    DOCUMENT_DOWNLOAD_ENDPOINT,
    LIST_PAGE,
    PUBLISHER_BUSINESS_NUMBER,
    PUBLISHER_LEGAL_NAME,
    Category,
    DisclosureError,
    GoodsListClient,
    Product,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "products.json"


def _progress(kind: str, payload: object, count: int) -> None:
    if kind == "category":
        category = payload
        assert isinstance(category, Category)
        print(f"  [{category.sell_type}/{category.goods_type}] {category.label} -> {count}건",
              file=sys.stderr)
    else:
        product = payload
        assert isinstance(product, Product)
        print(f"      {product.idx:>5}  {product.name}  (판매기간 {count}구간)", file=sys.stderr)


def serialise(product: Product) -> dict:
    return {
        "idx": product.idx,
        "name": product.name,
        "isRider": product.is_rider,
        "category": {
            "sellType": product.category.sell_type,
            "sellTypeName": product.category.sell_type_name,
            "goodsType": product.category.goods_type,
            "goodsTypeName": product.category.goods_type_name,
            "label": product.category.label,
        },
        "sourceUrl": product.source_url(),
        "terms": [
            {
                "sellStart": term.sell_start,
                "sellEnd": term.sell_end,
                "onSale": term.on_sale,
                "documents": [
                    {"kind": doc.kind, "fileName": doc.file_name} for doc in term.documents
                ],
            }
            for term in product.terms
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"출력 JSON 경로 (기본 {DEFAULT_OUTPUT})")
    parser.add_argument("--sell-flag", choices=["Y", "N"], default="Y",
                        help="Y=판매중(기본), N=판매중지")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="요청 간 대기 초 (기본 0.3)")
    parser.add_argument("--limit", type=int, default=0,
                        help="상품 N건만 수집 (0=전체). 스키마 시험용")
    args = parser.parse_args(argv)

    client = GoodsListClient(delay=args.delay)
    products: list[Product] = []

    print(f"한화생명 상품공시실 수집 (sellFlag={args.sell_flag})", file=sys.stderr)
    try:
        for product in client.fetch_all(args.sell_flag, on_progress=_progress):
            products.append(product)
            if args.limit and len(products) >= args.limit:
                print(f"  --limit {args.limit} 도달, 중단", file=sys.stderr)
                break
    except DisclosureError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    if not products:
        print("오류: 수집된 상품이 0건이다. 엔드포인트 또는 파라미터가 바뀐 것으로 보인다.",
              file=sys.stderr)
        return 1

    document_count = sum(len(term.documents) for p in products for term in p.terms)
    payload = {
        "fetchedAt": date.today().isoformat(),
        "sellFlag": args.sell_flag,
        "truncated": bool(args.limit) and len(products) >= args.limit,
        "source": {
            "publisherLegalName": PUBLISHER_LEGAL_NAME,
            "publisherBusinessNumber": PUBLISHER_BUSINESS_NUMBER,
            "listPage": LIST_PAGE,
            "documentDownloadEndpoint": DOCUMENT_DOWNLOAD_ENDPOINT,
            "documentDownloadMethod": "POST file_name=<fileName>",
        },
        "counts": {
            "products": len(products),
            "salesTerms": sum(len(p.terms) for p in products),
            "documents": document_count,
            "riders": sum(1 for p in products if p.is_rider),
        },
        "products": [serialise(p) for p in products],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    counts = payload["counts"]
    print("", file=sys.stderr)
    print(f"완료  상품 {counts['products']}건 (특약 {counts['riders']}건) / "
          f"판매기간 {counts['salesTerms']}구간 / 공시문서 {counts['documents']}건",
          file=sys.stderr)
    print(f"      -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
