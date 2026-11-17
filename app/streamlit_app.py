"""SnowVillage 受注デモ — 異なる書式のPDF発注書を Snowflake で受注データ化する"""
import streamlit as st

st.set_page_config(page_title="SnowVillage 受注取込", page_icon=":material/receipt_long:", layout="wide")

pages = [
    st.Page("app_pages/upload_page.py", title="発注書アップロード", icon=":material/upload_file:", default=True),
    st.Page("app_pages/review_page.py", title="AI抽出・受注確認", icon=":material/fact_check:"),
    st.Page("app_pages/orders_page.py", title="受注一覧", icon=":material/list_alt:"),
    st.Page("app_pages/audit_page.py", title="処理状況・監査", icon=":material/monitoring:"),
]
nav = st.navigation(pages)

with st.sidebar:
    st.markdown("**SnowVillage 文具販売**  \n受注取込デモ")
    st.caption("PDF → Internal Stage → Cortex AI_EXTRACT → 標準化 → 業務ルール検証 → 受注確定")
    from ui_common import current_user

    st.caption(f":material/person: {current_user()}")
    st.caption("※ すべて架空の企業・取引データです。日時は日本時間（JST）で表示")

nav.run()
