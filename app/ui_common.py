"""画面共通: セッション・利用者・マスタキャッシュ・表示ヘルパー"""
from __future__ import annotations

import streamlit as st

from db import get_session, query

STATUS_LABEL = {
    "RECEIVING": ("受付処理中", "gray"), "UPLOADED": ("受付済み", "blue"), "EXTRACTING": ("AI抽出中", "violet"),
    "NEEDS_REVIEW": ("確認待ち", "orange"), "READY": ("確定可能", "green"), "CONFIRMED": ("確定済み", "primary"),
    "FAILED": ("処理失敗", "red"), "DUPLICATE": ("重複", "gray"),
    "SUPERSEDED": ("訂正版で置換済み", "gray"),
}
LEVEL_ICON = {"PASS": ":green[:material/check_circle: PASS]", "WARNING": ":orange[:material/warning: WARNING]",
              "ERROR": ":red[:material/error: ERROR]"}


def session():
    if "_sf_session" not in st.session_state:
        st.session_state["_sf_session"] = get_session()
    return st.session_state["_sf_session"]


def current_user() -> str:
    """st.user（SiS 公式対応）を優先し、取得できない場合は Snowflake の CURRENT_USER()"""
    if "_user" not in st.session_state:
        name = None
        try:
            u = st.user
            name = u.get("user_name") or u.get("email") if hasattr(u, "get") else None
        except Exception:
            name = None
        if not name:
            name = query(session(), "SELECT CURRENT_USER() AS U")[0]["U"]
        st.session_state["_user"] = name
    return st.session_state["_user"]


@st.cache_data(ttl=300, show_spinner=False)
def masters_cached():
    from order_service import load_masters

    return load_masters(session())


def status_badge(status: str):
    label, color = STATUS_LABEL.get(status, (status, "gray"))
    st.badge(label, color=color)


def status_text(status: str) -> str:
    return STATUS_LABEL.get(status, (status,))[0]


def yen(v) -> str:
    return "-" if v is None else f"¥{float(v):,.0f}"


@st.cache_data(max_entries=16, show_spinner=False)
def render_pdf_pages(document_id: str, _pdf: bytes, scale: float = 1.4) -> list:
    """PDF を画像化してプレビュー（ランタイム差に依存しない表示方法）"""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    doc = pdfium.PdfDocument(_pdf)
    images = [doc[i].render(scale=scale).to_pil() for i in range(min(len(doc), 5))]
    doc.close()
    return images


@st.cache_data(max_entries=32, show_spinner=False)
def pdf_bytes(document_id: str) -> bytes:
    from upload import download_pdf

    return download_pdf(session(), document_id)


def select_document(label="処理対象の受付", statuses=None, key="selected_doc"):
    """受付一覧から選択。選択状態は session_state に保持し、他画面から引き継げる"""
    rows = query(session(), """SELECT DOCUMENT_ID, ORIGINAL_FILENAME, STATUS, UPLOADED_AT
        FROM SNOWVILLAGE_DB.SNOWVILLAGE_RAW.SNOWVILLAGE_DOCUMENTS ORDER BY UPLOADED_AT DESC LIMIT 300""")
    if statuses:
        rows = [r for r in rows if r["STATUS"] in statuses]
    if not rows:
        return None
    ids = [r["DOCUMENT_ID"] for r in rows]
    by_id = {r["DOCUMENT_ID"]: r for r in rows}
    cur = st.session_state.get(key)
    if cur in ids:
        st.session_state[f"{key}_box"] = cur  # 他画面からの引き継ぎを反映
    sel = st.selectbox(label, ids, key=f"{key}_box",
                       on_change=lambda: st.session_state.update({key: st.session_state[f"{key}_box"]}),
                       format_func=lambda i: f"{by_id[i]['ORIGINAL_FILENAME']}  ·  {status_text(by_id[i]['STATUS'])}  ·  "
                                             f"{by_id[i]['UPLOADED_AT']:%m/%d %H:%M}  ·  {i[:8]}")
    st.session_state[key] = sel
    return sel
