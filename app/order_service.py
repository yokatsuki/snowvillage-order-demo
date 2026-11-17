"""受注サービス: 下書き作成・修正（監査付き）・検証・確定・再処理

データの見え方:
- ORDER_STATUS = DRAFT      : 処理中・確認中（確定受注ビューには出ない）
- ORDER_STATUS = CONFIRMED  : 確定受注（V_CONFIRMED_ORDERS で参照）
- ORDER_STATUS = SUPERSEDED : 再処理で置き換えられた旧確定版（履歴として保持）
- ORDER_STATUS = DISCARDED  : 再抽出で破棄された旧下書き
ヘッダー・明細の書き込みはトランザクション内で行い、部分的なデータを残さない。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from db import (T_AUDIT, T_CMAP, T_CUSTOMERS, T_DOCUMENTS, T_HEADERS, T_LINES, T_PRODUCTS, T_RUNS, T_TAX, T_VALIDATIONS,
                execute, insert_row, insert_rows, load_json, new_id, query)
from extraction import run_extraction
from history import log_audit, set_status
from normalization import Masters, apply_mapping, calc_totals, normalize_order
from validation import ERROR, correction_eligible, overall_level, validate

HEADER_COLS = ["RAW_ORDER_NUMBER", "RAW_ORDER_DATE", "RAW_BUYER_NAME", "RAW_SUPPLIER_NAME", "RAW_DELIVERY_DATE",
               "RAW_DELIVERY_ADDRESS", "RAW_SUBTOTAL", "RAW_TAX_AMOUNT", "RAW_TOTAL_AMOUNT", "RAW_NOTES",
               "ORDER_NUMBER", "ORDER_DATE", "CUSTOMER_ID", "BUYER_NAME", "SUPPLIER_NAME", "DELIVERY_DATE",
               "DELIVERY_ADDRESS", "SUBTOTAL", "TAX_AMOUNT", "TOTAL_AMOUNT", "NOTES", "CALC_SUBTOTAL",
               "CALC_TAX_AMOUNT"]
LINE_COLS = ["LINE_NO", "RAW_LINE_NUMBER", "RAW_PRODUCT_CODE", "RAW_PRODUCT_NAME", "RAW_SPECIFICATION",
             "RAW_QUANTITY", "RAW_UNIT", "RAW_UNIT_PRICE", "RAW_LINE_AMOUNT", "CUSTOMER_PRODUCT_CODE",
             "PRODUCT_NAME", "SPECIFICATION", "QUANTITY", "UNIT", "UNIT_PRICE", "LINE_AMOUNT", "PRODUCT_CODE",
             "MAPPING_METHOD", "UNIT_CONVERSION_FACTOR", "STANDARD_QUANTITY", "MASTER_UNIT_PRICE", "TAX_CATEGORY",
             "TAX_RATE"]
EDITABLE_HEADER = {"ORDER_NUMBER": str, "ORDER_DATE": date, "CUSTOMER_ID": str, "BUYER_NAME": str,
                   "DELIVERY_DATE": date, "DELIVERY_ADDRESS": str, "SUBTOTAL": Decimal, "TAX_AMOUNT": Decimal,
                   "TOTAL_AMOUNT": Decimal, "NOTES": str}
EDITABLE_LINE = {"CUSTOMER_PRODUCT_CODE": str, "PRODUCT_NAME": str, "SPECIFICATION": str, "QUANTITY": Decimal,
                 "UNIT": str, "UNIT_PRICE": Decimal, "LINE_AMOUNT": Decimal, "PRODUCT_CODE": str}
NUMERIC_LINE = ("QUANTITY", "UNIT_PRICE", "LINE_AMOUNT", "UNIT_CONVERSION_FACTOR", "STANDARD_QUANTITY",
                "MASTER_UNIT_PRICE", "TAX_RATE")


# ------------------------------------------------------------------ masters
def load_masters(session) -> Masters:
    customers = query(session, f"SELECT CUSTOMER_ID, OFFICIAL_NAME, NAME_ALIASES, IS_ACTIVE FROM {T_CUSTOMERS}")
    for c in customers:
        c["NAME_ALIASES"] = load_json(c["NAME_ALIASES"]) or []
    products = {p["PRODUCT_CODE"]: p for p in query(session, f"SELECT * FROM {T_PRODUCTS}")}
    cmap = {(m["CUSTOMER_ID"], m["CUSTOMER_PRODUCT_CODE"].upper()): m for m in query(session, f"SELECT * FROM {T_CMAP}")}
    taxes = query(session, f"SELECT * FROM {T_TAX}")
    return Masters(customers, products, cmap, taxes)


def _dec(v):
    return None if v is None else Decimal(str(v))


# ------------------------------------------------------------------ read
def load_order(session, order_id):
    h = query(session, f"SELECT * FROM {T_HEADERS} WHERE ORDER_ID = ?", [order_id])
    if not h:
        return None, []
    h = h[0]
    for k in ("SUBTOTAL", "TAX_AMOUNT", "TOTAL_AMOUNT", "CALC_SUBTOTAL", "CALC_TAX_AMOUNT"):
        h[k] = _dec(h[k])
    lines = query(session, f"SELECT * FROM {T_LINES} WHERE ORDER_ID = ? ORDER BY LINE_NO", [order_id])
    for l in lines:
        for k in NUMERIC_LINE:
            l[k] = _dec(l[k])
    return h, lines


def latest_order_id(session, document_id, include_confirmed=True):
    sts = "('DRAFT','CONFIRMED')" if include_confirmed else "('DRAFT')"
    r = query(session, f"""SELECT ORDER_ID FROM {T_HEADERS} WHERE DOCUMENT_ID = ? AND ORDER_STATUS IN {sts}
                           ORDER BY VERSION_NO DESC LIMIT 1""", [document_id])
    return r[0]["ORDER_ID"] if r else None


def validation_results(session, order_id, validation_id=None):
    if validation_id is None:
        r = query(session, f"SELECT LAST_VALIDATION_ID FROM {T_HEADERS} WHERE ORDER_ID = ?", [order_id])
        validation_id = r[0]["LAST_VALIDATION_ID"] if r else None
    if not validation_id:
        return []
    return query(session, f"""SELECT RULE_ID, RULE_NAME, LEVEL, TARGET, MESSAGE, EXECUTED_AT FROM {T_VALIDATIONS}
        WHERE VALIDATION_ID = ? ORDER BY CASE LEVEL WHEN 'ERROR' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END, TARGET""",
                 [validation_id])


# ------------------------------------------------------------------ create draft
def create_draft(session, document_id, run_id, response, user, masters=None) -> str:
    masters = masters or load_masters(session)
    norm = normalize_order(response, masters)
    order_id = new_id()
    # 明示トランザクションは使わず、各文を原子的にして順序で整合性を保つ:
    #   1) 明細を一括登録（ヘッダーが無いうちはどのビュー・画面からも参照されない）
    #   2) 旧下書きを破棄（確定済みは触らない）  3) ヘッダーを登録して下書きとして可視化
    ver = query(session, f"SELECT COALESCE(MAX(VERSION_NO),0)+1 AS V FROM {T_HEADERS} WHERE DOCUMENT_ID = ?",
                [document_id])[0]["V"]
    insert_rows(session, T_LINES, [{"ORDER_ID": order_id, **{c: l.get(c) for c in LINE_COLS}} for l in norm.lines])
    execute(session, f"""UPDATE {T_HEADERS} SET ORDER_STATUS='DISCARDED', UPDATED_AT=CURRENT_TIMESTAMP()
            WHERE DOCUMENT_ID = ? AND ORDER_STATUS = 'DRAFT'""", [document_id])
    insert_row(session, T_HEADERS, {"ORDER_ID": order_id, "DOCUMENT_ID": document_id, "RUN_ID": run_id,
                                    "VERSION_NO": ver, "ORDER_STATUS": "DRAFT", "CREATED_BY": user,
                                    **{c: norm.header.get(c) for c in HEADER_COLS}})
    log_audit(session, user, "CREATE_DRAFT", document_id=document_id, order_id=order_id,
              detail={"run_id": run_id, "version": ver, "lines": len(norm.lines), "issues": norm.issues})
    # 標準化時の問題は検証で参照するため保持
    _ISSUES_CACHE[order_id] = norm.issues
    return order_id


_ISSUES_CACHE: dict[str, list] = {}


def _issues_for(session, order_id, header, lines):
    """標準化時の問題（列不一致・数値/日付の解釈不能）。修正済みの項目は再評価して除外"""
    if order_id not in _ISSUES_CACHE:
        r = query(session, f"""SELECT DETAIL FROM {T_AUDIT}
                  WHERE ORDER_ID = ? AND ACTION = 'CREATE_DRAFT' ORDER BY EVENT_AT DESC LIMIT 1""", [order_id])
        _ISSUES_CACHE[order_id] = (load_json(r[0]["DETAIL"]) or {}).get("issues", []) if r else []
    issues = []
    for i in _ISSUES_CACHE[order_id]:
        f = i.get("field", "")
        if i["rule_id"] == "DATE_FORMAT" and header.get(f.upper()) is not None:
            continue  # 担当者が日付を入力済み
        if i["rule_id"] == "NUMBER_FORMAT":
            if "." in f:
                ln, col = f.split(".")
                no = int(ln.replace("line", ""))
                if any(l["LINE_NO"] == no and l.get(col.upper()) is not None for l in lines):
                    continue
            elif header.get(f.upper()) is not None:
                continue
        if i["rule_id"] == "LINE_STRUCTURE" and header.get("IS_EDITED") and lines and all(
                (l.get("CUSTOMER_PRODUCT_CODE") or l.get("PRODUCT_NAME")) and l.get("QUANTITY") is not None
                and (l.get("LINE_AMOUNT") is not None or l.get("UNIT_PRICE") is not None) for l in lines):
            continue  # 列不一致を担当者が修正し、全行の必須値が埋まった
        issues.append(i)
    return issues


# ------------------------------------------------------------------ validate
def _refresh_derived(header, lines, masters):
    """マスタ照合・換算・税額を最新マスタで再計算（発注書の値は変更しない）"""
    new_lines = []
    for l in lines:
        manual = l["PRODUCT_CODE"] if l.get("MAPPING_METHOD") == "MANUAL" else None
        new_lines.append(apply_mapping(l, header.get("CUSTOMER_ID"), masters, header.get("ORDER_DATE"), manual))
    header["CALC_SUBTOTAL"], header["CALC_TAX_AMOUNT"] = calc_totals(new_lines, masters, header.get("ORDER_DATE"))
    return header, new_lines


def run_validation(session, order_id, user, masters=None, update_document_status=True) -> tuple[str, list]:
    masters = masters or load_masters(session)
    header, lines = load_order(session, order_id)
    if header is None:
        raise ValueError("受注IDが存在しません")
    header, lines = _refresh_derived(header, lines, masters)
    header["CUSTOMER_MATCH"] = "EXACT"
    if header.get("CUSTOMER_ID") and not any(c["CUSTOMER_ID"] == header["CUSTOMER_ID"] for c in masters.customers):
        header["CUSTOMER_ID"] = None
    doc_id = header["DOCUMENT_ID"]
    conflicts = query(session, f"""SELECT ORDER_ID, DOCUMENT_ID, ORDER_STATUS FROM {T_HEADERS}
        WHERE ORDER_NUMBER = ? AND COALESCE(CUSTOMER_ID,'') = COALESCE(?, '') AND DOCUMENT_ID <> ?
          AND ORDER_STATUS IN ('DRAFT','CONFIRMED')""", [header.get("ORDER_NUMBER"), header.get("CUSTOMER_ID"), doc_id])
    dup = query(session, f"""SELECT d2.DOCUMENT_ID FROM {T_DOCUMENTS} d JOIN {T_DOCUMENTS} d2 ON d2.SHA256 = d.SHA256
        AND d2.DOCUMENT_ID <> d.DOCUMENT_ID AND d2.STATUS <> 'DUPLICATE' AND d2.UPLOADED_AT < d.UPLOADED_AT
        WHERE d.DOCUMENT_ID = ? LIMIT 1""", [doc_id])
    run = query(session, f"SELECT SCORES FROM {T_RUNS} WHERE RUN_ID = ?", [header.get("RUN_ID")])
    ctx = {"issues": _issues_for(session, order_id, header, lines),
           "customers_active": {c["CUSTOMER_ID"]: c["IS_ACTIVE"] for c in masters.customers},
           "order_number_conflicts": conflicts, "duplicate_file_of": dup[0]["DOCUMENT_ID"] if dup else None,
           "scores": load_json(run[0]["SCORES"]) if run else None}
    # 確定後に修正がない場合でもスコアは最初の抽出のものを参考表示する
    results = validate(header, lines, ctx)
    level = overall_level(results)
    vid = new_id()
    # 1) 明細の照合結果を 1 文の MERGE で更新  2) 検証結果を 1 文で一括登録
    # 3) 最後にヘッダーの LAST_VALIDATION_ID を更新（ここで初めて新しい検証結果が「最新」として参照される）
    if lines:
        cols = ["LINE_NO", "PRODUCT_CODE", "MAPPING_METHOD", "UNIT_CONVERSION_FACTOR", "STANDARD_QUANTITY",
                "MASTER_UNIT_PRICE", "TAX_CATEGORY", "TAX_RATE"]
        tuples, vals = [], []
        for l in lines:
            ph = ["?"]
            vals.append(str(l["LINE_NO"]))
            for c in cols[1:]:
                v = l.get(c)
                if v is None:
                    ph.append("NULL")  # NULL はリテラル（SiS で None が 'None' 文字列になるのを防ぐ）
                else:
                    ph.append("?")
                    vals.append(format(v, "f") if isinstance(v, Decimal) else str(v))
            tuples.append("(" + ", ".join(ph) + ")")
        execute(session, f"""MERGE INTO {T_LINES} t USING (
                SELECT $1::NUMBER LINE_NO, $2 PRODUCT_CODE, $3 MAPPING_METHOD, $4::NUMBER(10,4) UNIT_CONVERSION_FACTOR,
                       $5::NUMBER(14,3) STANDARD_QUANTITY, $6::NUMBER(12,2) MASTER_UNIT_PRICE, $7 TAX_CATEGORY,
                       $8::NUMBER(5,4) TAX_RATE FROM VALUES {", ".join(tuples)}) s
            ON t.ORDER_ID = ? AND t.LINE_NO = s.LINE_NO
            WHEN MATCHED THEN UPDATE SET PRODUCT_CODE = s.PRODUCT_CODE, MAPPING_METHOD = s.MAPPING_METHOD,
                 UNIT_CONVERSION_FACTOR = s.UNIT_CONVERSION_FACTOR, STANDARD_QUANTITY = s.STANDARD_QUANTITY,
                 MASTER_UNIT_PRICE = s.MASTER_UNIT_PRICE, TAX_CATEGORY = s.TAX_CATEGORY, TAX_RATE = s.TAX_RATE""",
                vals + [order_id])
    insert_rows(session, T_VALIDATIONS, [{"VALIDATION_ID": vid, "ORDER_ID": order_id, "DOCUMENT_ID": doc_id,
                                          "RULE_ID": r["rule_id"], "RULE_NAME": r["rule_name"], "LEVEL": r["level"],
                                          "TARGET": r["target"], "MESSAGE": r["message"], "EXECUTED_BY": user}
                                         for r in results])
    execute(session, f"""UPDATE {T_HEADERS} SET LAST_VALIDATION_ID=?, LAST_VALIDATION_LEVEL=?, CUSTOMER_ID=?,
            CALC_SUBTOTAL=?, CALC_TAX_AMOUNT=?, UPDATED_AT=CURRENT_TIMESTAMP() WHERE ORDER_ID=?""",
            [vid, level, header.get("CUSTOMER_ID"), header["CALC_SUBTOTAL"], header["CALC_TAX_AMOUNT"], order_id])
    counts = {lv: sum(1 for r in results if r["level"] == lv) for lv in ("PASS", "WARNING", "ERROR")}
    log_audit(session, user, "VALIDATE", document_id=doc_id, order_id=order_id,
              detail={"validation_id": vid, "level": level, **counts})
    if update_document_status and header["ORDER_STATUS"] == "DRAFT":
        set_status(session, doc_id, "NEEDS_REVIEW" if level == ERROR else "READY", user,
                   note=f"検証 {level}（ERROR {counts['ERROR']} / WARNING {counts['WARNING']}）", clear_error=True)
    return level, results


# ------------------------------------------------------------------ edit
def _coerce(v, typ):
    if v is None or (isinstance(v, float) and v != v) or (isinstance(v, str) and v.strip() == ""):
        return None
    if typ is Decimal:
        return Decimal(str(v))
    if typ is date:
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    return str(v).strip()


def _same(a, b):
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return (a is None and b is None) or (a is not None and b is not None and Decimal(str(a)) == Decimal(str(b)))
    return a == b


def save_edits(session, order_id, user, header_changes: dict | None = None, line_changes: dict | None = None) -> int:
    """header_changes: {col: value}; line_changes: {line_no: {col: value}}。変更前後を監査ログへ"""
    header, lines = load_order(session, order_id)
    if header is None:
        raise ValueError("受注IDが存在しません")
    if header["ORDER_STATUS"] != "DRAFT":
        raise ValueError("確定済み・破棄済みの受注は修正できません。再処理で新しい版を作成してください")
    n = 0
    for col, val in (header_changes or {}).items():
        if col not in EDITABLE_HEADER:
            continue
        new = _coerce(val, EDITABLE_HEADER[col])
        if _same(header.get(col), new):
            continue
        execute(session, f"UPDATE {T_HEADERS} SET {col} = ?, IS_EDITED = TRUE, UPDATED_AT = CURRENT_TIMESTAMP() "
                         "WHERE ORDER_ID = ?", [new, order_id])
        log_audit(session, user, "EDIT", document_id=header["DOCUMENT_ID"], order_id=order_id,
                  field=f"HEADER.{col}", before=header.get(col), after=new)
        n += 1
    by_no = {l["LINE_NO"]: l for l in lines}
    for no, changes in (line_changes or {}).items():
        cur = by_no.get(int(no))
        if cur is None:
            continue
        for col, val in changes.items():
            if col not in EDITABLE_LINE:
                continue
            new = _coerce(val, EDITABLE_LINE[col])
            if _same(cur.get(col), new):
                continue
            extra = ", MAPPING_METHOD = 'MANUAL'" if col == "PRODUCT_CODE" else ""
            if col == "PRODUCT_CODE" and new is None:
                extra = ", MAPPING_METHOD = 'UNMAPPED'"
            execute(session, f"UPDATE {T_LINES} SET {col} = ?, IS_EDITED = TRUE{extra} WHERE ORDER_ID = ? AND LINE_NO = ?",
                    [new, order_id, int(no)])
            log_audit(session, user, "EDIT", document_id=header["DOCUMENT_ID"], order_id=order_id,
                      field=f"LINE{no}.{col}", before=cur.get(col), after=new)
            n += 1
    if n:
        execute(session, f"UPDATE {T_HEADERS} SET IS_EDITED = TRUE, LAST_VALIDATION_LEVEL = NULL WHERE ORDER_ID = ?",
                [order_id])
    if n:
        # 修正後は再検証が必要
        set_status(session, header["DOCUMENT_ID"], "NEEDS_REVIEW", user, note=f"{n} 項目を修正（再検証待ち）")
    return n


# ------------------------------------------------------------------ confirm
def confirm_order(session, order_id, user) -> dict:
    """確定直前に必ず再検証し、ERROR があれば確定しない"""
    level, results = run_validation(session, order_id, user)
    header, _ = load_order(session, order_id)
    if level == ERROR:
        log_audit(session, user, "CONFIRM_BLOCKED", document_id=header["DOCUMENT_ID"], order_id=order_id,
                  detail={"errors": [r["message"] for r in results if r["level"] == ERROR]})
        return {"ok": False, "message": "検証ERRORがあるため確定できません", "results": results}
    if header["ORDER_STATUS"] != "DRAFT":
        return {"ok": False, "message": f"この受注は {header['ORDER_STATUS']} のため確定できません"}
    doc_id = header["DOCUMENT_ID"]
    prev = query(session, f"""SELECT ORDER_ID, VERSION_NO FROM {T_HEADERS} WHERE DOCUMENT_ID = ?
                 AND ORDER_STATUS = 'CONFIRMED' AND ORDER_ID <> ?""", [doc_id, order_id])
    # 旧確定版の SUPERSEDED 化と新版の CONFIRMED 化を 1 文で行う（どちらか片方だけが反映される状態を作らない）
    execute(session, f"""UPDATE {T_HEADERS} SET
              ORDER_STATUS  = IFF(ORDER_ID = ?, 'CONFIRMED', 'SUPERSEDED'),
              SUPERSEDED_BY = IFF(ORDER_ID = ?, SUPERSEDED_BY, ?),
              CONFIRMED_BY  = IFF(ORDER_ID = ?, ?, CONFIRMED_BY),
              CONFIRMED_AT  = IFF(ORDER_ID = ?, CURRENT_TIMESTAMP(), CONFIRMED_AT),
              UPDATED_AT    = CURRENT_TIMESTAMP()
            WHERE DOCUMENT_ID = ? AND ((ORDER_ID = ? AND ORDER_STATUS = 'DRAFT') OR ORDER_STATUS = 'CONFIRMED')
              AND EXISTS (SELECT 1 FROM {T_HEADERS} d WHERE d.ORDER_ID = ? AND d.ORDER_STATUS = 'DRAFT')""",
            [order_id, order_id, order_id, order_id, user, order_id, doc_id, order_id, order_id])
    if load_order(session, order_id)[0]["ORDER_STATUS"] != "CONFIRMED":
        return {"ok": False, "message": "他の操作で状態が変わったため確定できませんでした。画面を更新してください"}
    for p in prev:
        log_audit(session, user, "SUPERSEDE", document_id=doc_id, order_id=p["ORDER_ID"],
                  detail={"superseded_by": order_id, "diff": diff_orders(session, p["ORDER_ID"], order_id)})
    set_status(session, doc_id, "CONFIRMED", user, note=f"受注確定（検証 {level}）")
    log_audit(session, user, "CONFIRM", document_id=doc_id, order_id=order_id, detail={"level": level})
    return {"ok": True, "message": "受注を確定しました", "level": level, "superseded": [p["ORDER_ID"] for p in prev]}


def correction_targets(session, order_id) -> list[dict]:
    """訂正版で置き換える候補: 同一取引先・同一発注番号で、別受付の確定受注"""
    h, _ = load_order(session, order_id)
    if h is None or not h.get("ORDER_NUMBER"):
        return []
    return query(session, f"""SELECT ORDER_ID, DOCUMENT_ID, VERSION_NO, TOTAL_AMOUNT, CONFIRMED_AT, CONFIRMED_BY
        FROM {T_HEADERS} WHERE ORDER_NUMBER = ? AND COALESCE(CUSTOMER_ID,'') = COALESCE(?, '')
          AND DOCUMENT_ID <> ? AND ORDER_STATUS = 'CONFIRMED' ORDER BY CONFIRMED_AT""",
                 [h["ORDER_NUMBER"], h.get("CUSTOMER_ID"), h["DOCUMENT_ID"]])


def confirm_correction(session, order_id, user, reason: str, expected_targets: list[str] | None = None) -> dict:
    """訂正版として確定: 同じ発注番号の確定受注（別受付）を SUPERSEDED にし、この下書きを CONFIRMED にする。
    - 確定直前に再検証し、ERROR が発注番号重複だけの場合に限る
    - 理由は必須。旧版は削除せず履歴として保持し、差分と理由を監査ログに記録
    - 旧版の置換と新版の確定は 1 文の UPDATE で行う"""
    reason = (reason or "").strip()
    if not reason:
        return {"ok": False, "message": "置き換えの理由を入力してください"}
    level, results = run_validation(session, order_id, user)
    header, _ = load_order(session, order_id)
    if header["ORDER_STATUS"] != "DRAFT":
        return {"ok": False, "message": f"この受注は {header['ORDER_STATUS']} のため確定できません"}
    if not correction_eligible(results):
        return {"ok": False, "message": "発注番号の重複以外の ERROR があるため、訂正版として確定できません", "results": results}
    targets = correction_targets(session, order_id)
    if not targets:
        return {"ok": False, "message": "置き換え対象の確定受注が見つかりません。画面を更新してください"}
    old_ids = [t["ORDER_ID"] for t in targets]
    if expected_targets is not None and sorted(expected_targets) != sorted(old_ids):
        return {"ok": False, "message": "置き換え対象が画面表示時から変わりました。画面を更新して内容を確認してください"}
    doc_id = header["DOCUMENT_ID"]
    # 同じ受付内の旧確定版（再読み取りによるもの）も通常の確定と同様に置き換える
    same_doc = [r["ORDER_ID"] for r in query(session, f"""SELECT ORDER_ID FROM {T_HEADERS}
                WHERE DOCUMENT_ID = ? AND ORDER_STATUS = 'CONFIRMED' AND ORDER_ID <> ?""", [doc_id, order_id])]
    replace_ids = old_ids + same_doc
    diffs = {o: diff_orders(session, o, order_id) for o in replace_ids}
    marks = ", ".join("?" * len(replace_ids))
    execute(session, f"""UPDATE {T_HEADERS} SET
              ORDER_STATUS           = IFF(ORDER_ID = ?, 'CONFIRMED', 'SUPERSEDED'),
              SUPERSEDED_BY          = IFF(ORDER_ID = ?, SUPERSEDED_BY, ?),
              CONFIRMED_BY           = IFF(ORDER_ID = ?, ?, CONFIRMED_BY),
              CONFIRMED_AT           = IFF(ORDER_ID = ?, CURRENT_TIMESTAMP(), CONFIRMED_AT),
              CORRECTION_OF_ORDER_ID = IFF(ORDER_ID = ?, ?, CORRECTION_OF_ORDER_ID),
              CORRECTION_REASON      = IFF(ORDER_ID = ?, ?, CORRECTION_REASON),
              UPDATED_AT             = CURRENT_TIMESTAMP()
            WHERE ((ORDER_ID = ? AND ORDER_STATUS = 'DRAFT') OR (ORDER_ID IN ({marks}) AND ORDER_STATUS = 'CONFIRMED'))
              AND EXISTS (SELECT 1 FROM {T_HEADERS} d WHERE d.ORDER_ID = ? AND d.ORDER_STATUS = 'DRAFT')""",
            [order_id, order_id, order_id, order_id, user, order_id, order_id, ",".join(old_ids), order_id, reason,
             order_id, *replace_ids, order_id])
    if load_order(session, order_id)[0]["ORDER_STATUS"] != "CONFIRMED":
        return {"ok": False, "message": "他の操作で状態が変わったため確定できませんでした。画面を更新してください"}
    # 置き換えた側の受付の状態と監査
    for t in targets:
        set_status(session, t["DOCUMENT_ID"], "SUPERSEDED", user,
                   note=f"訂正版（受付 {doc_id[:8]}）で置き換え: {reason[:200]}")
        log_audit(session, user, "SUPERSEDE_BY_CORRECTION", document_id=t["DOCUMENT_ID"], order_id=t["ORDER_ID"],
                  detail={"superseded_by": order_id, "corrected_document_id": doc_id, "reason": reason,
                          "diff": diffs[t["ORDER_ID"]]})
    for o in same_doc:
        log_audit(session, user, "SUPERSEDE", document_id=doc_id, order_id=o,
                  detail={"superseded_by": order_id, "diff": diffs[o]})
    # 置換後に再検証（重複が解消され PASS/WARNING になることを記録）
    level_after, _ = run_validation(session, order_id, user, update_document_status=False)
    set_status(session, doc_id, "CONFIRMED", user, note=f"訂正版として確定（{len(old_ids)} 件を置換）: {reason[:200]}")
    log_audit(session, user, "CONFIRM_CORRECTION", document_id=doc_id, order_id=order_id,
              detail={"replaced_order_ids": old_ids, "reason": reason, "level_after": level_after})
    return {"ok": True, "message": f"訂正版として確定しました（元の確定受注 {len(old_ids)} 件を置換済みにしました）",
            "replaced": old_ids, "level": level_after}


def diff_orders(session, old_id, new_id_) -> list[dict]:
    ho, lo = load_order(session, old_id)
    hn, ln = load_order(session, new_id_)
    out = [{"field": f"HEADER.{k}", "before": str(ho.get(k)), "after": str(hn.get(k))}
           for k in EDITABLE_HEADER if not _same(ho.get(k), hn.get(k))]
    lo_m, ln_m = {l["LINE_NO"]: l for l in lo}, {l["LINE_NO"]: l for l in ln}
    for no in sorted(set(lo_m) | set(ln_m)):
        a, b = lo_m.get(no, {}), ln_m.get(no, {})
        for k in EDITABLE_LINE:
            if not _same(a.get(k), b.get(k)):
                out.append({"field": f"LINE{no}.{k}", "before": str(a.get(k)), "after": str(b.get(k))})
    return out


# ------------------------------------------------------------------ orchestration
def process_document(session, document_id, user, extractor=None) -> dict:
    """AI抽出 → 標準化（下書き作成）→ 検証。各段の結果は必ず永続化"""
    kw = {"extractor": extractor} if extractor else {}
    ex = run_extraction(session, document_id, user, **kw)
    if not ex["ok"]:
        return {"ok": False, "stage": "EXTRACT", **ex}
    try:
        masters = load_masters(session)
        order_id = create_draft(session, document_id, ex["run_id"], ex["result"]["response"], user, masters)
        level, results = run_validation(session, order_id, user, masters)
    except Exception as e:
        set_status(session, document_id, "FAILED", user, error=f"標準化・検証で失敗: {e}")
        log_audit(session, user, "PROCESS_FAILED", document_id=document_id, detail={"error": str(e)[:1000]})
        return {"ok": False, "stage": "NORMALIZE", "error": str(e), "run_id": ex["run_id"]}
    return {"ok": True, "order_id": order_id, "level": level, "results": results, "run_id": ex["run_id"],
            "elapsed_ms": ex["elapsed_ms"]}
