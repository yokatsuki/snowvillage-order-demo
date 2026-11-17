"""処理状況・状態遷移・監査ログ・完全性集計"""
from __future__ import annotations

from db import (T_AUDIT, T_DOCUMENTS, T_RUNS, T_STATUS_HISTORY, V_PIPELINE, execute, insert_row, new_id,
                query)

STATUSES = {
    "RECEIVING": "受付処理中（保存確認前）",
    "UPLOADED": "PDF受付・保存完了",
    "EXTRACTING": "AI抽出中",
    "NEEDS_REVIEW": "確認・修正待ち",
    "READY": "確定可能",
    "CONFIRMED": "受注確定済み",
    "FAILED": "技術的な処理失敗",
    "DUPLICATE": "重複ファイル",
    "SUPERSEDED": "訂正版で置換済み",
}


def _fmt(v):
    from decimal import Decimal

    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else format(v, "f")
    return str(v)


def log_audit(session, user, action, document_id=None, order_id=None, field=None, before=None, after=None,
              detail=None):
    insert_row(session, T_AUDIT, {
        "AUDIT_ID": new_id(), "USER_NAME": user, "ACTION": action, "DOCUMENT_ID": document_id,
        "ORDER_ID": order_id, "FIELD_NAME": field,
        "BEFORE_VALUE": None if before is None else _fmt(before),
        "AFTER_VALUE": None if after is None else _fmt(after), "DETAIL": detail,
    }, variant_cols=("DETAIL",))


def set_status(session, document_id, to_status, user, note=None, error=None, clear_error=False):
    assert to_status in STATUSES, to_status
    cur = query(session, f"SELECT STATUS FROM {T_DOCUMENTS} WHERE DOCUMENT_ID = ?", [document_id])
    from_status = cur[0]["STATUS"] if cur else None
    if error is not None:
        execute(session, f"""UPDATE {T_DOCUMENTS} SET STATUS = ?, STATUS_UPDATED_AT = CURRENT_TIMESTAMP(),
                LAST_ERROR = ? WHERE DOCUMENT_ID = ?""", [to_status, str(error)[:4000], document_id])
    elif clear_error:
        execute(session, f"""UPDATE {T_DOCUMENTS} SET STATUS = ?, STATUS_UPDATED_AT = CURRENT_TIMESTAMP(),
                LAST_ERROR = NULL WHERE DOCUMENT_ID = ?""", [to_status, document_id])
    else:
        execute(session, f"""UPDATE {T_DOCUMENTS} SET STATUS = ?, STATUS_UPDATED_AT = CURRENT_TIMESTAMP()
                WHERE DOCUMENT_ID = ?""", [to_status, document_id])
    insert_row(session, T_STATUS_HISTORY, {
        "EVENT_ID": new_id(), "DOCUMENT_ID": document_id, "FROM_STATUS": from_status, "TO_STATUS": to_status,
        "CHANGED_BY": user, "NOTE": (note or (str(error)[:500] if error else None)),
    })


def get_document(session, document_id):
    rows = query(session, f"SELECT * FROM {T_DOCUMENTS} WHERE DOCUMENT_ID = ?", [document_id])
    return rows[0] if rows else None


def list_documents(session, limit=500):
    return query(session, f"SELECT * FROM {V_PIPELINE} ORDER BY UPLOADED_AT DESC LIMIT {int(limit)}")


def status_history(session, document_id):
    return query(session, f"""SELECT CHANGED_AT, FROM_STATUS, TO_STATUS, CHANGED_BY, NOTE
        FROM {T_STATUS_HISTORY} WHERE DOCUMENT_ID = ? ORDER BY CHANGED_AT, EVENT_ID""", [document_id])


def audit_for_document(session, document_id):
    return query(session, f"""SELECT EVENT_AT, USER_NAME, ACTION, ORDER_ID, FIELD_NAME, BEFORE_VALUE, AFTER_VALUE
        FROM {T_AUDIT} WHERE DOCUMENT_ID = ? ORDER BY EVENT_AT""", [document_id])


def runs_for_document(session, document_id):
    return query(session, f"""SELECT RUN_ID, RUN_NO, STATUS, STARTED_AT, ENDED_AT, ELAPSED_MS, ERROR_MESSAGE,
        SCORES, QUERY_ID, EXECUTED_BY FROM {T_RUNS} WHERE DOCUMENT_ID = ? ORDER BY RUN_NO""", [document_id])


def completeness(session) -> dict:
    """受付記録を起点とした完全性の集計（重複受付はファイル母数から除外して別計上）"""
    r = query(session, f"""
        SELECT
          COUNT(*)                                             AS ATTEMPTED,
          COUNT_IF(STATUS = 'DUPLICATE')                       AS DUPLICATES,
          COUNT_IF(STATUS <> 'DUPLICATE')                      AS ACCEPTED,
          COUNT_IF(STATUS <> 'DUPLICATE' AND STAGE_VERIFIED)   AS STORED,
          COUNT_IF(STATUS <> 'DUPLICATE' AND NOT STAGE_VERIFIED) AS NOT_STORED,
          COUNT_IF(COALESCE(RUNS_TOTAL,0) > 0)                 AS EXTRACT_RUN,
          COUNT_IF(COALESCE(RUNS_SUCCEEDED,0) > 0)             AS EXTRACT_OK,
          COUNT_IF(LATEST_ORDER_ID IS NOT NULL AND LAST_VALIDATION_LEVEL IN ('PASS','WARNING')) AS VALID_OK,
          COUNT_IF(CONFIRMED_ORDER_ID IS NOT NULL)             AS CONFIRMED,
          COUNT_IF(STATUS = 'NEEDS_REVIEW')                    AS NEEDS_REVIEW,
          COUNT_IF(STATUS = 'READY')                           AS READY,
          COUNT_IF(STATUS = 'FAILED')                          AS FAILED,
          COUNT_IF(STATUS = 'SUPERSEDED')                      AS SUPERSEDED,
          COUNT_IF(STATUS IN ('UPLOADED','RECEIVING','EXTRACTING'))   AS UNPROCESSED
        FROM {V_PIPELINE}""")
    return {k: int(v or 0) for k, v in r[0].items()}


def detect_gaps(session, stage_files: set[str] | None = None) -> list[dict]:
    """処理結果が欠けている受付を検出。stage_files を渡すとステージ実在も突き合わせる"""
    rows = list_documents(session, limit=10000)
    gaps = []
    for d in rows:
        if d["STATUS"] in ("DUPLICATE", "SUPERSEDED"):
            continue  # 重複受付・訂正版で置き換え済みの受付は処理完了扱い
        problems = []
        if not d["STAGE_VERIFIED"]:
            problems.append("ステージ保存が未確認")
        if stage_files is not None and d["STAGE_VERIFIED"] and f"{d['DOCUMENT_ID']}/original.pdf" not in stage_files:
            problems.append("ステージ上に原本が存在しない")
        if d["STATUS"] in ("UPLOADED",) and not d["RUNS_TOTAL"]:
            problems.append("AI抽出が未実行")
        if d["STATUS"] == "EXTRACTING":
            problems.append("抽出中のまま（中断の可能性）")
        if d["STATUS"] == "FAILED":
            problems.append(f"処理失敗: {d['LAST_ERROR'] or ''}"[:200])
        if d["RUNS_SUCCEEDED"] and not d["LATEST_ORDER_ID"]:
            problems.append("抽出成功だが受注データなし")
        if d["STATUS"] in ("NEEDS_REVIEW", "READY"):
            problems.append("確認・確定待ち")
        if problems:
            gaps.append({**d, "PROBLEMS": " / ".join(problems)})
    return gaps


def stage_file_set(session, stage) -> set[str]:
    rows = session.sql(f"LIST @{stage}").collect()
    out = set()
    for r in rows:
        name = r["name"]  # 例: snowvillage_pdf_stage/<uuid>/original.pdf
        out.add(name.split("/", 1)[1] if "/" in name else name)
    return out
