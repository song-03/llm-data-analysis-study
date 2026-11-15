"""Prepare validated Chapter10 classification input from common project raw data.

Run from the project root:

    python scripts/prepare_ch10_data.py

Input:
    data/raw/customers.csv
    data/raw/products.csv
    data/raw/orders.csv
    data/raw/order_items.csv

Output:
    data/processed/customers_clean.csv
    data/processed/products_clean.csv
    data/processed/orders_clean.csv
    data/processed/order_items_clean.csv
    data/processed/ch10/*_clean.csv (검증된 Chapter10 전용 분석 입력)
    data/processed/ch10/input_scope_checks.csv
    data/processed/ch10/temporal_quarantine_internal.csv (공개 금지 내부 감사)

Chapter05의 전용 오류 탐지 데이터(`practice/chapter05/data/raw`)는 사용하지 않습니다.
Chapter10은 Chapter08·09와 같은 공통 프로젝트 데이터 흐름을 이어받습니다.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data_loader import load_sales_data  # noqa: E402
from src.preprocessing import (  # noqa: E402
    preprocess_sales_data,
    save_processed_data,
    validate_relationships,
)


RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"


def prepare_ch10_scope(
    data: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    """Quarantine impossible order/signup chronology without changing dates.

    This is an explicit Chapter10 cohort policy, not a repair of source dates.
    Common processed inputs remain available to other chapters unchanged.
    Missing dates or relationships still fail instead of silently disappearing.
    """
    audit = data["orders"][
        ["order_id", "customer_id", "order_status", "order_date"]
    ].merge(
        data["customers"][["customer_id", "signup_date"]],
        on="customer_id", how="left", validate="many_to_one", indicator=True,
    )
    if not audit["_merge"].eq("both").all():
        raise ValueError("Chapter10 시간 검증에 고객 미매칭이 있습니다.")
    order_dates = pd.to_datetime(audit["order_date"], errors="coerce")
    signup_dates = pd.to_datetime(audit["signup_date"], errors="coerce")
    if order_dates.isna().any() or signup_dates.isna().any():
        raise ValueError("Chapter10 시간 검증에 결측 또는 변환 불가 날짜가 있습니다.")
    invalid = signup_dates.gt(order_dates)
    quarantine = audit.loc[invalid].drop(columns="_merge").copy()
    quarantine["reason"] = "signup_after_order"
    excluded_ids = set(quarantine["order_id"])
    scoped = {name: frame.copy() for name, frame in data.items()}
    scoped["orders"] = data["orders"].loc[
        ~data["orders"]["order_id"].isin(excluded_ids)
    ].copy()
    scoped["order_items"] = data["order_items"].loc[
        data["order_items"]["order_id"].isin(scoped["orders"]["order_id"])
    ].copy()
    counts = []
    for status in sorted(data["orders"]["order_status"].unique()):
        original = int(data["orders"]["order_status"].eq(status).sum())
        excluded = int(quarantine["order_status"].eq(status).sum())
        counts.append({
            "order_status": status, "original_count": original,
            "quarantined_count": excluded, "prepared_count": original - excluded,
            "policy": "exclude_signup_after_order_no_date_repair",
        })
    return scoped, pd.DataFrame(counts), quarantine


def prepare_ch10_data(
    raw_dir: str | Path = RAW_DIR,
    processed_dir: str | Path = PROCESSED_DIR,
) -> dict[str, object]:
    """Preprocess common raw data and fail on core relationship errors."""
    raw_data = load_sales_data(raw_dir)
    processed_data = preprocess_sales_data(raw_data)
    relationship_checks = validate_relationships(processed_data)

    if (
        not relationship_checks.empty
        and not relationship_checks["invalid_count"].eq(0).all()
    ):
        failed = relationship_checks.loc[
            relationship_checks["invalid_count"].ne(0)
        ]
        raise ValueError(
            "Chapter10 입력 관계 검증에 실패했습니다:\n"
            + failed.to_string(index=False)
        )

    ch10_data, scope_checks, quarantine = prepare_ch10_scope(processed_data)
    ch10_relationships = validate_relationships(ch10_data)
    if not ch10_relationships["invalid_count"].eq(0).all():
        raise ValueError("Chapter10 전용 입력의 외래키 검증에 실패했습니다.")
    # Keep the classification module's strict date/calculation/merge checks.
    # Validate before writing either the common or Chapter10-specific inputs.
    from src.classification import build_classification_dataset
    build_classification_dataset(
        customers=ch10_data["customers"], orders=ch10_data["orders"],
        order_items=ch10_data["order_items"],
    )
    saved_paths = save_processed_data(processed_data, processed_dir)
    ch10_dir = Path(processed_dir) / "ch10"
    ch10_saved_paths = save_processed_data(ch10_data, ch10_dir)
    scope_checks.to_csv(ch10_dir / "input_scope_checks.csv", index=False, encoding="utf-8-sig")
    quarantine.to_csv(ch10_dir / "temporal_quarantine_internal.csv", index=False, encoding="utf-8-sig")
    return {
        "raw_data": raw_data,
        "processed_data": processed_data,
        "relationship_checks": relationship_checks,
        "saved_paths": saved_paths,
        "ch10_data": ch10_data,
        "ch10_saved_paths": ch10_saved_paths,
        "scope_checks": scope_checks,
        "temporal_quarantine": quarantine,
    }


def main() -> None:
    result = prepare_ch10_data()

    print("Chapter10 모델링 입력 준비 완료")
    print(f"입력 Raw 경로: {RAW_DIR}")
    print("\n[관계 검증]")
    print(result["relationship_checks"].to_string(index=False))
    print("\n[Chapter10 입력 범위: 날짜를 변경하지 않고 시간 관계 오류 격리]")
    print(result["scope_checks"].to_string(index=False))
    print("\n[저장된 processed 파일]")
    for path in result["saved_paths"]:
        print(f"- {path}")
    print("\n[검증된 Chapter10 전용 입력]")
    for path in result["ch10_saved_paths"]:
        print(f"- {path}")


if __name__ == "__main__":
    main()
