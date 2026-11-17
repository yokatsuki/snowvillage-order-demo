"""Cortex AI_EXTRACT による発注書の構造化抽出

- PDF を TO_FILE() で参照し、公式の JSON Schema 形式（文字列/表）で抽出
- 明細は列ごとの文字列配列として返る → normalization.columns_to_rows で行に変換
- 応答 JSON は無加工で EXTRACTION_RUNS に保存（全実行を保持）
- PDF 中の文章はデータとしてのみ扱い、命令として解釈・実行しない
"""
from __future__ import annotations

import json
import time

from db import PDF_STAGE, T_DOCUMENTS, T_RUNS, execute, insert_row, load_json, new_id, query
from history import log_audit, set_status

HEADER_FIELDS = {
    "order_number": "発注番号。『発注番号』『注文No.』『PO番号』『注文番号』などの欄に記載された識別番号。記載がなければ空にする",
    "order_date": "発注日・注文日・日付の欄に記載された日付。書かれている表記のまま",
    "buyer_name": "発注元（注文者・発注者）の会社名。宛先（御中・様・殿の付いた会社）ではない方",
    "supplier_name": "発注先（宛先）の会社名。『御中』『様』『殿』が付いた会社",
    "delivery_date": "希望納品日・希望納期・納期の日付。書かれている表記のまま",
    "delivery_address": "納品先・お届け先・納入場所の住所や名称",
    "subtotal": "税抜合計・小計・税抜計の金額。記載どおりの数字",
    "tax_amount": "消費税額の金額。記載どおりの数字",
    "total_amount": "税込合計・合計金額・総合計の金額。記載どおりの数字",
    "notes": "備考・特記事項欄の記載内容",
}
LINE_COLUMNS = {
    "line_number": "明細の行番号（No.、#、行）",
    "product_code": "商品コード・品番・コード",
    "product_name": "商品名・品名",
    "specification": "規格・仕様",
    "quantity": "数量・数",
    "unit": "単位",
    "unit_price": "単価",
    "line_amount": "明細の金額",
}


def response_format() -> dict:
    props = {k: {"type": "string", "description": v} for k, v in HEADER_FIELDS.items()}
    props["order_lines"] = {
        "type": "object",
        "description": "発注明細の表（商品ごとの行）。複数ページにまたがる場合はすべてのページの行を含める。空行・合計行は含めない",
        "column_ordering": list(LINE_COLUMNS.keys()),
        "properties": {k: {"type": "array", "description": v} for k, v in LINE_COLUMNS.items()},
    }
    return {"schema": {"type": "object", "properties": props}}


def call_ai_extract(session, stage_path: str) -> tuple[dict, str]:
    rf = json.dumps(response_format(), ensure_ascii=False)
    rows = session.sql(
        f"""SELECT AI_EXTRACT(file => TO_FILE('@{PDF_STAGE}', ?), responseFormat => PARSE_JSON(?),
                               scores => TRUE) AS R""", params=[stage_path, rf]).collect()
    qid = session.sql("SELECT LAST_QUERY_ID() AS Q").collect()[0]["Q"]
    return load_json(rows[0]["R"]), qid


def run_extraction(session, document_id: str, user: str, extractor=call_ai_extract) -> dict:
    """1回の抽出実行。結果（成功/失敗）は必ず EXTRACTION_RUNS に残す"""
    doc = query(session, f"SELECT * FROM {T_DOCUMENTS} WHERE DOCUMENT_ID = ?",
                [document_id])
    if not doc:
        raise ValueError("受付IDが存在しません")
    doc = doc[0]
    if doc["STATUS"] == "DUPLICATE":
        raise ValueError("重複受付のため抽出できません。元の受付IDを処理してください")
    if not doc["STAGE_VERIFIED"]:
        raise ValueError("ステージ保存が確認できていない受付です")

    run_no = query(session, f"SELECT COALESCE(MAX(RUN_NO),0)+1 AS N FROM {T_RUNS} WHERE DOCUMENT_ID = ?",
                   [document_id])[0]["N"]
    run_id = new_id()
    insert_row(session, T_RUNS, {"RUN_ID": run_id, "DOCUMENT_ID": document_id, "RUN_NO": run_no,
                                 "STATUS": "RUNNING", "RESPONSE_FORMAT": response_format(), "EXECUTED_BY": user},
               variant_cols=("RESPONSE_FORMAT",))
    set_status(session, document_id, "EXTRACTING", user, note=f"AI抽出 #{run_no} 開始")
    if doc["STATUS"] == "CONFIRMED":
        log_audit(session, user, "REPROCESS", document_id=document_id,
                  detail={"run_id": run_id, "note": "確定済み受付の再抽出（確定データは保持）"})

    t0 = time.time()
    try:
        result, qid = extractor(session, doc["STAGE_PATH"])
        elapsed = int((time.time() - t0) * 1000)
        err = (result or {}).get("error") if isinstance(result, dict) else "応答形式が不正"
        if err or not isinstance(result, dict) or not isinstance(result.get("response"), dict):
            raise RuntimeError(f"AI_EXTRACT がエラーを返しました: {err or result}")
        scores = (result.get("scoring") or {}).get("scores")
        execute(session, f"""UPDATE {T_RUNS} SET STATUS='SUCCEEDED', RAW_RESPONSE=PARSE_JSON(?), SCORES=PARSE_JSON(?),
                QUERY_ID=?, ELAPSED_MS=?, ENDED_AT=CURRENT_TIMESTAMP() WHERE RUN_ID=?""",
                [json.dumps(result, ensure_ascii=False), json.dumps(scores, ensure_ascii=False), qid, elapsed, run_id])
        log_audit(session, user, "EXTRACT", document_id=document_id, detail={"run_id": run_id, "elapsed_ms": elapsed})
        return {"ok": True, "run_id": run_id, "run_no": run_no, "result": result, "elapsed_ms": elapsed,
                "query_id": qid}
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        msg = str(e)[:4000]
        execute(session, f"""UPDATE {T_RUNS} SET STATUS='FAILED', ERROR_MESSAGE=?, ELAPSED_MS=?,
                ENDED_AT=CURRENT_TIMESTAMP() WHERE RUN_ID=?""", [msg, elapsed, run_id])
        set_status(session, document_id, "FAILED", user, error=f"AI抽出失敗(#{run_no}): {msg}")
        log_audit(session, user, "EXTRACT_FAILED", document_id=document_id, detail={"run_id": run_id, "error": msg})
        return {"ok": False, "run_id": run_id, "run_no": run_no, "error": msg, "elapsed_ms": elapsed}
