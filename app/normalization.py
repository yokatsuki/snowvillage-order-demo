"""標準化: AI抽出結果（取引先ごとに表記が異なる文字列）→ 共通の受注データ構造

純粋関数のみ（Snowflake に依存しない）→ tests/test_normalization.py で決定論的にテスト。
方針:
- 発注書の表記（raw）と標準化値を両方保持する
- 発注書とマスタが矛盾しても発注書の値を書き換えない（マスタ値は参考列に保持し、検証で指摘）
- 解釈できない値は None + issues に記録（推測で補完しない）
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

EMPTY_TOKENS = {"", "-", "ー", "―", "なし", "無し", "n/a", "na", "none", "null", "記載なし", "不明"}
ERA_BASE = {"R": 2018, "令和": 2018, "H": 1988, "平成": 1988}


def nfkc(s) -> str | None:
    if s is None:
        return None
    s = unicodedata.normalize("NFKC", str(s)).strip()
    return None if s.lower() in EMPTY_TOKENS else s


# ------------------------------------------------------------------ numbers
def parse_number(raw) -> tuple[Decimal | None, str | None]:
    """'¥1,234' '1,234円' '１２３４' '1,234-' → Decimal。解釈不能は (None, 理由)"""
    s = nfkc(raw)
    if s is None:
        return None, None
    t = re.sub(r"[¥\\,\s円]|JPY|税込|税抜|\(.*?\)", "", s, flags=re.I)
    t = t.rstrip("-")  # '40,645-' 形式
    neg = t.startswith(("△", "▲"))
    t = t.lstrip("△▲")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None, f"数値として解釈できません: '{raw}'"
    try:
        v = Decimal(t)
    except InvalidOperation:
        return None, f"数値として解釈できません: '{raw}'"
    return (-v if neg else v), None


# ------------------------------------------------------------------ dates
_DATE_PATTERNS = [
    (r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", "western"),
    (r"(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", "western"),
    (r"(令和|平成)\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", "era"),
    (r"\b([RH])\s*(\d{1,2})[./\-](\d{1,2})[./\-](\d{1,2})", "era"),
    (r"(\d{8})", "compact"),
]


def parse_date(raw) -> tuple[date | None, str | None]:
    """西暦/和暦/区切り違いを DATE へ。'必着' 等の付記は無視。年の無い日付は推測しない"""
    s = nfkc(raw)
    if s is None:
        return None, None
    for pat, kind in _DATE_PATTERNS:
        m = re.search(pat, s)
        if not m:
            continue
        try:
            if kind == "western":
                y, mo, d = (int(x) for x in m.groups())
            elif kind == "era":
                era, ny, mo, d = m.groups()
                y = ERA_BASE[era] + (1 if ny == "元" else int(ny))
                mo, d = int(mo), int(d)
            else:
                v = m.group(1)
                y, mo, d = int(v[:4]), int(v[4:6]), int(v[6:])
            return date(y, mo, d), None
        except ValueError:
            return None, f"存在しない日付です: '{raw}'"
    return None, f"日付として解釈できません: '{raw}'"


# ------------------------------------------------------------------ names / codes
_CORP_TOKENS = ["株式会社", "有限会社", "合同会社", "(株)", "(有)", "(同)", "㈱", "㈲", "御中", "様", "殿", "co.,ltd.", "inc."]


def normalize_company(raw) -> str | None:
    s = nfkc(raw)
    if s is None:
        return None
    s = s.lower()
    for t in _CORP_TOKENS:
        s = s.replace(t.lower(), "")
    return re.sub(r"[\s・\.,]", "", s) or None


def normalize_code(raw) -> str | None:
    s = nfkc(raw)
    return re.sub(r"\s", "", s).upper() if s else None


def match_customer(raw_name, customers: list[dict]) -> tuple[str | None, str]:
    """取引先名を正式名称・別名で照合。完全一致（正規化後）→ 部分一致（一意の場合のみ）"""
    key = normalize_company(raw_name)
    if not key:
        return None, "NO_NAME"
    exact, partial = [], []
    for c in customers:
        names = [c["OFFICIAL_NAME"]] + list(c.get("NAME_ALIASES") or [])
        keys = {normalize_company(n) for n in names if n}
        if key in keys:
            exact.append(c)
        elif any(k and (k in key or key in k) and len(k) >= 3 for k in keys):
            partial.append(c)
    if len(exact) == 1:
        return exact[0]["CUSTOMER_ID"], "EXACT"
    if not exact and len(partial) == 1:
        return partial[0]["CUSTOMER_ID"], "PARTIAL"
    return None, "AMBIGUOUS" if (exact or partial) else "NOT_FOUND"


# ------------------------------------------------------------------ table columns -> rows
def columns_to_rows(table, columns: list[str]) -> tuple[list[dict], str | None]:
    """AI_EXTRACT の表（列ごとの配列）を行へ。長さ不一致は黙って補完せずエラーを返す"""
    if not isinstance(table, dict):
        return [], "明細表が抽出されていません"
    lengths = {c: len(table.get(c) or []) for c in columns}
    n = max(lengths.values()) if lengths else 0
    err = None
    if len(set(lengths.values())) > 1:
        err = "明細の列ごとの件数が一致しません: " + ", ".join(f"{c}={v}" for c, v in lengths.items())
    rows = []
    for i in range(n):
        rows.append({c: ((table.get(c) or [])[i] if i < lengths[c] else None) for c in columns})
    # 全列空の行（空白行）は除外
    rows = [r for r in rows if any(nfkc(v) for v in r.values())]
    return rows, err


# ------------------------------------------------------------------ master lookups
@dataclass
class Masters:
    customers: list[dict]
    products: dict  # code -> row
    cmap: dict  # (customer_id, code) -> row
    tax_rates: list[dict]

    def tax_rate(self, category, on: date | None) -> Decimal | None:
        cands = [t for t in self.tax_rates if t["TAX_CATEGORY"] == category
                 and (on is None or (t["VALID_FROM"] <= on and (t["VALID_TO"] is None or on <= t["VALID_TO"])))]
        if not cands:
            return None
        return Decimal(str(sorted(cands, key=lambda t: t["VALID_FROM"])[-1]["TAX_RATE"]))


def resolve_product(customer_id, customer_code, masters: Masters, manual_code=None) -> dict:
    """発注側コード → 自社商品。取引先別対応表 → 自社コード直接一致 → 未対応"""
    code = normalize_code(customer_code)
    m = None
    if manual_code:
        p = masters.products.get(manual_code)
        method, product_code, factor, contract = "MANUAL", manual_code if p else None, Decimal(1), None
        cm = masters.cmap.get((customer_id, code)) if code else None
        if cm and cm["PRODUCT_CODE"] == manual_code:
            factor = Decimal(str(cm["UNIT_CONVERSION_FACTOR"] or 1))
            contract = cm["CONTRACT_UNIT_PRICE"]
    elif code and customer_id and (m := masters.cmap.get((customer_id, code))) and m.get("IS_ACTIVE", True):
        method, product_code = "CUSTOMER_MAP", m["PRODUCT_CODE"]
        factor, contract = Decimal(str(m["UNIT_CONVERSION_FACTOR"] or 1)), m["CONTRACT_UNIT_PRICE"]
    elif code and code in masters.products:
        method, product_code, factor, contract = "OWN_CODE", code, Decimal(1), None
    else:
        return {"PRODUCT_CODE": None, "MAPPING_METHOD": "UNMAPPED", "UNIT_CONVERSION_FACTOR": None,
                "MASTER_UNIT_PRICE": None, "TAX_CATEGORY": None, "CUSTOMER_UNIT": None}
    p = masters.products.get(product_code) or {}
    if contract is not None:
        master_price = Decimal(str(contract))
    elif p.get("REFERENCE_PRICE") is not None:
        master_price = Decimal(str(p["REFERENCE_PRICE"])) * factor
    else:
        master_price = None
    return {"PRODUCT_CODE": product_code, "MAPPING_METHOD": method, "UNIT_CONVERSION_FACTOR": factor,
            "MASTER_UNIT_PRICE": master_price, "TAX_CATEGORY": p.get("TAX_CATEGORY"),
            "CUSTOMER_UNIT": (m or {}).get("CUSTOMER_UNIT") if method == "CUSTOMER_MAP" else p.get("STANDARD_UNIT"),
            "PRODUCT_ACTIVE": p.get("IS_ACTIVE", True)}


def yen_floor(v: Decimal) -> Decimal:
    return v.quantize(Decimal(1), rounding=ROUND_FLOOR)


def calc_totals(lines: list[dict], masters: Masters, on: date | None) -> tuple[Decimal | None, Decimal | None]:
    """税抜合計 = 明細金額の合計（未記載行は 数量×単価 を切り捨て）
    税額 = 税率ごとに合計して1回だけ切り捨て（適格請求書の端数処理ルール）"""
    by_rate: dict[Decimal, Decimal] = {}
    sub = Decimal(0)
    for l in lines:
        amt = l.get("LINE_AMOUNT")
        if amt is None and l.get("QUANTITY") is not None and l.get("UNIT_PRICE") is not None:
            amt = yen_floor(l["QUANTITY"] * l["UNIT_PRICE"])
        if amt is None:
            return None, None
        sub += amt
        rate = l.get("TAX_RATE")
        if rate is None:
            rate = masters.tax_rate("STANDARD", on)  # 未対応付け商品は標準税率で仮計算（検証でERROR）
        by_rate[rate] = by_rate.get(rate, Decimal(0)) + amt
    tax = sum((yen_floor(v * r) for r, v in by_rate.items() if r is not None), Decimal(0))
    return sub, tax


# ------------------------------------------------------------------ whole order
@dataclass
class NormalizedOrder:
    header: dict
    lines: list[dict]
    issues: list[dict] = field(default_factory=list)  # 標準化時に見つかった問題（検証へ引き継ぐ）


def normalize_order(response: dict, masters: Masters) -> NormalizedOrder:
    from extraction import HEADER_FIELDS, LINE_COLUMNS

    r = response or {}
    issues = []
    raw = {k: (r.get(k) if not isinstance(r.get(k), (dict, list)) else None) for k in HEADER_FIELDS}
    h = {f"RAW_{k.upper()}": raw[k] for k in HEADER_FIELDS}

    h["ORDER_NUMBER"] = normalize_code(raw["order_number"])
    for k in ("order_date", "delivery_date"):
        v, err = parse_date(raw[k])
        h[k.upper()] = v
        if err:
            issues.append({"rule_id": "DATE_FORMAT", "field": k, "message": err})
    for k in ("subtotal", "tax_amount", "total_amount"):
        v, err = parse_number(raw[k])
        h[k.upper()] = v
        if err:
            issues.append({"rule_id": "NUMBER_FORMAT", "field": k, "message": err})
    h["BUYER_NAME"] = nfkc(raw["buyer_name"])
    h["SUPPLIER_NAME"] = nfkc(raw["supplier_name"])
    h["DELIVERY_ADDRESS"] = nfkc(raw["delivery_address"])
    h["NOTES"] = nfkc(raw["notes"])
    h["CUSTOMER_ID"], h["CUSTOMER_MATCH"] = match_customer(raw["buyer_name"], masters.customers)

    rows, struct_err = columns_to_rows(r.get("order_lines"), list(LINE_COLUMNS))
    if struct_err:
        issues.append({"rule_id": "LINE_STRUCTURE", "field": "order_lines", "message": struct_err})
    lines = []
    for i, row in enumerate(rows, 1):
        l = {f"RAW_{k.upper()}": row.get(k) for k in LINE_COLUMNS}
        l["LINE_NO"] = i
        l["CUSTOMER_PRODUCT_CODE"] = normalize_code(row.get("product_code"))
        l["PRODUCT_NAME"] = nfkc(row.get("product_name"))
        l["SPECIFICATION"] = nfkc(row.get("specification"))
        l["UNIT"] = nfkc(row.get("unit"))
        for k in ("quantity", "unit_price", "line_amount"):
            v, err = parse_number(row.get(k))
            l[k.upper()] = v
            if err:
                issues.append({"rule_id": "NUMBER_FORMAT", "field": f"line{i}.{k}", "message": err})
        lines.append(apply_mapping(l, h["CUSTOMER_ID"], masters, h["ORDER_DATE"]))
    h["CALC_SUBTOTAL"], h["CALC_TAX_AMOUNT"] = calc_totals(lines, masters, h["ORDER_DATE"])
    return NormalizedOrder(h, lines, issues)


def apply_mapping(line: dict, customer_id, masters: Masters, on: date | None, manual_code=None) -> dict:
    res = resolve_product(customer_id, line.get("CUSTOMER_PRODUCT_CODE"), masters, manual_code)
    line = {**line, **{k: res.get(k) for k in ("PRODUCT_CODE", "MAPPING_METHOD", "UNIT_CONVERSION_FACTOR",
                                                "MASTER_UNIT_PRICE", "TAX_CATEGORY")}}
    line["_EXPECTED_UNIT"] = res.get("CUSTOMER_UNIT")
    line["_PRODUCT_ACTIVE"] = res.get("PRODUCT_ACTIVE", True)
    q, f = line.get("QUANTITY"), line.get("UNIT_CONVERSION_FACTOR")
    line["STANDARD_QUANTITY"] = q * f if q is not None and f is not None else None
    line["TAX_RATE"] = masters.tax_rate(res["TAX_CATEGORY"], on) if res.get("TAX_CATEGORY") else None
    return line
