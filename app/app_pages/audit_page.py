import pandas as pd
import streamlit as st

from db import PDF_STAGE, T_AUDIT, T_RUNS, T_STATUS_HISTORY, query
from history import completeness, detect_gaps, list_documents, stage_file_set
from ui_common import session, status_text

st.title(":material/monitoring: 処理状況・監査")
s = session()
k = completeness(s)

c = st.columns(5)
c[0].metric("受付済みPDF", k["ACCEPTED"], help=f"受付試行 {k['ATTEMPTED']} 件（うち重複 {k['DUPLICATES']} 件は別計上）", border=True)
c[1].metric("処理成功（抽出成功）", k["EXTRACT_OK"], border=True)
c[2].metric("確認待ち", k["NEEDS_REVIEW"] + k["READY"], help=f"要修正 {k['NEEDS_REVIEW']} / 確定可能 {k['READY']}", border=True)
c[3].metric("処理失敗", k["FAILED"], border=True)
c[4].metric("確定済み受注", k["CONFIRMED"], border=True)

st.subheader("受付データの完全性")
st.caption("アプリで受付を試みたPDFを起点に、各段階を通過した件数を突き合わせます（アプリ外の発注書は対象外）。")
funnel = pd.DataFrame([
    ("受付試行（重複を除く）", k["ACCEPTED"]), ("ステージ保存に成功", k["STORED"]), ("AI抽出を実行", k["EXTRACT_RUN"]),
    ("AI抽出に成功", k["EXTRACT_OK"]), ("検証に合格（ERRORなし）", k["VALID_OK"]), ("受注確定", k["CONFIRMED"]),
], columns=["段階", "件数"])
left, right = st.columns([3, 2], gap="large")
with left:
    st.bar_chart(funnel, x="段階", y="件数", horizontal=True, sort=False, height=260)
with right:
    st.dataframe(pd.DataFrame([
        {"区分": "保存未確認", "件数": k["NOT_STORED"]}, {"区分": "未処理（受付済み・抽出前/抽出中）", "件数": k["UNPROCESSED"]},
        {"区分": "処理失敗", "件数": k["FAILED"]}, {"区分": "確認・修正待ち", "件数": k["NEEDS_REVIEW"]},
        {"区分": "確定可能（未確定）", "件数": k["READY"]}, {"区分": "重複受付", "件数": k["DUPLICATES"]},
        {"区分": "訂正版で置換済み（履歴）", "件数": k["SUPERSEDED"]}]), hide_index=True)

check_stage = st.toggle("ステージ上の原本実在も確認する（LIST を実行）", value=False)
try:
    files = stage_file_set(s, PDF_STAGE) if check_stage else None
except Exception as e:
    files = None
    st.warning(f"ステージ一覧を取得できませんでした: {e}")
gaps = detect_gaps(s, files)
if gaps:
    st.markdown(f"**処理が完了していない受付: {len(gaps)} 件**")
    st.dataframe(pd.DataFrame([{"受付日時": g["UPLOADED_AT"], "ファイル名": g["ORIGINAL_FILENAME"],
                                "状態": status_text(g["STATUS"]), "検出内容": g["PROBLEMS"], "受付ID": g["DOCUMENT_ID"]}
                               for g in gaps]), hide_index=True)
else:
    st.success("処理結果が欠けている受付はありません。", icon=":material/task_alt:")

st.subheader("ファイルごとの処理履歴")
docs = list_documents(s)
st.dataframe(pd.DataFrame([{
    "受付日時": d["UPLOADED_AT"], "ファイル名": d["ORIGINAL_FILENAME"], "状態": status_text(d["STATUS"]),
    "完全性": d["COMPLETENESS_STATE"], "抽出回数": d["RUNS_TOTAL"] or 0, "抽出成功": d["RUNS_SUCCEEDED"] or 0,
    "最終抽出": d["LAST_RUN_AT"], "最新検証": d["LAST_VALIDATION_LEVEL"], "確定日時": d["CONFIRMED_AT"],
    "受付者": d["UPLOADED_BY"], "受付ID": d["DOCUMENT_ID"]} for d in docs]), hide_index=True)

timeline = query(s, f"""SELECT h.CHANGED_AT AS "日時", d.ORIGINAL_FILENAME AS "ファイル名", h.FROM_STATUS AS "変更前",
    h.TO_STATUS AS "変更後", h.CHANGED_BY AS "実行者", h.NOTE AS "内容", h.DOCUMENT_ID AS "受付ID"
    FROM {T_STATUS_HISTORY} h JOIN SNOWVILLAGE_DB.SNOWVILLAGE_RAW.SNOWVILLAGE_DOCUMENTS d ON d.DOCUMENT_ID = h.DOCUMENT_ID
    ORDER BY h.CHANGED_AT DESC LIMIT 300""")
with st.expander("状態遷移タイムライン（受付・抽出・検証・確定）", icon=":material/timeline:"):
    st.dataframe(pd.DataFrame(timeline), hide_index=True)

with st.expander("操作監査ログ（修正前後の値・実行者）", icon=":material/history:"):
    st.dataframe(pd.DataFrame(query(s, f"""SELECT EVENT_AT AS "日時", USER_NAME AS "実行者", ACTION AS "操作",
        DOCUMENT_ID AS "受付ID", ORDER_ID AS "受注ID", FIELD_NAME AS "項目", BEFORE_VALUE AS "修正前", AFTER_VALUE AS "修正後"
        FROM {T_AUDIT} ORDER BY EVENT_AT DESC LIMIT 500""")), hide_index=True)

st.subheader("AI処理時間")
perf = query(s, f"""SELECT COUNT(*) AS RUNS, COUNT_IF(STATUS='SUCCEEDED') AS OK, COUNT_IF(STATUS='FAILED') AS NG,
    AVG(IFF(STATUS='SUCCEEDED', ELAPSED_MS, NULL))/1000 AS AVG_SEC, MAX(ELAPSED_MS)/1000 AS MAX_SEC FROM {T_RUNS}""")[0]
p = st.columns(4)
p[0].metric("AI_EXTRACT 実行回数", perf["RUNS"], border=True)
p[1].metric("成功 / 失敗", f"{perf['OK']} / {perf['NG']}", border=True)
p[2].metric("平均処理時間", f"{float(perf['AVG_SEC'] or 0):.1f} 秒", border=True)
p[3].metric("最大処理時間", f"{float(perf['MAX_SEC'] or 0):.1f} 秒", border=True)
st.caption("AI_EXTRACT のクレジット消費は SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY で "
           "Query ID（抽出の詳細に表示）と突き合わせて確認できます（管理者権限が必要・反映に遅延あり）。")
