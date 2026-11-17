"""End-to-End ワークフローテスト（実 Snowflake / 実 AI_EXTRACT）

アプリと同じサービス層（upload / extraction / order_service / history）を呼び出し、
実テーブル・実ステージが更新されることを確認する。
  実行: SNOWVILLAGE_CONNECTION=default pytest tests/test_order_workflow.py -v
  ※ テストで作成した受付ID分のデータのみ終了時に削除（KEEP_TEST_DATA=1 で保持）

AI 抽出の精度（揺らぎあり）は test_ai_extraction_accuracy_* として分離し、
業務ロジック（決定論的）の合否と混同しない。
"""
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
USER = "pytest@snowvillage"
pytestmark = pytest.mark.skipif(os.environ.get("SNOWVILLAGE_SKIP_E2E") == "1", reason="E2E disabled")


@pytest.fixture(scope="module")
def session():
    from db import get_session
    s = get_session()
    yield s
    s.close()


@pytest.fixture(scope="module")
def created(session):
    ids = []
    yield ids
    if os.environ.get("KEEP_TEST_DATA") == "1" or not ids:
        return
    from db import PDF_STAGE, T_AUDIT, T_DOCUMENTS, T_HEADERS, T_LINES, T_RUNS, T_STATUS_HISTORY, T_VALIDATIONS, execute
    marks = ",".join("?" * len(ids))
    execute(session, f"DELETE FROM {T_LINES} WHERE ORDER_ID IN (SELECT ORDER_ID FROM {T_HEADERS} WHERE DOCUMENT_ID IN ({marks}))", ids)
    for t in (T_VALIDATIONS, T_HEADERS, T_RUNS, T_STATUS_HISTORY, T_AUDIT, T_DOCUMENTS):
        execute(session, f"DELETE FROM {t} WHERE DOCUMENT_ID IN ({marks})", ids)
    execute(session, f"DELETE FROM {T_AUDIT} WHERE USER_NAME = ? AND DOCUMENT_ID IS NULL", [USER])  # 受付拒否の記録
    for i in ids:
        session.sql(f"REMOVE @{PDF_STAGE}/{i}/").collect()


@pytest.fixture(scope="module")
def ctx():
    return {}


RUN_TAG = __import__("uuid").uuid4().hex[:12]


def _read(name):
    """テスト実行ごとに末尾へコメントを追加して別ハッシュにする（PDFとして有効・画面で受付済みのデータと衝突しない）"""
    return (SAMPLES / name).read_bytes() + f"\n% e2e-{RUN_TAG}\n%%EOF\n".encode()


def _doc(session, doc_id):
    from history import get_document
    return get_document(session, doc_id)


# ------------------------------------------------------------------ 0. 事前に同一ハッシュの受付があるとテストが成り立たない
def test_00_samples_not_already_registered(session):
    import hashlib
    from upload import find_by_hash
    for f in ("order_company_a.pdf", "order_company_b.pdf", "order_company_c.pdf", "order_company_a_revised.pdf"):
        assert find_by_hash(session, hashlib.sha256(_read(f)).hexdigest()) is None, f"{f} のテスト用コピーが既に存在"


def test_01_reject_non_pdf(session):
    from upload import register_upload
    r = register_upload(session, "fake.pdf", b"PK\x03\x04 this is a zip", USER)
    assert r["result"] == "REJECTED" and "PDF" in r["message"]
    r = register_upload(session, "note.txt", b"%PDF-1.4", USER)
    assert r["result"] == "REJECTED"


# ------------------------------------------------------------------ A: 正常系
def test_10_upload_a_saved_to_stage(session, created, ctx):
    from upload import register_upload, verify_on_stage
    data = _read("order_company_a.pdf")
    r = register_upload(session, "order_company_a.pdf", data, USER)
    assert r["result"] == "UPLOADED", r
    created.append(r["document_id"]); ctx["a"] = r["document_id"]
    d = _doc(session, r["document_id"])
    assert d["STATUS"] == "UPLOADED" and d["STAGE_VERIFIED"] and d["SHA256"]
    assert verify_on_stage(session, d["STAGE_PATH"], len(data))


def test_11_extract_a_and_validate(session, ctx):
    from order_service import load_order, process_document
    r = process_document(session, ctx["a"], USER)
    assert r["ok"], r
    assert r["level"] in ("PASS", "WARNING"), [x for x in r["results"] if x["level"] == "ERROR"]
    h, lines = load_order(session, r["order_id"])
    assert h["ORDER_NUMBER"] == "SKR-PO-2026-1105" and h["CUSTOMER_ID"] == "C001"
    assert [l["PRODUCT_CODE"] for l in lines] == ["SV-CP-A4", "SV-TN-K01", "SV-CF-A4", "SV-BP-BK"]
    assert _doc(session, ctx["a"])["STATUS"] == "READY"
    ctx["a_order"] = r["order_id"]


def test_12_confirm_a_and_listed(session, ctx):
    from db import V_CONFIRMED, query
    from order_service import confirm_order
    r = confirm_order(session, ctx["a_order"], USER)
    assert r["ok"], r
    rows = query(session, f"SELECT * FROM {V_CONFIRMED} WHERE ORDER_ID = ?", [ctx["a_order"]])
    assert len(rows) == 4 and _doc(session, ctx["a"])["STATUS"] == "CONFIRMED"


def test_13_reupload_a_is_duplicate(session, created, ctx):
    from db import T_HEADERS, query
    from upload import register_upload
    r = register_upload(session, "order_company_a_copy.pdf", _read("order_company_a.pdf"), USER)
    created.append(r["document_id"])
    assert r["result"] == "DUPLICATE" and r["duplicate_of"] == ctx["a"]
    assert not query(session, f"SELECT 1 FROM {T_HEADERS} WHERE DOCUMENT_ID = ?", [r["document_id"]])  # 受注は作らない


# ------------------------------------------------------------------ B: レイアウト差・コード変換
def test_20_b_layout_and_code_mapping(session, created, ctx):
    from order_service import load_order, process_document
    from upload import register_upload
    r = register_upload(session, "order_company_b.pdf", _read("order_company_b.pdf"), USER)
    assert r["result"] == "UPLOADED"; created.append(r["document_id"])
    p = process_document(session, r["document_id"], USER)
    assert p["ok"] and p["level"] != "ERROR", [x for x in p["results"] if x["level"] == "ERROR"]
    h, lines = load_order(session, p["order_id"])
    assert h["CUSTOMER_ID"] == "C002" and str(h["DELIVERY_DATE"]) == "2026-11-20"
    assert [l["CUSTOMER_PRODUCT_CODE"] for l in lines] == ["AL-1001", "AL-2050", "AL-3300", "AL-3410", "AL-3120"]
    assert [l["PRODUCT_CODE"] for l in lines] == ["SV-CP-A4", "SV-TN-K01", "SV-ST-10", "SV-FF-A4", "SV-CF-A4"]
    assert lines[0]["STANDARD_QUANTITY"] == Decimal(20)  # 4箱 × 5冊
    ctx["b_order"] = p["order_id"]


# ------------------------------------------------------------------ C: ERROR → 修正 → 確定
def test_30_c_error_blocks_confirm(session, created, ctx):
    from order_service import confirm_order, process_document
    from upload import register_upload
    r = register_upload(session, "order_company_c.pdf", _read("order_company_c.pdf"), USER)
    created.append(r["document_id"]); ctx["c"] = r["document_id"]
    p = process_document(session, r["document_id"], USER)
    assert p["ok"] and p["level"] == "ERROR"
    errs = {x["rule_id"] for x in p["results"] if x["level"] == "ERROR"}
    assert {"PRODUCT_MAPPING", "LINE_AMOUNT"} <= errs
    assert _doc(session, ctx["c"])["STATUS"] == "NEEDS_REVIEW"
    c = confirm_order(session, p["order_id"], USER)
    assert not c["ok"]
    ctx["c_order"] = p["order_id"]


def test_31_c_fix_revalidate_confirm(session, ctx):
    from db import T_AUDIT, query
    from order_service import confirm_order, load_order, run_validation, save_edits
    n = save_edits(session, ctx["c_order"], USER, line_changes={3: {"PRODUCT_CODE": "SV-CF-A4"}, 2: {"LINE_AMOUNT": 17600}})
    assert n == 2
    edits = query(session, f"SELECT FIELD_NAME, BEFORE_VALUE, AFTER_VALUE, USER_NAME FROM {T_AUDIT} WHERE ORDER_ID = ? AND ACTION='EDIT'",
                  [ctx["c_order"]])
    assert {e["FIELD_NAME"] for e in edits} == {"LINE3.PRODUCT_CODE", "LINE2.LINE_AMOUNT"}
    assert any(e["BEFORE_VALUE"] == "18600" and e["AFTER_VALUE"] == "17600" for e in edits)
    level, _ = run_validation(session, ctx["c_order"], USER)
    assert level != "ERROR"
    assert confirm_order(session, ctx["c_order"], USER)["ok"]
    _, lines = load_order(session, ctx["c_order"])
    assert lines[1]["RAW_LINE_AMOUNT"] == "18,600"  # 抽出時の値は保持


# ------------------------------------------------------------------ 同一発注番号・内容違い
def test_40_same_order_number_not_overwritten(session, created, ctx):
    from order_service import confirm_order, load_order, process_document
    from upload import register_upload
    r = register_upload(session, "order_company_a_revised.pdf", _read("order_company_a_revised.pdf"), USER)
    assert r["result"] == "UPLOADED"; created.append(r["document_id"])
    p = process_document(session, r["document_id"], USER)
    assert p["ok"] and "DUPLICATE_ORDER_NUMBER" in {x["rule_id"] for x in p["results"] if x["level"] == "ERROR"}
    assert not confirm_order(session, p["order_id"], USER)["ok"]
    ctx["rev"], ctx["rev_order"] = r["document_id"], p["order_id"]
    h, lines = load_order(session, ctx["a_order"])
    assert h["ORDER_STATUS"] == "CONFIRMED" and lines[0]["QUANTITY"] == Decimal(20)  # 元の確定値のまま


# ------------------------------------------------------------------ AI抽出失敗 → FAILED → 再処理
def test_50_extraction_failure_and_retry(session, created, ctx):
    import io
    from db import PDF_STAGE
    from extraction import call_ai_extract
    from history import runs_for_document
    from order_service import process_document
    from upload import register_upload

    data = _read("order_company_b.pdf")
    # ハッシュが変わるよう末尾にコメントを追加（PDFとして有効）
    data = data + b"\n% retry-test\n%%EOF\n"
    r = register_upload(session, "order_retry_test.pdf", data, USER)
    assert r["result"] == "UPLOADED"; created.append(r["document_id"])
    doc = _doc(session, r["document_id"])
    session.sql(f"REMOVE @{PDF_STAGE}/{doc['STAGE_PATH']}").collect()  # 障害注入: 原本消失
    p = process_document(session, r["document_id"], USER)
    assert not p["ok"] and _doc(session, r["document_id"])["STATUS"] == "FAILED"
    assert _doc(session, r["document_id"])["LAST_ERROR"]
    # 復旧して再処理
    session.file.put_stream(io.BytesIO(data), f"@{PDF_STAGE}/{doc['STAGE_PATH']}", auto_compress=False, overwrite=True)
    p2 = process_document(session, r["document_id"], USER, extractor=call_ai_extract)
    assert p2["ok"]
    runs = runs_for_document(session, r["document_id"])
    assert [x["STATUS"] for x in runs] == ["FAILED", "SUCCEEDED"]  # 両方の実行履歴が残る


def test_51_ai_error_field_marks_failed(session, created, ctx):
    from order_service import process_document
    p = process_document(session, ctx["c"], USER, extractor=lambda s, path: ({"error": "simulated", "response": None}, None))
    assert not p["ok"] and _doc(session, ctx["c"])["STATUS"] == "FAILED"


# ------------------------------------------------------------------ 確定済みの再処理: 旧確定は保持し、新版確定で SUPERSEDED
def test_60_reprocess_confirmed_keeps_history(session, ctx):
    from db import T_HEADERS, query
    from order_service import confirm_order, process_document
    p = process_document(session, ctx["a"], USER)
    assert p["ok"]
    rows = query(session, f"SELECT ORDER_ID, ORDER_STATUS FROM {T_HEADERS} WHERE DOCUMENT_ID = ? ORDER BY VERSION_NO", [ctx["a"]])
    assert [x["ORDER_STATUS"] for x in rows] == ["CONFIRMED", "DRAFT"]  # 旧確定は無断で上書きされない
    c = confirm_order(session, p["order_id"], USER)
    assert c["ok"] and c["superseded"] == [ctx["a_order"]]
    rows = query(session, f"SELECT ORDER_STATUS FROM {T_HEADERS} WHERE DOCUMENT_ID = ? ORDER BY VERSION_NO", [ctx["a"]])
    assert [x["ORDER_STATUS"] for x in rows] == ["SUPERSEDED", "CONFIRMED"]


# ------------------------------------------------------------------ 訂正版で確定済み受注を置き換える
def test_65_confirm_correction_replaces_confirmed(session, ctx):
    from db import T_AUDIT, V_CONFIRMED, query
    from history import get_document
    from order_service import confirm_correction, correction_targets, load_order
    targets = correction_targets(session, ctx["rev_order"])
    assert len(targets) == 1 and targets[0]["DOCUMENT_ID"] == ctx["a"]
    old_order = targets[0]["ORDER_ID"]
    assert not confirm_correction(session, ctx["rev_order"], USER, "  ")["ok"]          # 理由は必須
    assert not confirm_correction(session, ctx["rev_order"], USER, "x", expected_targets=["other"])["ok"]  # 対象が変わったら拒否
    r = confirm_correction(session, ctx["rev_order"], USER, "取引先に数量変更の訂正版と確認", expected_targets=[old_order])
    assert r["ok"], r
    h_old, _ = load_order(session, old_order)
    h_new, lines = load_order(session, ctx["rev_order"])
    assert h_old["ORDER_STATUS"] == "SUPERSEDED" and h_old["SUPERSEDED_BY"] == ctx["rev_order"]
    assert h_new["ORDER_STATUS"] == "CONFIRMED" and h_new["CORRECTION_OF_ORDER_ID"] == old_order
    assert h_new["LAST_VALIDATION_LEVEL"] in ("PASS", "WARNING")                       # 置換後は重複が解消
    assert lines[0]["QUANTITY"] == Decimal(30)
    assert get_document(session, ctx["a"])["STATUS"] == "SUPERSEDED"
    assert get_document(session, ctx["rev"])["STATUS"] == "CONFIRMED"
    conf = query(session, f"SELECT DISTINCT ORDER_ID FROM {V_CONFIRMED} WHERE ORDER_NUMBER = 'SKR-PO-2026-1105' AND DOCUMENT_ID IN (?, ?)",
                 [ctx["a"], ctx["rev"]])
    assert [c["ORDER_ID"] for c in conf] == [ctx["rev_order"]]                          # 確定受注は訂正版だけ
    acts = {a["ACTION"] for a in query(session, f"SELECT ACTION FROM {T_AUDIT} WHERE ORDER_ID IN (?, ?)", [old_order, ctx["rev_order"]])}
    assert {"SUPERSEDE_BY_CORRECTION", "CONFIRM_CORRECTION"} <= acts


# ------------------------------------------------------------------ 追跡性・完全性
def test_70_traceability_from_document_id(session, ctx):
    from history import audit_for_document, runs_for_document, status_history
    from order_service import latest_order_id, validation_results
    from upload import download_pdf
    assert download_pdf(session, ctx["c"])[:5] == b"%PDF-"
    assert runs_for_document(session, ctx["c"])
    assert [h["TO_STATUS"] for h in status_history(session, ctx["c"])][:2] == ["RECEIVING", "UPLOADED"]
    acts = {a["ACTION"] for a in audit_for_document(session, ctx["c"])}
    assert {"UPLOAD", "EXTRACT", "VALIDATE", "EDIT", "CONFIRM"} <= acts
    assert validation_results(session, latest_order_id(session, ctx["c"]) or ctx["c_order"])


def test_71_completeness_detects_gaps(session, created):
    from history import detect_gaps, stage_file_set
    from db import PDF_STAGE
    gaps = {g["DOCUMENT_ID"]: g["PROBLEMS"] for g in detect_gaps(session, stage_file_set(session, PDF_STAGE))}
    # FAILED にした受付C（test_51）と、発注番号重複で確定できない改訂版は検出される
    assert any("処理失敗" in v for v in gaps.values())
    assert any("確認・確定待ち" in v for v in gaps.values())


# ------------------------------------------------------------------ AI 抽出精度（揺らぎあり・参考）
@pytest.mark.parametrize("name", ["order_company_a.pdf", "order_company_b.pdf", "order_company_c.pdf"])
def test_ai_extraction_accuracy(session, name):
    """期待値（PDF生成元データ）と実抽出の一致率。90%以上を合格とし、結果を記録"""
    from db import PDF_STAGE
    from extraction import call_ai_extract
    import io
    exp = json.loads((SAMPLES / "expected_results.json").read_text(encoding="utf-8"))[name]
    path = f"_accuracy/{name}"
    session.file.put_stream(io.BytesIO((SAMPLES / name).read_bytes()), f"@{PDF_STAGE}/{path}", auto_compress=False, overwrite=True)
    try:
        r, _ = call_ai_extract(session, path)
    finally:
        session.sql(f"REMOVE @{PDF_STAGE}/{path}").collect()
    from normalization import normalize_code, parse_date, parse_number, nfkc
    resp = r["response"]
    checks = []
    h = exp["header"]
    checks.append(normalize_code(resp.get("order_number")) == h["order_number"])
    checks.append(str(parse_date(resp.get("order_date"))[0]) == h["order_date"])
    checks.append(str(parse_date(resp.get("delivery_date"))[0]) == h["delivery_date"])
    for k in ("subtotal", "tax_amount", "total_amount"):
        checks.append(parse_number(resp.get(k))[0] == h[k])
    t = resp.get("order_lines") or {}
    for i, l in enumerate(exp["lines"]):
        g = lambda c: (t.get(c) or [None] * 99)[i] if i < len(t.get(c) or []) else None
        checks.append(normalize_code(g("product_code")) == l["product_code"])
        checks.append(parse_number(g("quantity"))[0] == l["quantity"])
        checks.append(parse_number(g("unit_price"))[0] == l["unit_price"])
        checks.append(parse_number(g("line_amount"))[0] == l["line_amount"])
        checks.append(nfkc(g("unit")) == l["unit"])
    rate = sum(checks) / len(checks)
    print(f"[accuracy] {name}: {sum(checks)}/{len(checks)} = {rate:.1%}")
    assert rate >= 0.9
