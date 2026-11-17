import pandas as pd
import streamlit as st

from db import T_HEADERS, T_LINES, V_CONFIRMED, V_PIPELINE, query
from history import audit_for_document, runs_for_document, status_history
from ui_common import masters_cached, pdf_bytes, session, status_text, yen

st.title(":material/list_alt: 受注一覧")
s = session()
masters = masters_cached()
customers = {c["CUSTOMER_ID"]: c["OFFICIAL_NAME"] for c in masters.customers}

rows = query(s, f"""SELECT p.DOCUMENT_ID, p.UPLOADED_AT, p.ORIGINAL_FILENAME, p.STATUS, p.LATEST_ORDER_ID,
       h.ORDER_NUMBER, h.CUSTOMER_ID, h.ORDER_DATE, h.TOTAL_AMOUNT, h.VERSION_NO, h.ORDER_STATUS,
       p.CONFIRMED_ORDER_ID, p.CONFIRMED_AT, p.LAST_VALIDATION_LEVEL
  FROM {V_PIPELINE} p LEFT JOIN {T_HEADERS} h ON h.ORDER_ID = p.LATEST_ORDER_ID
  ORDER BY p.UPLOADED_AT DESC""")
df = pd.DataFrame([{
    "受付日時": r["UPLOADED_AT"], "取引先": customers.get(r["CUSTOMER_ID"], "（未照合）" if r["LATEST_ORDER_ID"] else "-"),
    "発注番号": r["ORDER_NUMBER"], "発注日": r["ORDER_DATE"], "税込合計": float(r["TOTAL_AMOUNT"]) if r["TOTAL_AMOUNT"] is not None else None,
    "ステータス": status_text(r["STATUS"]), "最新検証": r["LAST_VALIDATION_LEVEL"], "版": r["VERSION_NO"],
    "ファイル名": r["ORIGINAL_FILENAME"], "受付ID": r["DOCUMENT_ID"], "_order": r["CONFIRMED_ORDER_ID"] or r["LATEST_ORDER_ID"],
} for r in rows])

if df.empty:
    st.info("まだ受付がありません。", icon=":material/info:")
    st.stop()

with st.container(horizontal=True, vertical_alignment="bottom"):
    f_status = st.multiselect("ステータス", sorted(df["ステータス"].unique()), placeholder="すべて")
    f_cust = st.multiselect("取引先", sorted(df["取引先"].unique()), placeholder="すべて")
view = df
if f_status:
    view = view[view["ステータス"].isin(f_status)]
if f_cust:
    view = view[view["取引先"].isin(f_cust)]

ev = st.dataframe(view.drop(columns=["_order"]), hide_index=True, on_select="rerun", selection_mode="single-row",
                  key="orders_table", column_config={
                      "受付日時": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
                      "税込合計": st.column_config.NumberColumn(format="yen")})
st.caption(f"{len(view)} 件（行を選択すると明細と処理履歴を表示）")

# 確定済み受注のCSV
confirmed = query(s, f"SELECT * FROM {V_CONFIRMED} ORDER BY CONFIRMED_AT DESC, ORDER_ID, LINE_NO")
st.download_button("確定済み受注をCSVでダウンロード", data=pd.DataFrame(confirmed).to_csv(index=False).encode("utf-8-sig"),
                   file_name="snowvillage_confirmed_orders.csv", mime="text/csv", icon=":material/download:",
                   disabled=not confirmed)

sel_rows = ev.selection.rows if ev and ev.selection else []
if not sel_rows:
    st.stop()
r = view.iloc[sel_rows[0]]
doc_id, order_id = r["受付ID"], r["_order"]

st.divider()
with st.container(horizontal=True, vertical_alignment="center"):
    st.subheader(f"{r['発注番号'] or '（発注番号なし）'} — {r['取引先']}")
    if st.button("この受付を確認画面で開く", icon=":material/open_in_new:"):
        st.session_state["selected_doc"] = doc_id
        st.switch_page("app_pages/review_page.py")
    try:
        st.download_button("原本PDF", data=pdf_bytes(doc_id), file_name=r["ファイル名"], mime="application/pdf",
                           icon=":material/picture_as_pdf:")
    except Exception as e:
        st.caption(f":red[原本を取得できません: {e}]")

tab_lines, tab_hist = st.tabs(["受注ヘッダー・明細", "処理履歴"])
with tab_lines:
    if order_id:
        h = query(s, f"SELECT * FROM {T_HEADERS} WHERE ORDER_ID = ?", [order_id])[0]
        c = st.columns(5)
        c[0].metric("発注日", str(h["ORDER_DATE"] or "-"), border=True)
        c[1].metric("希望納品日", str(h["DELIVERY_DATE"] or "-"), border=True)
        c[2].metric("税抜合計", yen(h["SUBTOTAL"]), border=True)
        c[3].metric("消費税", yen(h["TAX_AMOUNT"]), border=True)
        c[4].metric("税込合計", yen(h["TOTAL_AMOUNT"]), border=True)
        st.caption(f"受注ID `{order_id}` ・ 版 {h['VERSION_NO']} ・ {h['ORDER_STATUS']} ・ 納品先 {h['DELIVERY_ADDRESS'] or '-'}"
                   + (f" ・ 確定 {h['CONFIRMED_AT']:%Y-%m-%d %H:%M} {h['CONFIRMED_BY']}" if h["CONFIRMED_AT"] else ""))
        lines = query(s, f"""SELECT LINE_NO AS "行", CUSTOMER_PRODUCT_CODE AS "発注側コード", PRODUCT_CODE AS "自社商品コード",
            PRODUCT_NAME AS "品名", QUANTITY AS "数量", UNIT AS "単位", STANDARD_QUANTITY AS "標準数量", UNIT_PRICE AS "単価",
            LINE_AMOUNT AS "金額", MAPPING_METHOD AS "対応方法" FROM {T_LINES} WHERE ORDER_ID = ? ORDER BY LINE_NO""", [order_id])
        ldf = pd.DataFrame(lines)
        for _c in ("数量", "標準数量", "単価", "金額"):
            if _c in ldf:
                ldf[_c] = pd.to_numeric(ldf[_c], errors="coerce")
        st.dataframe(ldf, hide_index=True, column_config={
            "数量": st.column_config.NumberColumn(format="%.0f"), "標準数量": st.column_config.NumberColumn(format="%.0f"),
            "単価": st.column_config.NumberColumn(format="yen"), "金額": st.column_config.NumberColumn(format="yen")})
    else:
        st.caption("受注データはまだありません")
with tab_hist:
    st.markdown("**状態遷移**")
    st.dataframe(pd.DataFrame(status_history(s, doc_id)), hide_index=True)
    st.markdown("**AI抽出の実行履歴**")
    st.dataframe(pd.DataFrame(runs_for_document(s, doc_id)).drop(columns=["SCORES"], errors="ignore"), hide_index=True)
    st.markdown("**操作・修正の監査ログ**")
    st.dataframe(pd.DataFrame(audit_for_document(s, doc_id)), hide_index=True)
