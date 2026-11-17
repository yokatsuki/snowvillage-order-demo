"""PDF 受付: 実体検証 → 重複判定 → 受付記録 → put_stream → ステージ存在確認 → UPLOADED"""
from __future__ import annotations

import hashlib
import io

from db import PDF_STAGE, T_DOCUMENTS, execute, insert_row, new_id, query
from history import log_audit, set_status

MAX_BYTES = 20 * 1024 * 1024


def inspect_pdf(filename: str, data: bytes) -> tuple[bool, str, int | None]:
    """拡張子だけでなく PDF の実体（マジックナンバー・EOF・パース可否）を確認"""
    if not filename.lower().endswith(".pdf"):
        return False, "拡張子が .pdf ではありません", None
    if len(data) == 0:
        return False, "空のファイルです", None
    if len(data) > MAX_BYTES:
        return False, f"ファイルサイズが上限 {MAX_BYTES // 1024 // 1024}MB を超えています", None
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        return False, "PDF のヘッダー（%PDF-）がありません。PDF 以外のファイルです", None
    if b"%%EOF" not in data[-2048:]:
        return False, "PDF の終端（%%EOF）が見つかりません。破損している可能性があります", None
    pages = None
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(data)
        pages = len(doc)
        doc.close()
        if pages == 0:
            return False, "ページが含まれていません", 0
    except ImportError:
        pass
    except Exception as e:  # 暗号化・破損など
        return False, f"PDF として読み込めません: {e}", None
    return True, "OK", pages


def stage_path_for(document_id: str) -> str:
    # 元ファイル名はパスに使わない（一意性・文字種の問題を避ける）
    return f"{document_id}/original.pdf"


def verify_on_stage(session, path: str, expected_size: int) -> bool:
    rows = session.sql(f"LIST @{PDF_STAGE}/{path}").collect()
    return any(r["name"].endswith(path) and int(r["size"]) == expected_size for r in rows)


def find_by_hash(session, sha: str):
    rows = query(session, f"""SELECT DOCUMENT_ID, ORIGINAL_FILENAME, STATUS, UPLOADED_AT FROM {T_DOCUMENTS}
        WHERE SHA256 = ? AND STATUS NOT IN ('DUPLICATE') AND STAGE_VERIFIED ORDER BY UPLOADED_AT LIMIT 1""", [sha])
    return rows[0] if rows else None


def register_upload(session, filename: str, data: bytes, user: str) -> dict:
    ok, reason, pages = inspect_pdf(filename, data)
    if not ok:
        log_audit(session, user, "UPLOAD_REJECTED", detail={"filename": filename, "reason": reason, "size": len(data)})
        return {"result": "REJECTED", "message": reason, "filename": filename}

    sha = hashlib.sha256(data).hexdigest()
    doc_id = new_id()
    base = {"DOCUMENT_ID": doc_id, "ORIGINAL_FILENAME": filename, "FILE_SIZE_BYTES": len(data), "SHA256": sha,
            "PDF_PAGE_COUNT": pages, "UPLOADED_BY": user}

    dup = find_by_hash(session, sha)
    if dup:
        # 受付試行は記録するが、ファイル保存・受注作成は行わない
        insert_row(session, T_DOCUMENTS, {**base, "STATUS": "DUPLICATE", "DUPLICATE_OF": dup["DOCUMENT_ID"],
                                          "STAGE_VERIFIED": False})
        set_status(session, doc_id, "DUPLICATE", user, note=f"同一内容の受付 {dup['DOCUMENT_ID']} が存在")
        log_audit(session, user, "DUPLICATE", document_id=doc_id, detail={"duplicate_of": dup["DOCUMENT_ID"]})
        return {"result": "DUPLICATE", "document_id": doc_id, "duplicate_of": dup["DOCUMENT_ID"],
                "message": f"同一内容のPDFが受付済みです（受付ID {dup['DOCUMENT_ID']}・{dup['ORIGINAL_FILENAME']}）",
                "filename": filename}

    # 1) 保存前の受付記録（RECEIVING）: 途中で失敗しても記録は消えない
    path = stage_path_for(doc_id)
    insert_row(session, T_DOCUMENTS, {**base, "STAGE_PATH": path, "STATUS": "RECEIVING", "STAGE_VERIFIED": False})
    set_status(session, doc_id, "RECEIVING", user, note="受付記録作成")
    # 2) ステージへバイナリのまま保存
    try:
        session.file.put_stream(io.BytesIO(data), f"@{PDF_STAGE}/{path}", auto_compress=False, overwrite=False)
        stored = verify_on_stage(session, path, len(data))
    except Exception as e:
        set_status(session, doc_id, "FAILED", user, error=f"ステージ保存失敗: {e}")
        log_audit(session, user, "UPLOAD_FAILED", document_id=doc_id, detail={"error": str(e)[:1000]})
        return {"result": "FAILED", "document_id": doc_id, "message": f"ステージへの保存に失敗しました: {e}",
                "filename": filename}
    if not stored:
        set_status(session, doc_id, "FAILED", user, error="ステージ保存後の存在確認に失敗")
        return {"result": "FAILED", "document_id": doc_id, "message": "ステージ上でファイルを確認できませんでした",
                "filename": filename}
    # 3) 保存確認後に受付成功を記録
    execute(session, f"UPDATE {T_DOCUMENTS} SET STAGE_VERIFIED = TRUE WHERE DOCUMENT_ID = ?", [doc_id])
    set_status(session, doc_id, "UPLOADED", user, note="ステージ保存を確認", clear_error=True)
    log_audit(session, user, "UPLOAD", document_id=doc_id, detail={"filename": filename, "sha256": sha,
                                                                    "size": len(data), "pages": pages})
    return {"result": "UPLOADED", "document_id": doc_id, "message": "受付しました", "filename": filename,
            "pages": pages}


def download_pdf(session, document_id: str, attempts: int = 2) -> bytes:
    """原本PDFを取得。get_stream が失敗した場合はサーバー側プロシージャ経由で取得"""
    import base64
    import time

    for i in range(attempts):
        try:
            return session.file.get_stream(f"@{PDF_STAGE}/{stage_path_for(document_id)}").read()
        except Exception:
            time.sleep(1.0 * (i + 1))
    b64 = session.sql("CALL SNOWVILLAGE_DB.SNOWVILLAGE_APP.SNOWVILLAGE_READ_PDF(?)", params=[document_id]).collect()[0][0]
    return base64.b64decode(b64)
