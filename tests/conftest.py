"""共通フィクスチャ: app/ を import パスに追加し、Snowflake 非依存のマスタを用意"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from normalization import Masters  # noqa: E402


@pytest.fixture
def masters():
    """sql/02_master_data.sql と同内容のマスタ（ローカルテスト用）"""
    customers = [
        {"CUSTOMER_ID": "C001", "OFFICIAL_NAME": "株式会社さくら文具商事", "NAME_ALIASES": ["さくら文具商事", "(株)さくら文具商事"], "IS_ACTIVE": True},
        {"CUSTOMER_ID": "C002", "OFFICIAL_NAME": "青葉ロジテック株式会社", "NAME_ALIASES": ["青葉ロジテック", "青葉ロジテック(株)"], "IS_ACTIVE": True},
        {"CUSTOMER_ID": "C003", "OFFICIAL_NAME": "みなと工務店株式会社", "NAME_ALIASES": ["みなと工務店"], "IS_ACTIVE": True},
        {"CUSTOMER_ID": "C099", "OFFICIAL_NAME": "旧取引 ひまわり事務機株式会社", "NAME_ALIASES": ["ひまわり事務機"], "IS_ACTIVE": False},
    ]
    P = lambda c, n, u, p: {"PRODUCT_CODE": c, "PRODUCT_NAME": n, "STANDARD_UNIT": u, "REFERENCE_PRICE": p,
                            "TAX_CATEGORY": "STANDARD", "IS_ACTIVE": True}
    products = {p["PRODUCT_CODE"]: p for p in [
        P("SV-CP-A4", "コピー用紙", "冊", 480), P("SV-TN-K01", "トナー", "本", 8800), P("SV-CF-A4", "クリアファイル", "パック", 350),
        P("SV-BP-BK", "ボールペン", "箱", 900), P("SV-ST-10", "ホッチキス針", "箱", 120), P("SV-FF-A4", "フラットファイル", "パック", 1200)]}
    M = lambda cid, code, pc, unit, f=1, price=None: ((cid, code), {"CUSTOMER_ID": cid, "CUSTOMER_PRODUCT_CODE": code,
            "PRODUCT_CODE": pc, "CUSTOMER_UNIT": unit, "UNIT_CONVERSION_FACTOR": f, "CONTRACT_UNIT_PRICE": price, "IS_ACTIVE": True})
    cmap = dict([
        M("C001", "SV-CP-A4", "SV-CP-A4", "冊"), M("C001", "SV-TN-K01", "SV-TN-K01", "本"),
        M("C001", "SV-CF-A4", "SV-CF-A4", "パック"), M("C001", "SV-BP-BK", "SV-BP-BK", "箱"),
        M("C002", "AL-1001", "SV-CP-A4", "箱", 5, 2400), M("C002", "AL-2050", "SV-TN-K01", "個", 1, 8500),
        M("C002", "AL-3300", "SV-ST-10", "箱"), M("C002", "AL-3410", "SV-FF-A4", "P"), M("C002", "AL-3120", "SV-CF-A4", "P"),
        M("C003", "MK-0101", "SV-CP-A4", "冊"), M("C003", "MK-0205", "SV-TN-K01", "本"), M("C003", "MK-0310", "SV-BP-BK", "箱"),
    ])
    taxes = [{"TAX_CATEGORY": "STANDARD", "TAX_RATE": 0.10, "VALID_FROM": date(2019, 10, 1), "VALID_TO": None},
             {"TAX_CATEGORY": "REDUCED", "TAX_RATE": 0.08, "VALID_FROM": date(2019, 10, 1), "VALID_TO": None}]
    return Masters(customers, products, cmap, taxes)


@pytest.fixture
def recorded():
    return json.loads((ROOT / "tests/fixtures/recorded_ai_responses.json").read_text(encoding="utf-8"))


@pytest.fixture
def expected():
    return json.loads((ROOT / "samples/expected_results.json").read_text(encoding="utf-8"))
