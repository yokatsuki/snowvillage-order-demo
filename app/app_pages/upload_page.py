import pandas as pd
import streamlit as st

from order_service import process_document
from ui_common import current_user, session
from upload import MAX_BYTES, register_upload

st.title(":material/upload_file: 発注書アップロード")
st.caption("取引先から届いたPDF発注書を受け付けます。PDFは受付ID単位で Snowflake 内部ステージへバイナリのまま保存されます。")

st.session_state.setdefault("uploader_gen", 0)
files = st.file_uploader("PDF発注書（複数可・1ファイル20MBまで）", type=["pdf"], accept_multiple_files=True,
                         key=f"uploader_{st.session_state.uploader_gen}")

if files:
    st.dataframe(pd.DataFrame([{"ファイル名": f.name, "サイズ(KB)": round(f.size / 1024, 1),
                                "判定": "上限超過" if f.size > MAX_BYTES else "OK"} for f in files]),
                 hide_index=True)
with st.container(horizontal=True, vertical_alignment="center"):
    run_ai = st.toggle("アップロード後に続けてAI読み取りを実行", value=False,
                       help="複数PDFを一括で受付→抽出→検証まで処理します")
    go = st.button("アップロード", type="primary", icon=":material/cloud_upload:", disabled=not files)

if go and files:
    user = current_user()
    results = []
    prog = st.progress(0.0, text="受付中…")
    for i, f in enumerate(files):
        try:
            r = register_upload(session(), f.name, f.getvalue(), user)
        except Exception as e:  # 想定外のエラーも画面に表示（受付記録は upload 側で保持）
            r = {"result": "FAILED", "filename": f.name, "message": str(e)}
        if run_ai and r["result"] == "UPLOADED":
            prog.progress((i + 0.5) / len(files), text=f"AI読み取り中: {f.name}")
            p = process_document(session(), r["document_id"], user)
            r["ai"] = (f"検証 {p['level']}" if p["ok"] else f"失敗: {p.get('error', '')[:80]}")
        results.append(r)
        prog.progress((i + 1) / len(files), text=f"{i + 1}/{len(files)} 件処理")
    prog.empty()
    st.session_state["last_upload_results"] = results
    st.session_state.uploader_gen += 1  # アップローダーをクリア
    ok = [r for r in results if r["result"] == "UPLOADED"]
    if ok:
        st.session_state["selected_doc"] = ok[0]["document_id"]
    st.rerun()

results = st.session_state.get("last_upload_results")
if results:
    st.subheader("受付結果")
    for r in results:
        with st.container(border=True):
            name = r.get("filename", "")
            if r["result"] == "UPLOADED":
                st.success(f"**{name}** を受付しました。受付ID: `{r['document_id']}`"
                           + (f"　（{r['ai']}）" if r.get("ai") else ""), icon=":material/check_circle:")
            elif r["result"] == "DUPLICATE":
                st.warning(f"**{name}**: {r['message']}。新しい受注は作成していません。", icon=":material/content_copy:")
            elif r["result"] == "REJECTED":
                st.error(f"**{name}**: 受け付けできません — {r['message']}", icon=":material/block:")
            else:
                st.error(f"**{name}**: {r['message']}" + (f"（受付ID `{r['document_id']}` に記録済み）" if r.get("document_id") else ""),
                         icon=":material/error:")
            target = r.get("duplicate_of") if r["result"] == "DUPLICATE" else r.get("document_id")
            if target and r["result"] in ("UPLOADED", "DUPLICATE"):
                label = "既存の受付を開く" if r["result"] == "DUPLICATE" else "AI抽出・受注確認へ"
                if st.button(label, key=f"go_{r.get('document_id')}", icon=":material/arrow_forward:"):
                    st.session_state["selected_doc"] = target
                    st.switch_page("app_pages/review_page.py")
    if st.button("結果をクリア", type="tertiary"):
        st.session_state.pop("last_upload_results", None)
        st.rerun()
