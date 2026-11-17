from datetime import date

import pandas as pd
import streamlit as st

from db import load_json
from history import get_document, runs_for_document
from order_service import (confirm_correction, confirm_order, correction_targets, diff_orders, latest_order_id, load_order, process_document, run_validation, save_edits,
                           validation_results)
from validation import correction_eligible
from ui_common import (LEVEL_ICON, current_user, masters_cached, pdf_bytes, render_pdf_pages, select_document, session,
                       status_badge, yen)

st.title(":material/fact_check: AI抽出・受注確認")

doc_id = select_document()
if not doc_id:
    st.info("受付済みのPDFがありません。「発注書アップロード」から登録してください。", icon=":material/info:")
    st.stop()

s, user = session(), current_user()
doc = get_document(s, doc_id)
masters = masters_cached()

# ---------------------------------------------------------------- ステータス行
with st.container(horizontal=True, vertical_alignment="center"):
    status_badge(doc["STATUS"])
    st.caption(f"受付ID `{doc_id}` ・ {doc['ORIGINAL_FILENAME']} ・ {doc['FILE_SIZE_BYTES'] / 1024:,.1f} KB ・ "
               f"{doc['PDF_PAGE_COUNT'] or '-'} ページ ・ 受付 {doc['UPLOADED_AT']:%Y-%m-%d %H:%M} ({doc['UPLOADED_BY']})")

if doc["STATUS"] == "DUPLICATE":
    st.warning(f"この受付は重複ファイルです。元の受付 `{doc['DUPLICATE_OF']}` を処理してください。", icon=":material/content_copy:")
    if st.button("元の受付を開く", icon=":material/arrow_forward:"):
        st.session_state["selected_doc"] = doc["DUPLICATE_OF"]
        st.rerun()
    st.stop()
if doc["STATUS"] == "FAILED" and doc["LAST_ERROR"]:
    st.error(f"前回の処理が失敗しました: {doc['LAST_ERROR'][:400]}", icon=":material/error:")

order_id = latest_order_id(s, doc_id)
header, lines = load_order(s, order_id) if order_id else (None, [])

with st.container(horizontal=True, vertical_alignment="center"):
    can_extract = doc["STAGE_VERIFIED"] and doc["STATUS"] != "EXTRACTING"
    label = "AIで読み取り" if not order_id else "AIで再読み取り（新しい版を作成）"
    if st.button(label, type="primary" if not order_id else "secondary", icon=":material/auto_awesome:",
                 disabled=not can_extract):
        with st.status("Cortex AI_EXTRACT で発注書を読み取り中…", expanded=True) as stt:
            st.write("PDF を TO_FILE() で参照し、ヘッダー10項目と明細表を抽出しています")
            res = process_document(s, doc_id, user)
            if res["ok"]:
                stt.update(label=f"抽出・標準化・検証が完了しました（{res['elapsed_ms'] / 1000:.1f} 秒・検証 {res['level']}）",
                           state="complete")
            else:
                stt.update(label=f"処理に失敗しました（{res.get('stage')}）", state="error")
                st.write(res.get("error", ""))
        st.rerun()
    try:
        st.download_button("原本PDFをダウンロード", data=pdf_bytes(doc_id), file_name=doc["ORIGINAL_FILENAME"],
                           mime="application/pdf", icon=":material/download:")
    except Exception as e:
        st.caption(f":red[原本を取得できませんでした: {e}]")

if not order_id:
    st.info("「AIで読み取り」を押すと、発注情報を抽出して共通フォーマットに変換し、業務ルールで検証します。",
            icon=":material/touch_app:")
    st.stop()

if header["ORDER_STATUS"] == "CONFIRMED":
    st.success(f"この受注（版 {header['VERSION_NO']}）は {header['CONFIRMED_AT']:%Y-%m-%d %H:%M} に "
               f"{header['CONFIRMED_BY']} が確定しました。修正する場合は再読み取りで新しい版を作成します（確定済みデータは保持）。",
               icon=":material/verified:")
    if header.get("CORRECTION_OF_ORDER_ID"):
        st.info(f"訂正版として確定（置き換えた受注: {header['CORRECTION_OF_ORDER_ID']}）。理由: {header['CORRECTION_REASON']}",
                icon=":material/published_with_changes:")
if doc["STATUS"] == "SUPERSEDED":
    st.warning("この受付の確定受注は、訂正版によって置き換えられました（履歴として保持）。", icon=":material/history:")

# ---------------------------------------------------------------- 左: 原本 / 右: 抽出結果
left, right = st.columns([5, 7], gap="large")
with left:
    st.subheader("原本PDF")
    try:
        pages = render_pdf_pages(doc_id, pdf_bytes(doc_id))
        for i, img in enumerate(pages, 1):
            st.image(img, caption=f"{i} ページ")
        if not pages:
            st.caption("プレビューを表示できません。ダウンロードして確認してください。")
    except Exception as e:
        st.caption(f"プレビューを表示できません（{e}）。ダウンロードして確認してください。")

editable = header["ORDER_STATUS"] == "DRAFT"
customers = {c["CUSTOMER_ID"]: c["OFFICIAL_NAME"] for c in masters.customers}
products = {p: v["PRODUCT_NAME"] for p, v in masters.products.items()}

with right:
    st.subheader(f"標準化された受注データ（版 {header['VERSION_NO']}・{header['ORDER_STATUS']}）")
    with st.form("edit_form", border=False):
        with st.container(border=True):
            st.markdown("**受注ヘッダー**")
            c1, c2, c3 = st.columns(3)
            h_order_no = c1.text_input("発注番号", header["ORDER_NUMBER"] or "", disabled=not editable,
                                       help=f"発注書の表記: {header['RAW_ORDER_NUMBER']}")
            h_order_date = c2.date_input("発注日", header["ORDER_DATE"], disabled=not editable, format="YYYY/MM/DD",
                                         help=f"発注書の表記: {header['RAW_ORDER_DATE']}")
            h_deliv = c3.date_input("希望納品日", header["DELIVERY_DATE"], disabled=not editable, format="YYYY/MM/DD",
                                    help=f"発注書の表記: {header['RAW_DELIVERY_DATE']}")
            c4, c5 = st.columns([1, 2])
            cust_opts = [""] + list(customers)
            h_cust = c4.selectbox("取引先（マスタ照合）", cust_opts,
                                  index=cust_opts.index(header["CUSTOMER_ID"]) if header["CUSTOMER_ID"] in cust_opts else 0,
                                  format_func=lambda c: f"{c} {customers[c]}" if c else "（未照合）", disabled=not editable,
                                  help=f"発注元の表記: {header['RAW_BUYER_NAME']}")
            h_addr = c5.text_input("納品先", header["DELIVERY_ADDRESS"] or "", disabled=not editable)
            c6, c7, c8 = st.columns(3)
            h_sub = c6.number_input("税抜合計（記載）", value=float(header["SUBTOTAL"]) if header["SUBTOTAL"] is not None else None,
                                    step=1.0, format="%.0f", disabled=not editable, help=f"表記: {header['RAW_SUBTOTAL']}")
            h_tax = c7.number_input("消費税（記載）", value=float(header["TAX_AMOUNT"]) if header["TAX_AMOUNT"] is not None else None,
                                    step=1.0, format="%.0f", disabled=not editable, help=f"表記: {header['RAW_TAX_AMOUNT']}")
            h_tot = c8.number_input("税込合計（記載）", value=float(header["TOTAL_AMOUNT"]) if header["TOTAL_AMOUNT"] is not None else None,
                                    step=1.0, format="%.0f", disabled=not editable, help=f"表記: {header['RAW_TOTAL_AMOUNT']}")
            st.caption(f"発注元: {header['RAW_BUYER_NAME']} → {header['CUSTOMER_ID'] or '未照合'} ・ "
                       f"明細から再計算: 税抜 {yen(header['CALC_SUBTOTAL'])} / 消費税 {yen(header['CALC_TAX_AMOUNT'])}（税率マスタ）")
            h_notes = st.text_input("備考", header["NOTES"] or "", disabled=not editable)

        st.markdown("**受注明細**（発注側コード → 自社商品コードの対応を確認・修正できます）")
        df = pd.DataFrame([{
            "行": l["LINE_NO"], "発注側コード": l["CUSTOMER_PRODUCT_CODE"], "品名（発注書）": l["PRODUCT_NAME"],
            "規格": l["SPECIFICATION"], "数量": float(l["QUANTITY"]) if l["QUANTITY"] is not None else None,
            "単位": l["UNIT"], "単価": float(l["UNIT_PRICE"]) if l["UNIT_PRICE"] is not None else None,
            "金額": float(l["LINE_AMOUNT"]) if l["LINE_AMOUNT"] is not None else None,
            "自社商品コード": l["PRODUCT_CODE"] or "",
            "対応": {"CUSTOMER_MAP": "取引先対応表", "OWN_CODE": "自社コード", "MANUAL": "手動", "UNMAPPED": "未対応"}.get(
                l["MAPPING_METHOD"], l["MAPPING_METHOD"]),
            "標準数量": float(l["STANDARD_QUANTITY"]) if l["STANDARD_QUANTITY"] is not None else None,
            "マスタ/契約単価": float(l["MASTER_UNIT_PRICE"]) if l["MASTER_UNIT_PRICE"] is not None else None,
            "税率": float(l["TAX_RATE"]) if l["TAX_RATE"] is not None else None,
            "抽出値（金額）": l["RAW_LINE_AMOUNT"],
        } for l in lines])
        # 数値列は明示的に数値型にする（空欄が "None" と表示されないように）
        for _c in ("数量", "単価", "金額", "標準数量", "マスタ/契約単価", "税率"):
            if _c in df:
                df[_c] = pd.to_numeric(df[_c], errors="coerce")
        edited = st.data_editor(
            df, hide_index=True, key=f"lines_{order_id}", disabled=not editable,
            column_config={
                "行": st.column_config.NumberColumn(disabled=True, width="small"),
                "数量": st.column_config.NumberColumn(format="%.0f"),
                "単価": st.column_config.NumberColumn(format="yen"),
                "金額": st.column_config.NumberColumn(format="yen"),
                "自社商品コード": st.column_config.SelectboxColumn(options=[""] + list(products),
                                                                 help="未対応の場合は担当者が選択（手動対応付けとして記録）"),
                "対応": st.column_config.TextColumn(disabled=True),
                "標準数量": st.column_config.NumberColumn(disabled=True, format="%.0f"),
                "マスタ/契約単価": st.column_config.NumberColumn(disabled=True, format="yen"),
                "税率": st.column_config.NumberColumn(disabled=True, format="%.2f"),
                "抽出値（金額）": st.column_config.TextColumn(disabled=True),
            }) if len(df) else st.caption("明細がありません")
        submitted = st.form_submit_button("修正を保存して再検証", icon=":material/save:", disabled=not editable)

    if submitted and editable:
        hc = {"ORDER_NUMBER": h_order_no, "ORDER_DATE": h_order_date, "DELIVERY_DATE": h_deliv,
              "CUSTOMER_ID": h_cust or None, "DELIVERY_ADDRESS": h_addr, "SUBTOTAL": h_sub, "TAX_AMOUNT": h_tax,
              "TOTAL_AMOUNT": h_tot, "NOTES": h_notes}
        col_map = {"発注側コード": "CUSTOMER_PRODUCT_CODE", "品名（発注書）": "PRODUCT_NAME", "規格": "SPECIFICATION",
                   "数量": "QUANTITY", "単位": "UNIT", "単価": "UNIT_PRICE", "金額": "LINE_AMOUNT",
                   "自社商品コード": "PRODUCT_CODE"}
        lc = {}
        if isinstance(edited, pd.DataFrame):
            for _, row in edited.iterrows():
                orig = df[df["行"] == row["行"]].iloc[0]
                ch = {col_map[c]: (row[c] if row[c] != "" else None) for c in col_map
                      if not (pd.isna(orig[c]) and pd.isna(row[c])) and orig[c] != row[c]}
                if ch:
                    lc[int(row["行"])] = ch
        n = save_edits(s, order_id, user, header_changes=hc, line_changes=lc)
        level, _ = run_validation(s, order_id, user)
        st.toast(f"{n} 項目を修正し、再検証しました（{level}）", icon=":material/task_alt:")
        st.rerun()

    # ------------------------------------------------------------ 検証結果
    results = validation_results(s, order_id)
    st.markdown("**検証結果**")
    if not results or header["LAST_VALIDATION_LEVEL"] is None:
        st.info("修正後の再検証が必要です。", icon=":material/pending:")
    if results:
        cnt = {lv: sum(1 for r in results if r["LEVEL"] == lv) for lv in ("ERROR", "WARNING", "PASS")}
        m1, m2, m3 = st.columns(3)
        m1.metric("ERROR", cnt["ERROR"], border=True)
        m2.metric("WARNING", cnt["WARNING"], border=True)
        m3.metric("PASS", cnt["PASS"], border=True)
        rdf = pd.DataFrame([{"判定": r["LEVEL"], "ルール": r["RULE_NAME"], "対象": r["TARGET"], "内容": r["MESSAGE"]}
                            for r in results])
        st.dataframe(rdf.style.map(lambda v: {"ERROR": "color:#d33;font-weight:600", "WARNING": "color:#c77700",
                                               "PASS": "color:#1a7f37"}.get(v, ""), subset=["判定"]),
                     hide_index=True, height=min(38 * (len(rdf) + 1), 420))
        st.caption(f"検証日時 {results[0]['EXECUTED_AT']:%Y-%m-%d %H:%M:%S}")

    with st.container(horizontal=True, vertical_alignment="center"):
        if st.button("再検証", icon=":material/rule:", disabled=not editable):
            level, _ = run_validation(s, order_id, user)
            st.toast(f"再検証しました（{level}）")
            st.rerun()
        level = header["LAST_VALIDATION_LEVEL"]
        can_confirm = editable and level in ("PASS", "WARNING")
        if st.button("受注確定", type="primary", icon=":material/verified:", disabled=not can_confirm):
            r = confirm_order(s, order_id, user)
            (st.toast if r["ok"] else st.error)(r["message"])
            if r["ok"]:
                st.rerun()
        if editable and level == "ERROR":
            st.caption(":red[ERROR を解消するまで受注確定できません]")
        elif editable and level is None:
            st.caption(":orange[再検証してから確定してください]")

    # ------------------------------------------------------------ 訂正版として確定（発注番号重複のみが ERROR の場合）
    if editable and header["LAST_VALIDATION_LEVEL"] == "ERROR" and correction_eligible(results):
        targets = correction_targets(s, order_id)
        if targets:
            with st.container(border=True):
                st.markdown("**:material/published_with_changes: 訂正版として確定（元の確定受注を置き換え）**")
                st.caption("同じ発注番号の受注がすでに確定されています。取引先に訂正版であることを確認できた場合のみ、"
                           "元の確定受注を「置換済み」にしてこの内容で確定できます。元の受注は削除されず履歴に残ります。")
                for t in targets:
                    st.markdown(f"置き換え対象: 受注 `{t['ORDER_ID'][:8]}`（受付 `{t['DOCUMENT_ID'][:8]}`・版 {t['VERSION_NO']}・"
                                f"税込 {yen(t['TOTAL_AMOUNT'])}・{t['CONFIRMED_AT']:%Y-%m-%d %H:%M} {t['CONFIRMED_BY']} 確定）")
                    d = diff_orders(s, t["ORDER_ID"], order_id)
                    if d:
                        st.dataframe(pd.DataFrame([{"項目": x["field"], "確定済み（元）": x["before"], "訂正版": x["after"]}
                                                   for x in d]), hide_index=True)
                    else:
                        st.caption("内容の差分はありません")
                reason = st.text_area("置き換えの理由（必須・監査ログに記録）", key=f"corr_reason_{order_id}",
                                      placeholder="例: 2026/11/12 さくら文具商事 佐倉様より数量変更（20→30冊）の訂正版と電話で確認")
                ack = st.checkbox("元の確定受注を置き換えることを確認しました", key=f"corr_ack_{order_id}")
                if st.button("訂正版として確定", type="primary", icon=":material/published_with_changes:",
                             disabled=not (ack and reason.strip())):
                    r = confirm_correction(s, order_id, user, reason, expected_targets=[t["ORDER_ID"] for t in targets])
                    if r["ok"]:
                        st.toast(r["message"], icon=":material/verified:")
                        st.rerun()
                    else:
                        st.error(r["message"])

# ---------------------------------------------------------------- 抽出の詳細
with st.expander("AI抽出の詳細（実行履歴・スコア・応答JSON）", icon=":material/data_object:"):
    runs = runs_for_document(s, doc_id)
    st.dataframe(pd.DataFrame([{"実行": r["RUN_NO"], "結果": r["STATUS"], "開始": r["STARTED_AT"],
                                "処理時間(秒)": (r["ELAPSED_MS"] or 0) / 1000, "エラー": r["ERROR_MESSAGE"],
                                "Query ID": r["QUERY_ID"], "実行者": r["EXECUTED_BY"]} for r in runs]), hide_index=True)
    latest = next((r for r in reversed(runs) if r["RUN_ID"] == header["RUN_ID"]), None)
    if latest and latest["SCORES"]:
        sc = load_json(latest["SCORES"]) or {}
        st.markdown("抽出スコア（参考値。表は表全体の集約スコアで、各セルの正しさは保証しません）")
        st.bar_chart(pd.DataFrame({"項目": list(sc), "スコア": [v.get("score") for v in sc.values()]}),
                     x="項目", y="スコア", height=220)
    from db import T_RUNS, query
    raw = query(s, f"SELECT RAW_RESPONSE FROM {T_RUNS} WHERE RUN_ID = ?", [header["RUN_ID"]])
    if raw:
        st.json(load_json(raw[0]["RAW_RESPONSE"]), expanded=False)
