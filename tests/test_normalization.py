"""標準化ロジックの決定論的テスト（Snowflake・AI 非依存）"""
from datetime import date
from decimal import Decimal

import pytest

from normalization import (columns_to_rows, match_customer, normalize_company, normalize_order, parse_date,
                           parse_number)


@pytest.mark.parametrize("raw,exp", [
    ("9,600", 9600), ("¥25,500", 25500), ("40,800円", 40800), ("１２，３４５", 12345), ("¥40,645-", 40645),
    (" 480 ", 480), ("△100", -100), ("12.5", Decimal("12.5")),
])
def test_parse_number(raw, exp):
    v, err = parse_number(raw)
    assert err is None and v == Decimal(str(exp))


@pytest.mark.parametrize("raw", [None, "", "-", "なし", "N/A"])
def test_parse_number_empty_is_null(raw):
    assert parse_number(raw) == (None, None)


def test_parse_number_invalid_reports_error():
    v, err = parse_number("約1万")
    assert v is None and "解釈できません" in err


@pytest.mark.parametrize("raw,exp", [
    ("2026年11月5日", date(2026, 11, 5)), ("2026/11/06", date(2026, 11, 6)), ("2026.11.07", date(2026, 11, 7)),
    ("2026-11-20", date(2026, 11, 20)), ("R8.11.20", date(2026, 11, 20)), ("令和8年11月20日", date(2026, 11, 20)),
    ("令和元年5月1日", date(2019, 5, 1)), ("2026年11月21日 必着", date(2026, 11, 21)), ("20261105", date(2026, 11, 5)),
    ("２０２６年１１月５日", date(2026, 11, 5)),
])
def test_parse_date(raw, exp):
    assert parse_date(raw) == (exp, None)


def test_parse_date_does_not_guess():
    assert parse_date("11月21日")[0] is None          # 年がない → 推測しない
    assert "存在しない" in parse_date("2026/02/30")[1]


@pytest.mark.parametrize("raw,exp", [("株式会社さくら文具商事", "さくら文具商事"), ("青葉ロジテック(株)", "青葉ロジテック"),
                                     ("青葉ロジテック（株）", "青葉ロジテック"), ("㈱みなと工務店 御中", "みなと工務店")])
def test_normalize_company(raw, exp):
    assert normalize_company(raw) == exp


def test_match_customer(masters):
    assert match_customer("青葉ロジテック(株)", masters.customers) == ("C002", "EXACT")
    assert match_customer("さくら文具商事 総務部", masters.customers)[0] == "C001"
    assert match_customer("株式会社 架空不明商会", masters.customers) == (None, "NOT_FOUND")


def test_columns_to_rows_mismatch_is_error_not_silent():
    rows, err = columns_to_rows({"a": ["1", "2"], "b": ["x"]}, ["a", "b"])
    assert err and "一致しません" in err
    assert rows[1]["b"] is None  # 補完はしない（None のまま、エラーとして報告）


def test_normalize_sample_a(recorded, masters, expected):
    n = normalize_order(recorded["order_company_a.pdf"]["response"], masters)
    e = expected["order_company_a.pdf"]
    assert n.header["ORDER_NUMBER"] == e["header"]["order_number"]
    assert n.header["ORDER_DATE"] == date.fromisoformat(e["header"]["order_date"])
    assert n.header["CUSTOMER_ID"] == "C001"
    assert [l["PRODUCT_CODE"] for l in n.lines] == e["expected_product_codes"]
    assert n.header["CALC_SUBTOTAL"] == e["header"]["subtotal"]
    assert n.header["CALC_TAX_AMOUNT"] == e["header"]["tax_amount"]
    assert n.issues == []


def test_normalize_sample_b_layout_and_code_conversion(recorded, masters, expected):
    n = normalize_order(recorded["order_company_b.pdf"]["response"], masters)
    e = expected["order_company_b.pdf"]
    assert n.header["CUSTOMER_ID"] == "C002"                       # (株) 表記ゆれ
    assert n.header["ORDER_DATE"] == date(2026, 11, 6)            # 2026/11/06
    assert n.header["DELIVERY_DATE"] == date(2026, 11, 20)        # 和暦 R8.11.20
    assert n.header["SUBTOTAL"] == Decimal(40800)                 # '40,800円'
    assert [l["PRODUCT_CODE"] for l in n.lines] == e["expected_product_codes"]  # 独自品番 → 自社コード
    assert [l["STANDARD_QUANTITY"] for l in n.lines] == [Decimal(x) for x in e["expected_standard_quantity"]]
    assert n.lines[0]["RAW_LINE_AMOUNT"] == "¥9,600" and n.lines[0]["LINE_AMOUNT"] == Decimal(9600)  # 元値と対応
    assert n.lines[1]["MASTER_UNIT_PRICE"] == Decimal(8500)       # 契約単価を参照
    assert n.lines[1]["UNIT_PRICE"] == Decimal(8500)


def test_normalize_sample_c_unmapped(recorded, masters):
    n = normalize_order(recorded["order_company_c.pdf"]["response"], masters)
    assert n.header["CUSTOMER_ID"] == "C003"
    assert [l["MAPPING_METHOD"] for l in n.lines] == ["CUSTOMER_MAP", "CUSTOMER_MAP", "UNMAPPED", "CUSTOMER_MAP"]
    assert n.lines[1]["LINE_AMOUNT"] == Decimal(18600)            # 発注書の記載値をそのまま保持


def test_master_conflict_does_not_overwrite(masters):
    resp = {"order_number": "X1", "order_date": "2026/11/01", "buyer_name": "さくら文具商事",
            "order_lines": {"line_number": ["1"], "product_code": ["SV-CP-A4"], "product_name": ["紙"],
                            "specification": [""], "quantity": ["1"], "unit": ["冊"], "unit_price": ["500"],
                            "line_amount": ["500"]}}
    n = normalize_order(resp, masters)
    assert n.lines[0]["UNIT_PRICE"] == Decimal(500)               # 発注書の値
    assert n.lines[0]["MASTER_UNIT_PRICE"] == Decimal(480)        # マスタ値は別列


def test_inline_nulls_for_sis_binding():
    """SiS では None のバインドが 'None' 文字列になるため、NULL はリテラルにする"""
    from db import _inline_nulls
    sql, vals = _inline_nulls("UPDATE t SET a = ?, b = 'x?y', c = ? WHERE id = ?", [None, 5, "k"])
    assert sql == "UPDATE t SET a = NULL, b = 'x?y', c = ? WHERE id = ?" and vals == [5, "k"]
