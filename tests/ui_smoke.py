"""画面のスモークテスト（Streamlit AppTest・実 Snowflake 接続）

各画面を headless で実行し、例外なく描画されることを確認する。
  実行: cd app && python ../tests/ui_smoke.py
"""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

PAGES = ["upload_page.py", "review_page.py", "orders_page.py", "audit_page.py"]


def run(page, setup=None):
    at = AppTest.from_file(str(APP / "app_pages" / page), default_timeout=180)
    if setup:
        setup(at)
    at.run()
    errs = [e.value for e in at.exception]
    return at, errs


if __name__ == "__main__":
    failed = 0
    for p in PAGES:
        at, errs = run(p)
        status = "OK" if not errs else "EXCEPTION"
        failed += bool(errs)
        print(f"{status:9} {p}: titles={[t.value for t in at.title]} errors_shown={[e.value[:80] for e in at.error]}")
        for e in errs:
            print("   ", e[:1500])
    # 確認画面: 全受付（全ステータス）を順に開く
    from db import get_session, query

    docs = query(get_session(), "SELECT DOCUMENT_ID, STATUS, ORIGINAL_FILENAME FROM SNOWVILLAGE_DB.SNOWVILLAGE_RAW.SNOWVILLAGE_DOCUMENTS")
    for d in docs:
        at, errs = run("review_page.py", lambda a, i=d["DOCUMENT_ID"]: a.session_state.__setitem__("selected_doc", i))
        failed += bool(errs)
        print(f"{'OK' if not errs else 'EXCEPTION':9} review[{d['STATUS']:12}] {d['ORIGINAL_FILENAME']}: "
              f"buttons={[b.label for b in at.button if not b.disabled][:6]} images={len(at.get('imgs'))}")
        for e in errs:
            print("   ", e[:1500])
    sys.exit(1 if failed else 0)
