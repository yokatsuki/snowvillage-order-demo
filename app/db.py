"""Snowflake 接続と共通ヘルパー

- SiS 上では get_active_session()（所有者権限で実行）
- ローカル（テスト・開発）では接続名 SNOWVILLAGE_CONNECTION（既定: default）で接続
SQL はすべて qmark パラメータで実行し、値を文字列連結しない。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

DB = "SNOWVILLAGE_DB"
RAW = f"{DB}.SNOWVILLAGE_RAW"
CORE = f"{DB}.SNOWVILLAGE_CORE"
APP = f"{DB}.SNOWVILLAGE_APP"
PDF_STAGE = f"{RAW}.SNOWVILLAGE_PDF_STAGE"

T_DOCUMENTS = f"{RAW}.SNOWVILLAGE_DOCUMENTS"
T_STATUS_HISTORY = f"{RAW}.SNOWVILLAGE_DOCUMENT_STATUS_HISTORY"
T_RUNS = f"{RAW}.SNOWVILLAGE_EXTRACTION_RUNS"
T_CUSTOMERS = f"{CORE}.SNOWVILLAGE_CUSTOMERS"
T_PRODUCTS = f"{CORE}.SNOWVILLAGE_PRODUCTS"
T_CMAP = f"{CORE}.SNOWVILLAGE_CUSTOMER_PRODUCT_MAP"
T_TAX = f"{CORE}.SNOWVILLAGE_TAX_RATES"
T_HEADERS = f"{CORE}.SNOWVILLAGE_ORDER_HEADERS"
T_LINES = f"{CORE}.SNOWVILLAGE_ORDER_LINES"
T_VALIDATIONS = f"{CORE}.SNOWVILLAGE_VALIDATION_RESULTS"
T_AUDIT = f"{CORE}.SNOWVILLAGE_AUDIT_LOG"
V_CONFIRMED = f"{APP}.SNOWVILLAGE_V_CONFIRMED_ORDERS"
V_PIPELINE = f"{APP}.SNOWVILLAGE_V_DOCUMENT_PIPELINE"


JST = timezone(timedelta(hours=9), "JST")  # 日本時間（夏時間なし）


def _set_tokyo_tz(s):
    # SiS の所有者権限セッションで ALTER SESSION が使えない場合も、query() 側で日本時間に変換する
    try:
        s.sql("ALTER SESSION SET TIMEZONE = 'Asia/Tokyo'").collect()
    except Exception:
        pass
    return s


def to_jst(v):
    """タイムゾーン付き日時を日本時間（表示用にタイムゾーン情報なし）へ"""
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(JST).replace(tzinfo=None)
    return v


def get_session():
    try:
        from snowflake.snowpark.context import get_active_session

        return _set_tokyo_tz(get_active_session())
    except Exception:
        from snowflake.snowpark import Session

        s = Session.builder.config("connection_name", os.environ.get("SNOWVILLAGE_CONNECTION", "default")).create()
        s.sql("USE ROLE SNOWVILLAGE_APP_OWNER_ROLE").collect()
        s.sql("USE WAREHOUSE SNOWVILLAGE_WH").collect()
        s.sql(f"USE DATABASE {DB}").collect()
        return _set_tokyo_tz(s)


def new_id() -> str:
    return str(uuid.uuid4())


def _bind(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def _inline_nulls(sql: str, params: list) -> tuple[str, list]:
    """None のパラメータを SQL リテラル NULL に置き換える（文字列リテラル内の ? は対象外）。
    SiS（ウェアハウスランタイム）の Snowpark では None のバインドが 'None' 文字列になる場合があるため"""
    if not any(p is None for p in params):
        return sql, params
    out, vals, i, in_str = [], [], 0, False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        if ch == "?" and not in_str:
            v = params[i]
            i += 1
            if v is None:
                out.append("NULL")
                continue
            vals.append(v)
        out.append(ch)
    return "".join(out), vals


def _run(session, sql, params):
    sql, vals = _inline_nulls(sql, [_bind(p) for p in (params or [])])
    return session.sql(sql, params=vals).collect()


def query(session, sql: str, params=None) -> list[dict]:
    rows = _run(session, sql, params)
    return [{k: to_jst(v) for k, v in r.as_dict().items()} for r in rows]


def execute(session, sql: str, params=None) -> None:
    _run(session, sql, params)


def insert_row(session, table: str, row: dict, variant_cols: tuple = ()) -> None:
    """列名はコード内定数のみ。値はすべてバインド変数。VARIANT 列は PARSE_JSON で格納。"""
    cols = list(row.keys())
    exprs = ["PARSE_JSON(?)" if c in variant_cols else "?" for c in cols]
    vals = [json.dumps(row[c], ensure_ascii=False, default=str) if c in variant_cols and row[c] is not None
            else _bind(row[c]) for c in cols]
    execute(session, f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(exprs)}", vals)


def _bind_str(v):
    """複数行 VALUES 用: 列の型推論がぶれないよう値はすべて文字列（NULL はそのまま）で渡し、格納時に暗黙変換"""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(_bind(v))


def insert_rows(session, table: str, rows: list[dict], variant_cols: tuple = ()) -> None:
    """複数行を 1 文の INSERT で登録（文単位で原子的）。
    NULL はバインドせず SQL リテラル NULL にする（SiS の Snowpark では VALUES 内の None が 'None' 文字列になるため）"""
    if not rows:
        return
    cols = list(rows[0].keys())
    exprs = [f"PARSE_JSON(${i})" if c in variant_cols else f"${i}" for i, c in enumerate(cols, 1)]
    tuples, vals = [], []
    for r in rows:
        ph = []
        for c in cols:
            v = r.get(c)
            if v is None:
                ph.append("NULL")
            else:
                ph.append("?")
                vals.append(json.dumps(v, ensure_ascii=False, default=str) if c in variant_cols else _bind_str(v))
        tuples.append("(" + ", ".join(ph) + ")")
    execute(session, f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(exprs)} "
                     f"FROM VALUES {', '.join(tuples)}", vals)


def load_json(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v
