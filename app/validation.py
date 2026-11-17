"""業務ルール検証（決定論的・純粋関数）

判定レベル:
  PASS    問題なし
  WARNING 確認推奨（確定は可能）
  ERROR   解消するまで確定禁止
AI抽出が成功したこと自体は合格条件にしない。スコアは参考値（WARNING判定のみに使用）。
"""
from __future__ import annotations

from decimal import Decimal

from normalization import yen_floor

PASS, WARNING, ERROR = "PASS", "WARNING", "ERROR"
LEVEL_ORDER = {PASS: 0, WARNING: 1, ERROR: 2}
RULE_NAMES = {
    "REQUIRED_FIELDS": "必須項目",
    "CUSTOMER_MATCH": "取引先照合",
    "PRODUCT_MAPPING": "商品照合",
    "QUANTITY": "数量検証",
    "UNIT": "単位検証",
    "LINE_STRUCTURE": "明細検証",
    "UNIT_PRICE": "単価照合",
    "LINE_AMOUNT": "金額検算",
    "SUBTOTAL": "合計検算（税抜）",
    "TAX": "合計検算（消費税）",
    "TOTAL": "合計検算（税込）",
    "DATE": "日付検証",
    "NUMBER_FORMAT": "数値形式",
    "DUPLICATE_FILE": "重複検証（ファイル）",
    "DUPLICATE_ORDER_NUMBER": "重複検証（発注番号）",
    "EXTRACTION_SCORE": "抽出スコア（参考）",
}
SCORE_WARN_THRESHOLD = 0.70
TAX_TOLERANCE = Decimal(1)  # 端数処理方法の違いとして許容する差（円）


def _r(rule, level, message, target="ヘッダー"):
    return {"rule_id": rule, "rule_name": RULE_NAMES[rule], "level": level, "target": target, "message": message}


def fmt(v):
    if v is None:
        return "（なし）"
    if isinstance(v, Decimal):
        return f"{v:,.0f}" if v == v.to_integral() else f"{v:,}"
    return str(v)


def validate(header: dict, lines: list[dict], context: dict | None = None) -> list[dict]:
    """context:
      issues: 標準化時の問題 [{rule_id, field, message}]
      customers_active: {customer_id: bool}
      order_number_conflicts: [{ORDER_ID, DOCUMENT_ID, ORDER_STATUS}]（同一取引先・同一発注番号の他受付）
      duplicate_file_of: 同一ハッシュの先行受付ID or None
      scores: AI_EXTRACT scoring.scores
    """
    ctx = context or {}
    out: list[dict] = []
    issues = ctx.get("issues") or []

    # --- 必須項目
    missing = [n for k, n in (("ORDER_NUMBER", "発注番号"), ("ORDER_DATE", "発注日"), ("BUYER_NAME", "発注元会社名"))
               if not header.get(k)]
    out.append(_r("REQUIRED_FIELDS", ERROR, "未設定: " + "、".join(missing)) if missing
               else _r("REQUIRED_FIELDS", PASS, "発注番号・発注日・発注元が存在します"))

    # --- 取引先照合
    cid = header.get("CUSTOMER_ID")
    if not cid:
        out.append(_r("CUSTOMER_MATCH", ERROR, f"発注元『{header.get('BUYER_NAME') or ''}』が取引先マスタに見つかりません"))
    elif ctx.get("customers_active", {}).get(cid) is False:
        out.append(_r("CUSTOMER_MATCH", ERROR, f"取引先 {cid} は無効（取引停止）です"))
    elif header.get("CUSTOMER_MATCH") == "PARTIAL":
        out.append(_r("CUSTOMER_MATCH", WARNING, f"取引先 {cid} に部分一致で対応付けました。表記を確認してください"))
    else:
        out.append(_r("CUSTOMER_MATCH", PASS, f"取引先 {cid} に一致"))

    # --- 日付
    date_issues = [i for i in issues if i["rule_id"] == "DATE_FORMAT"]
    for i in date_issues:
        lvl = ERROR if i["field"] == "order_date" else WARNING
        out.append(_r("DATE", lvl, i["message"], "発注日" if i["field"] == "order_date" else "希望納品日"))
    od, dd = header.get("ORDER_DATE"), header.get("DELIVERY_DATE")
    if od and dd and dd < od:
        out.append(_r("DATE", WARNING, f"希望納品日 {dd} が発注日 {od} より前です"))
    if not date_issues and not (od and dd and dd < od):
        out.append(_r("DATE", PASS, f"発注日 {od or '-'} / 希望納品日 {dd or '未記載'}"))

    # --- 数値形式
    num_issues = [i for i in issues if i["rule_id"] == "NUMBER_FORMAT"]
    for i in num_issues:
        out.append(_r("NUMBER_FORMAT", ERROR, i["message"], i["field"]))

    # --- 明細構造
    struct = [i for i in issues if i["rule_id"] == "LINE_STRUCTURE"]
    if not lines:
        out.append(_r("LINE_STRUCTURE", ERROR, "明細が1行もありません", "明細"))
    elif struct:
        for i in struct:
            out.append(_r("LINE_STRUCTURE", ERROR, i["message"], "明細"))
    else:
        out.append(_r("LINE_STRUCTURE", PASS, f"明細 {len(lines)} 行・列の件数が一致", "明細"))

    # --- 明細ごと
    for l in lines:
        t = f"明細{l['LINE_NO']}"
        code = l.get("CUSTOMER_PRODUCT_CODE")
        m = l.get("MAPPING_METHOD")
        if m == "UNMAPPED" or not l.get("PRODUCT_CODE"):
            out.append(_r("PRODUCT_MAPPING", ERROR, f"発注側商品コード『{code or '（なし）'}』{l.get('PRODUCT_NAME') or ''} が自社商品に対応付けられません", t))
        elif l.get("_PRODUCT_ACTIVE") is False:
            out.append(_r("PRODUCT_MAPPING", ERROR, f"自社商品 {l['PRODUCT_CODE']} は無効（取扱停止）です", t))
        elif m == "MANUAL":
            out.append(_r("PRODUCT_MAPPING", WARNING, f"『{code}』を担当者が {l['PRODUCT_CODE']} に手動で対応付けました", t))
        else:
            via = "取引先コード対応表" if m == "CUSTOMER_MAP" else "自社コード一致"
            out.append(_r("PRODUCT_MAPPING", PASS, f"『{code}』→ {l['PRODUCT_CODE']}（{via}）", t))

        q = l.get("QUANTITY")
        if q is None:
            out.append(_r("QUANTITY", ERROR, "数量がありません", t))
        elif q <= 0:
            out.append(_r("QUANTITY", ERROR, f"数量が正の数ではありません: {fmt(q)}", t))
        else:
            f = l.get("UNIT_CONVERSION_FACTOR")
            conv = f"（標準単位換算 {fmt(l.get('STANDARD_QUANTITY'))}）" if f is not None and f != 1 else ""
            out.append(_r("QUANTITY", PASS, f"数量 {fmt(q)} {l.get('UNIT') or ''}{conv}", t))

        exp_unit = l.get("_EXPECTED_UNIT")
        if exp_unit and l.get("UNIT") and l["UNIT"] != exp_unit:
            out.append(_r("UNIT", WARNING, f"単位『{l['UNIT']}』がマスタの単位『{exp_unit}』と異なります（換算係数を確認）", t))

        p, mp = l.get("UNIT_PRICE"), l.get("MASTER_UNIT_PRICE")
        if p is None:
            out.append(_r("UNIT_PRICE", WARNING, f"単価が記載されていません（マスタ/契約単価 {fmt(mp)} で要確認）", t))
        elif mp is not None and p != mp:
            out.append(_r("UNIT_PRICE", WARNING, f"記載単価 {fmt(p)} がマスタ/契約単価 {fmt(mp)} と異なります（発注書の値を保持）", t))
        elif mp is not None:
            out.append(_r("UNIT_PRICE", PASS, f"単価 {fmt(p)} はマスタ/契約単価と一致", t))

        a = l.get("LINE_AMOUNT")
        if q is not None and p is not None:
            calc = yen_floor(q * p)
            if a is None:
                out.append(_r("LINE_AMOUNT", WARNING, f"明細金額が未記載（数量×単価 = {fmt(calc)}）", t))
            elif a != calc:
                out.append(_r("LINE_AMOUNT", ERROR, f"数量×単価 = {fmt(calc)} ですが記載金額は {fmt(a)}（差 {fmt(a - calc)}）", t))
            else:
                out.append(_r("LINE_AMOUNT", PASS, f"{fmt(q)} × {fmt(p)} = {fmt(a)}", t))
        elif a is None:
            out.append(_r("LINE_AMOUNT", ERROR, "明細金額も単価もないため金額を確定できません", t))

    # --- 合計
    cs, ct = header.get("CALC_SUBTOTAL"), header.get("CALC_TAX_AMOUNT")
    s, tx, tot = header.get("SUBTOTAL"), header.get("TAX_AMOUNT"), header.get("TOTAL_AMOUNT")
    if cs is None:
        out.append(_r("SUBTOTAL", ERROR, "明細金額が不足しているため税抜合計を計算できません"))
    elif s is None:
        out.append(_r("SUBTOTAL", WARNING, f"税抜合計の記載がありません（明細合計 {fmt(cs)}）"))
    elif s != cs:
        out.append(_r("SUBTOTAL", ERROR, f"明細合計 {fmt(cs)} と記載の税抜合計 {fmt(s)} が一致しません（差 {fmt(s - cs)}）"))
    else:
        out.append(_r("SUBTOTAL", PASS, f"明細合計 = 税抜合計 {fmt(s)}"))

    if ct is not None:
        if tx is None:
            out.append(_r("TAX", WARNING, f"消費税額の記載がありません（税率マスタによる計算値 {fmt(ct)}）"))
        elif tx == ct:
            out.append(_r("TAX", PASS, f"消費税 {fmt(tx)}（税率マスタ・税率ごと切り捨て）"))
        elif abs(tx - ct) <= TAX_TOLERANCE:
            out.append(_r("TAX", WARNING, f"消費税 {fmt(tx)} が計算値 {fmt(ct)} と端数分だけ異なります（端数処理の違い）"))
        else:
            out.append(_r("TAX", ERROR, f"消費税 {fmt(tx)} が計算値 {fmt(ct)} と一致しません"))

    base = s if s is not None else cs
    tax_base = tx if tx is not None else ct
    if tot is None:
        out.append(_r("TOTAL", WARNING, f"税込合計の記載がありません（計算値 {fmt(base + tax_base) if base is not None and tax_base is not None else '-'}）"))
    elif base is not None and tax_base is not None:
        if tot != base + tax_base:
            out.append(_r("TOTAL", ERROR, f"税抜合計 {fmt(base)} + 消費税 {fmt(tax_base)} = {fmt(base + tax_base)} ですが税込合計は {fmt(tot)}"))
        else:
            out.append(_r("TOTAL", PASS, f"{fmt(base)} + {fmt(tax_base)} = {fmt(tot)}"))

    # --- 重複
    dup_file = ctx.get("duplicate_file_of")
    out.append(_r("DUPLICATE_FILE", ERROR, f"同一内容のファイルが受付 {dup_file} に存在します") if dup_file
               else _r("DUPLICATE_FILE", PASS, "同一内容のファイルはありません"))
    conflicts = ctx.get("order_number_conflicts") or []
    confirmed = [c for c in conflicts if c["ORDER_STATUS"] == "CONFIRMED"]
    drafts = [c for c in conflicts if c["ORDER_STATUS"] == "DRAFT"]
    if confirmed:
        out.append(_r("DUPLICATE_ORDER_NUMBER", ERROR,
                      f"発注番号 {header.get('ORDER_NUMBER')} は別受付（{', '.join(c['DOCUMENT_ID'][:8] for c in confirmed)}）で確定済みです。"
                      "既存の確定受注は上書きしません。訂正版の場合は取引先へ確認してください"))
    elif drafts:
        out.append(_r("DUPLICATE_ORDER_NUMBER", WARNING,
                      f"同じ発注番号の未確定受注が別受付（{', '.join(c['DOCUMENT_ID'][:8] for c in drafts)}）にあります"))
    elif header.get("ORDER_NUMBER"):
        out.append(_r("DUPLICATE_ORDER_NUMBER", PASS, "発注番号の重複はありません"))

    # --- 抽出スコア（参考値。単独で合否を決めない）
    scores = ctx.get("scores") or {}
    low = {k: v.get("score") for k, v in scores.items() if isinstance(v, dict) and v.get("score") is not None
           and v["score"] < SCORE_WARN_THRESHOLD}
    if low:
        out.append(_r("EXTRACTION_SCORE", WARNING, "抽出スコアが低い項目: " + ", ".join(f"{k}={v:.2f}" for k, v in low.items())))
    elif scores:
        out.append(_r("EXTRACTION_SCORE", PASS, f"全項目のスコア ≥ {SCORE_WARN_THRESHOLD}（参考値）"))
    return out


def overall_level(results: list[dict]) -> str:
    return max((r["level"] for r in results), key=LEVEL_ORDER.get, default=PASS)


CORRECTABLE_RULES = {"DUPLICATE_ORDER_NUMBER"}


def correction_eligible(results: list[dict]) -> bool:
    """訂正版として確定できるか: ERROR が「確定済み発注番号との重複」だけの場合のみ"""
    errs = [r for r in results if (r.get("level") or r.get("LEVEL")) == ERROR]
    return bool(errs) and all((r.get("rule_id") or r.get("RULE_ID")) in CORRECTABLE_RULES for r in errs)
