"""検証ルールの決定論的テスト（Snowflake・AI 非依存）"""
import copy
from decimal import Decimal

from normalization import apply_mapping, calc_totals, normalize_order
from validation import ERROR, PASS, WARNING, overall_level, validate


def _levels(results, rule):
    return [r["level"] for r in results if r["rule_id"] == rule]


def _run(resp, masters, ctx=None):
    n = normalize_order(resp, masters)
    return n, validate(n.header, n.lines, {"issues": n.issues, **(ctx or {})})


def test_sample_a_passes(recorded, masters):
    _, res = _run(recorded["order_company_a.pdf"]["response"], masters)
    assert overall_level(res) == PASS, [r for r in res if r["level"] != PASS]


def test_sample_b_ready(recorded, masters):
    _, res = _run(recorded["order_company_b.pdf"]["response"], masters)
    assert overall_level(res) in (PASS, WARNING)
    assert ERROR not in [r["level"] for r in res]


def test_sample_c_errors(recorded, masters):
    _, res = _run(recorded["order_company_c.pdf"]["response"], masters)
    errors = {r["rule_id"] for r in res if r["level"] == ERROR}
    # 明細2の金額誤りは明細合計→税抜合計・税額の不一致にも波及する
    assert errors == {"PRODUCT_MAPPING", "LINE_AMOUNT", "SUBTOTAL", "TAX"}


def test_sample_c_fixed_becomes_confirmable(recorded, masters):
    n = normalize_order(recorded["order_company_c.pdf"]["response"], masters)
    n.lines[2] = apply_mapping(n.lines[2], "C003", masters, n.header["ORDER_DATE"], manual_code="SV-CF-A4")
    n.lines[1]["LINE_AMOUNT"] = Decimal(17600)
    n.header["CALC_SUBTOTAL"], n.header["CALC_TAX_AMOUNT"] = calc_totals(n.lines, masters, n.header["ORDER_DATE"])
    res = validate(n.header, n.lines, {})
    assert overall_level(res) == WARNING  # 手動対応付けは WARNING として残る
    assert _levels(res, "PRODUCT_MAPPING").count(WARNING) == 1


def test_required_fields_missing(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["order_number"] = ""
    _, res = _run(resp, masters)
    assert ERROR in _levels(res, "REQUIRED_FIELDS")


def test_unknown_customer(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["buyer_name"] = "株式会社 架空不明商会"
    _, res = _run(resp, masters)
    assert ERROR in _levels(res, "CUSTOMER_MATCH")


def test_inactive_customer(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["buyer_name"] = "ひまわり事務機"
    n = normalize_order(resp, masters)
    res = validate(n.header, n.lines, {"customers_active": {"C099": False}})
    assert ERROR in _levels(res, "CUSTOMER_MATCH")


def test_non_positive_quantity(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["order_lines"]["quantity"][0] = "0"
    _, res = _run(resp, masters)
    assert ERROR in _levels(res, "QUANTITY")


def test_column_length_mismatch_is_error(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["order_lines"]["unit_price"].pop()
    _, res = _run(resp, masters)
    assert ERROR in _levels(res, "LINE_STRUCTURE")


def test_no_lines_is_error(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["order_lines"] = {k: [] for k in resp["order_lines"]}
    _, res = _run(resp, masters)
    assert ERROR in _levels(res, "LINE_STRUCTURE")


def test_total_mismatch(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["total_amount"] = "41,000"
    _, res = _run(resp, masters)
    assert ERROR in _levels(res, "TOTAL")


def test_tax_rounding_difference_is_warning(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["tax_amount"], resp["total_amount"] = "3,696", "40,646"   # 1円差 = 端数処理差
    _, res = _run(resp, masters)
    assert _levels(res, "TAX") == [WARNING] and _levels(res, "TOTAL") == [PASS]


def test_missing_price_and_tax_are_warnings_not_errors(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["order_lines"]["unit_price"] = ["", "", "", ""]
    resp["tax_amount"], resp["total_amount"] = "", ""
    _, res = _run(resp, masters)
    assert overall_level(res) == WARNING
    assert set(_levels(res, "UNIT_PRICE")) == {WARNING}


def test_price_differs_from_master_is_warning(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["order_lines"]["unit_price"][0], resp["order_lines"]["line_amount"][0] = "500", "10,000"
    resp["subtotal"], resp["tax_amount"], resp["total_amount"] = "37,350", "3,735", "41,085"
    _, res = _run(resp, masters)
    assert WARNING in _levels(res, "UNIT_PRICE") and overall_level(res) == WARNING


def test_invalid_order_date(recorded, masters):
    resp = copy.deepcopy(recorded["order_company_a.pdf"]["response"])
    resp["order_date"] = "2026/13/45"
    _, res = _run(resp, masters)
    assert ERROR in _levels(res, "DATE")


def test_duplicate_order_number_confirmed_blocks(recorded, masters):
    ctx = {"order_number_conflicts": [{"ORDER_ID": "o1", "DOCUMENT_ID": "d1234567", "ORDER_STATUS": "CONFIRMED"}]}
    _, res = _run(recorded["order_company_a.pdf"]["response"], masters, ctx)
    assert _levels(res, "DUPLICATE_ORDER_NUMBER") == [ERROR]


def test_duplicate_file_blocks(recorded, masters):
    _, res = _run(recorded["order_company_a.pdf"]["response"], masters, {"duplicate_file_of": "d0"})
    assert _levels(res, "DUPLICATE_FILE") == [ERROR]


def test_low_score_is_warning_only(recorded, masters):
    _, res = _run(recorded["order_company_a.pdf"]["response"], masters, {"scores": {"order_lines": {"score": 0.3}}})
    assert overall_level(res) == WARNING


def test_tax_rate_comes_from_master(recorded, masters):
    masters.tax_rates[0]["TAX_RATE"] = 0.08
    n = normalize_order(recorded["order_company_a.pdf"]["response"], masters)
    assert n.header["CALC_TAX_AMOUNT"] == Decimal(2956)  # floor(36,950 × 0.08)


def test_correction_eligible_only_for_order_number_duplicate(recorded, masters):
    from validation import correction_eligible
    ctx = {"order_number_conflicts": [{"ORDER_ID": "o1", "DOCUMENT_ID": "d1234567", "ORDER_STATUS": "CONFIRMED"}]}
    _, res = _run(recorded["order_company_a.pdf"]["response"], masters, ctx)
    assert correction_eligible(res)                      # ERROR は発注番号重複のみ → 訂正版として確定可
    _, res_c = _run(recorded["order_company_c.pdf"]["response"], masters, ctx)
    assert not correction_eligible(res_c)                # 他の ERROR もある → 不可
    _, res_ok = _run(recorded["order_company_a.pdf"]["response"], masters)
    assert not correction_eligible(res_ok)               # ERROR なし → 通常の確定を使う
