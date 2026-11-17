"""docs/ 用の構成図・処理フロー図を生成（matplotlib・日本語フォント Noto Sans CJK JP）

実行: python3 scripts/generate_diagrams.py
出力: docs/architecture.png, docs/processing_flow.png, docs/status_flow.png
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.family"] = ["Noto Sans CJK JP", "Noto Sans CJK JP Regular", "DejaVu Sans"]
OUT = Path(__file__).resolve().parents[1] / "docs"

SNOW = "#29B5E8"; NAVY = "#11567F"; GRAY = "#6B7280"; LIGHT = "#F3F8FC"
GREEN = "#1A7F37"; ORANGE = "#C77700"; RED = "#D33"; PURPLE = "#7C3AED"


def box(ax, x, y, w, h, title, body=None, fc="white", ec=NAVY, tc=NAVY, fs=11, bfs=8.5, lw=1.4, r=0.012):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw))
    if body:
        ax.text(x + w / 2, y + h - 0.022, title, ha="center", va="top", fontsize=fs, color=tc, weight="bold")
        ax.text(x + w / 2, y + h - 0.052, body, ha="center", va="top", fontsize=bfs, color="#222", linespacing=1.45)
    else:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=fs, color=tc, weight="bold")


def group(ax, x, y, w, h, label, ec=SNOW, fc=LIGHT):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.015", fc=fc, ec=ec, lw=1.6,
                                linestyle=(0, (4, 2))))
    ax.text(x + 0.008, y + h - 0.008, label, ha="left", va="top", fontsize=10, color=ec, weight="bold")


def arrow(ax, p, q, label=None, color=GRAY, ls="-", rad=0.0, lfs=8, loff=(0, 0.012), lw=1.4):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=13, color=color, lw=lw, linestyle=ls,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text((p[0] + q[0]) / 2 + loff[0], (p[1] + q[1]) / 2 + loff[1], label, ha="center", va="bottom",
                fontsize=lfs, color=color, bbox=dict(fc="white", ec="none", pad=1.2))


def canvas(w, h, title, subtitle):
    fig = plt.figure(figsize=(w, h), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.text(0.02, 0.975, title, fontsize=17, weight="bold", color=NAVY, va="top")
    ax.text(0.02, 0.935, subtitle, fontsize=9.5, color=GRAY, va="top")
    return fig, ax


# ====================================================================== 1. アーキテクチャ
def architecture():
    fig, ax = canvas(16, 9.6, "SnowVillage 受注取込デモ — システム構成",
                     "すべて Snowflake ネイティブ（外部OCR・外部API・外部DBなし）／ 作成リソースはすべて SNOWVILLAGE_ 接頭辞")
    # 利用者
    box(ax, 0.02, 0.70, 0.12, 0.13, "業務担当者", "ブラウザ（Snowsight）\nSNOWVILLAGE_VIEWER_ROLE\nUSAGE のみ", fc="#FFF", ec=GRAY, tc="#333")
    box(ax, 0.02, 0.50, 0.12, 0.13, "発注書PDF", "取引先ごとに書式が異なる\nA: A4縦 / B: A4横2頁\nC: スキャン画像", fc="#FFF", ec=GRAY, tc="#333")
    arrow(ax, (0.08, 0.63), (0.08, 0.70), "ドラッグ&ドロップ", lfs=7.5, loff=(0.0, -0.01))

    group(ax, 0.17, 0.05, 0.81, 0.86, "Snowflake アカウント（AWS ap-northeast-1）")

    # Streamlit
    group(ax, 0.19, 0.55, 0.37, 0.32, "Streamlit in Snowflake  SNOWVILLAGE_ORDER_INTAKE_APP", ec=NAVY, fc="#FFFFFF")
    ax.text(0.20, 0.825, "ウェアハウスランタイム（SYSTEM$WAREHOUSE_RUNTIME）・所有者権限・Streamlit 1.52",
            fontsize=7.8, color=GRAY)
    pages = [("A アップロード", "file_uploader\n実体検証・SHA-256"), ("B AI抽出・受注確認", "原本プレビュー\n修正・再検証・確定"),
             ("C 受注一覧", "絞り込み・CSV\n原本・履歴参照"), ("D 処理状況・監査", "KPI・完全性\n状態遷移・監査ログ")]
    for i, (t, b) in enumerate(pages):
        box(ax, 0.20 + i * 0.089, 0.705, 0.083, 0.10, t, b, fs=8.6, bfs=7.2, ec=SNOW)
    box(ax, 0.20, 0.57, 0.35, 0.115, "サービス層（Python / Snowpark）",
        "upload.py  →  extraction.py  →  normalization.py  →  validation.py  →  order_service.py\n"
        "history.py（状態遷移・監査・完全性）    db.py（パラメータ化SQL・日本時間変換）",
        fs=9.5, bfs=7.6, fc="#EAF6FC", ec=NAVY)
    arrow(ax, (0.14, 0.765), (0.20, 0.765), "HTTPS", lfs=7.5)

    # Cortex
    group(ax, 0.60, 0.68, 0.17, 0.19, "Cortex AI", ec=PURPLE, fc="#F7F3FF")
    box(ax, 0.61, 0.73, 0.15, 0.10, "AI_EXTRACT", "TO_FILE(@PDF_STAGE, …)\nJSON Schema（項目＋明細表）\nscores => TRUE", fs=10, bfs=7.4, ec=PURPLE, tc=PURPLE)
    ax.text(0.685, 0.70, "SNOWFLAKE.CORTEX_USER\nクロスリージョン: AWS_JP（国内のみ）", ha="center", fontsize=7, color=PURPLE)

    # Warehouse
    box(ax, 0.80, 0.73, 0.16, 0.10, "SNOWVILLAGE_WH", "X-SMALL\nAUTO_SUSPEND 60秒\nアプリ実行・SQL", fs=10, bfs=7.6, ec=GRAY, tc="#333")

    # DB
    group(ax, 0.19, 0.08, 0.77, 0.43, "SNOWVILLAGE_DB", ec=NAVY, fc="#FFFFFF")
    # RAW
    group(ax, 0.20, 0.11, 0.24, 0.36, "SNOWVILLAGE_RAW（受付・抽出の生データ）", ec=SNOW)
    box(ax, 0.21, 0.335, 0.22, 0.09, "@SNOWVILLAGE_PDF_STAGE", "内部ステージ（SNOWFLAKE_SSE）\n<受付ID>/original.pdf（バイナリのまま）", fs=9, bfs=7.3, ec=SNOW)
    box(ax, 0.21, 0.235, 0.22, 0.085, "DOCUMENTS / STATUS_HISTORY", "受付台帳（完全性の起点）\nSHA-256・状態・遷移日時", fs=9, bfs=7.3)
    box(ax, 0.21, 0.13, 0.22, 0.09, "EXTRACTION_RUNS", "全実行を保持：応答JSON（無加工）\nスコア・エラー・処理時間・Query ID", fs=9, bfs=7.3)
    # CORE
    group(ax, 0.46, 0.11, 0.27, 0.36, "SNOWVILLAGE_CORE（マスタ・受注・検証・監査）", ec=SNOW)
    box(ax, 0.47, 0.335, 0.25, 0.09, "マスタ", "CUSTOMERS（別名）/ PRODUCTS / TAX_RATES\nCUSTOMER_PRODUCT_MAP（取引先コード・換算・契約単価）", fs=9, bfs=7.1)
    box(ax, 0.47, 0.235, 0.25, 0.085, "ORDER_HEADERS / ORDER_LINES", "RAW_*（発注書の表記）＋ 標準化値\nDRAFT / CONFIRMED / SUPERSEDED / DISCARDED", fs=9, bfs=7.1)
    box(ax, 0.47, 0.13, 0.25, 0.09, "VALIDATION_RESULTS / AUDIT_LOG", "ルールごとの PASS / WARNING / ERROR\n修正前後・実行者・確定・置換理由", fs=9, bfs=7.1)
    # APP
    group(ax, 0.75, 0.11, 0.20, 0.36, "SNOWVILLAGE_APP（公開層）", ec=SNOW)
    box(ax, 0.76, 0.335, 0.18, 0.09, "V_CONFIRMED_ORDERS", "確定受注のみ\n（下流連携・CSV）", fs=8.6, bfs=7.3, ec=GREEN, tc=GREEN)
    box(ax, 0.76, 0.235, 0.18, 0.085, "V_DOCUMENT_PIPELINE", "受付起点の完全性状態", fs=8.6, bfs=7.3)
    box(ax, 0.76, 0.13, 0.18, 0.09, "READ_PDF（プロシージャ）\n@SNOWVILLAGE_APP_STAGE", None, fs=8, ec=GRAY, tc="#333")

    # flows（終点は SNOWVILLAGE_DB の外枠に揃え、グループ名と重ならないようにする）
    arrow(ax, (0.24, 0.57), (0.24, 0.51), color=NAVY)
    ax.text(0.245, 0.535, " ① put_stream（auto_compress=False）→ LIST で保存確認", fontsize=7.4, color=NAVY, va="center")
    arrow(ax, (0.55, 0.64), (0.61, 0.76), color=PURPLE)
    ax.text(0.585, 0.62, "② AI_EXTRACT", fontsize=7.4, color=PURPLE, ha="left", va="center")
    arrow(ax, (0.66, 0.68), (0.60, 0.51), color=PURPLE, ls="--")
    ax.text(0.64, 0.585, "TO_FILE で\n原本を参照", fontsize=7.2, color=PURPLE, ha="left", va="center")
    arrow(ax, (0.45, 0.57), (0.45, 0.51), color=NAVY)
    ax.text(0.455, 0.535, " ③ 標準化・照合・検証・確定", fontsize=7.4, color=NAVY, va="center")
    arrow(ax, (0.72, 0.28), (0.76, 0.37), None, color=GREEN)
    fig.savefig(OUT / "architecture.png", facecolor="white"); plt.close(fig)


# ====================================================================== 2. 処理フロー
def processing_flow():
    fig, ax = canvas(16, 9.2, "処理の流れ — PDF受付から受注確定まで",
                     "AI抽出の成功だけでは確定しない：決定論的な業務ルール検証に合格（ERRORなし）したものだけが確定受注になる")
    lanes = [("業務担当者", 0.78, "#FFFFFF"), ("Streamlit / サービス層", 0.53, "#F3F8FC"),
             ("Cortex AI", 0.36, "#F7F3FF"), ("Snowflake テーブル・ステージ", 0.08, "#F6FBF7")]
    hs = {0.78: 0.12, 0.53: 0.24, 0.36: 0.16, 0.08: 0.27}
    for name, y, fc in lanes:
        ax.add_patch(FancyBboxPatch((0.02, y), 0.96, hs[y], boxstyle="round,pad=0,rounding_size=0.008", fc=fc, ec="#D0D7DE"))
        ax.text(0.03, y + hs[y] / 2, name, rotation=90, ha="center", va="center", fontsize=9.5, color=NAVY, weight="bold")

    W, H = 0.105, 0.085
    steps_x = [0.05 + i * 0.118 for i in range(8)]
    # 担当者
    user = [(0, "① PDFを選択\nアップロード"), (2, "④「AIで読み取り」"), (5, "⑦ 検証結果を確認\n必要なら修正"), (7, "⑨「受注確定」")]
    for i, t in user:
        box(ax, steps_x[i], 0.795, W, H, t, fs=8.8, ec=GRAY, tc="#222")
    # サービス
    svc = [(0, "② 受付検証", "拡張子・%PDF-・%%EOF\npypdfium2解析・20MB\nSHA-256 → 重複判定"),
           (1, "③ ステージ保存", "RECEIVING で受付記録\nput_stream → LIST確認\n→ UPLOADED"),
           (3, "⑤ 抽出実行", "EXTRACTING\n実行履歴 RUNNING\n失敗時 FAILED（再実行可）"),
           (4, "⑥ 標準化・照合", "列配列→行（長さ不一致はERROR）\n日付/和暦・数値・社名正規化\n商品コード対応・単位換算・税率"),
           (5, "⑦' 業務ルール検証", "必須・取引先・商品・数量\n数量×単価・合計・消費税\n日付・重複（ファイル/発注番号）"),
           (6, "⑧ 修正の保存", "変更項目ごとに監査ログ\n（修正前→後・実行者）\n→ 再検証"),
           (7, "⑩ 確定処理", "確定直前に再検証\nERRORなし → CONFIRMED\n訂正版: 理由必須で旧版を置換")]
    for i, t, b in svc:
        box(ax, steps_x[i], 0.585, W, 0.145, t, b, fs=9, bfs=6.9, ec=NAVY, fc="white")
    # Cortex
    box(ax, steps_x[3] - 0.01, 0.385, W + 0.02, 0.11, "AI_EXTRACT", "ヘッダー10項目（string）\n明細表（列ごとの array）\nscores（参考値）", fs=9.5, bfs=7.2, ec=PURPLE, tc=PURPLE)
    # テーブル
    tbl = [(0, "DOCUMENTS\n(DUPLICATE)", GRAY), (1, "PDF_STAGE\nDOCUMENTS", SNOW), (3, "EXTRACTION_RUNS\n応答JSON無加工", SNOW),
           (4, "ORDER_HEADERS\nORDER_LINES (DRAFT)", NAVY), (5, "VALIDATION_RESULTS", NAVY), (6, "AUDIT_LOG", NAVY),
           (7, "V_CONFIRMED_ORDERS\n確定受注のみ", GREEN)]
    for i, t, c in tbl:
        box(ax, steps_x[i], 0.13, W, 0.09, t, fs=8.2, ec=c, tc=c)
    ax.text(0.5, 0.095, "全段階の状態遷移を DOCUMENT_STATUS_HISTORY に記録（受付IDから原本・抽出・検証・修正・確定を追跡）／ 状態は永続テーブルのため再読み込み後も復元",
            ha="center", fontsize=8.3, color=GRAY)

    def c(i, y):
        return steps_x[i] + W / 2, y
    # 担当→サービス
    arrow(ax, c(0, 0.795), c(0, 0.73)); arrow(ax, c(2, 0.795), (steps_x[3] + 0.02, 0.73), rad=0.15)
    arrow(ax, (steps_x[5] + 0.03, 0.73), (steps_x[5] + 0.03, 0.795), "結果表示", lfs=7)
    arrow(ax, (steps_x[5] + 0.08, 0.795), (steps_x[6] + 0.03, 0.73), "修正", lfs=7, rad=-0.2)
    arrow(ax, c(7, 0.795), c(7, 0.73))
    # サービス内
    arrow(ax, (steps_x[0] + W, 0.66), (steps_x[1], 0.66), "新規", lfs=7)
    arrow(ax, (steps_x[3] + W, 0.66), (steps_x[4], 0.66)); arrow(ax, (steps_x[4] + W, 0.66), (steps_x[5], 0.66))
    arrow(ax, (steps_x[6] + 0.02, 0.585), (steps_x[5] + 0.08, 0.585), "再検証", lfs=7, rad=-0.4, loff=(0, -0.045))
    # Cortex
    arrow(ax, c(3, 0.585), (steps_x[3] + W / 2, 0.495), color=PURPLE)
    arrow(ax, (steps_x[3] + W + 0.01, 0.44), (steps_x[4] + 0.02, 0.585), "応答JSON", color=PURPLE, lfs=7, rad=0.2)
    # テーブルへ
    for i in (1, 4, 5, 6, 7):
        arrow(ax, c(i, 0.585), c(i, 0.22), color="#9AA4AE", lw=1.1)
    arrow(ax, (steps_x[0] + 0.02, 0.585), (steps_x[0] + 0.02, 0.22), "重複時\n受注は作らない", color=GRAY, lfs=6.8, lw=1.1, loff=(0.035, 0.0))
    arrow(ax, (steps_x[3] + 0.01, 0.385), (steps_x[3] + 0.01, 0.22), color="#9AA4AE", lw=1.1)
    # ERROR ゲート
    ax.text(steps_x[7] + W / 2, 0.555, "ERROR があれば\n確定ボタン無効", ha="center", va="top", fontsize=7.3, color=RED)
    fig.savefig(OUT / "processing_flow.png", facecolor="white"); plt.close(fig)


# ====================================================================== 3. 状態遷移
def status_flow():
    fig, ax = canvas(16, 6.4, "受付（DOCUMENTS.STATUS）と受注版（ORDER_STATUS）の状態遷移",
                     "受付記録は失敗しても消えない ／ 確定済み受注は再処理・訂正版でも上書きせず、SUPERSEDED として履歴に残す")
    ax.set_ylim(0, 1.08)
    S = {"RECEIVING": (0.03, 0.55, GRAY, "受付処理中\n（保存確認前）"), "UPLOADED": (0.17, 0.55, SNOW, "受付済み"),
         "EXTRACTING": (0.31, 0.55, PURPLE, "AI抽出中"), "NEEDS_REVIEW": (0.47, 0.70, ORANGE, "確認待ち\n（ERRORあり）"),
         "READY": (0.47, 0.40, GREEN, "確定可能\n（PASS/WARNING）"), "CONFIRMED": (0.66, 0.55, NAVY, "受注確定済み"),
         "SUPERSEDED": (0.84, 0.55, GRAY, "訂正版で\n置換済み"), "FAILED": (0.31, 0.22, RED, "処理失敗"),
         "DUPLICATE": (0.03, 0.22, GRAY, "重複ファイル")}
    W, H = 0.11, 0.12
    for k, (x, y, col, jp) in S.items():
        box(ax, x, y, W, H, k, jp, fs=9.5, bfs=8, ec=col, tc=col)

    def p(k, side):
        x, y = S[k][0], S[k][1]
        return {"r": (x + W, y + H / 2), "l": (x, y + H / 2), "t": (x + W / 2, y + H), "b": (x + W / 2, y),
                "tr": (x + W, y + H * 0.8), "br": (x + W, y + H * 0.2)}[side]
    arrow(ax, p("RECEIVING", "r"), p("UPLOADED", "l"), "保存を確認", lfs=7.3)
    arrow(ax, p("RECEIVING", "b"), p("DUPLICATE", "t"), "同一SHA-256", lfs=7.3, loff=(0.03, 0))
    arrow(ax, p("UPLOADED", "r"), p("EXTRACTING", "l"), "AIで読み取り", lfs=7.3)
    arrow(ax, p("EXTRACTING", "tr"), p("NEEDS_REVIEW", "l"), "検証ERROR", lfs=7.3, rad=-0.1)
    arrow(ax, p("EXTRACTING", "br"), p("READY", "l"), "ERRORなし", lfs=7.3, rad=0.1)
    arrow(ax, p("NEEDS_REVIEW", "b"), p("READY", "t"), "修正→再検証", lfs=7.3, loff=(0.04, -0.01))
    arrow(ax, p("READY", "r"), p("CONFIRMED", "l"), "受注確定", lfs=7.3, rad=0.1, loff=(0, -0.03))
    arrow(ax, p("NEEDS_REVIEW", "r"), p("CONFIRMED", "l"), "訂正版として確定\n（発注番号重複のみ・理由必須）", lfs=6.8, rad=-0.1, loff=(0, 0.01))
    arrow(ax, p("CONFIRMED", "r"), p("SUPERSEDED", "l"), "別受付の訂正版で置換", lfs=7.3)
    arrow(ax, p("EXTRACTING", "b"), p("FAILED", "t"), "抽出失敗", lfs=7.3, loff=(0.03, 0))
    arrow(ax, p("FAILED", "l"), p("UPLOADED", "b"), "再処理", lfs=7.3, rad=-0.3, color=RED, loff=(-0.03, 0))
    arrow(ax, p("CONFIRMED", "t"), p("EXTRACTING", "t"), None, rad=0.5, ls="--")
    ax.text(0.545, 0.905, "再読み取り（新しい版を作成・確定版は保持）", ha="center", fontsize=7.6, color=GRAY,
            bbox=dict(fc="white", ec="none", pad=1.2))
    # 受注版
    ax.add_patch(FancyBboxPatch((0.47, 0.05), 0.51, 0.12, boxstyle="round,pad=0,rounding_size=0.01", fc=LIGHT, ec=SNOW))
    ax.text(0.48, 0.15, "受注版（ORDER_HEADERS.ORDER_STATUS）", fontsize=9.5, color=NAVY, weight="bold", va="top")
    ax.text(0.48, 0.11, "DRAFT（確認中・確定ビューに出ない） → CONFIRMED（確定受注）→ SUPERSEDED（再処理・訂正版で置換。履歴として保持）\n"
                        "DRAFT → DISCARDED（再読み取りで破棄された下書き）", fontsize=8.2, va="top", linespacing=1.6)
    fig.savefig(OUT / "status_flow.png", facecolor="white"); plt.close(fig)


if __name__ == "__main__":
    architecture(); processing_flow(); status_flow()
    print("generated:", [p.name for p in sorted(OUT.glob("*.png"))])
