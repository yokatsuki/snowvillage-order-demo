"""SnowVillage デモ用 PDF 発注書ジェネレーター（すべて架空データ）

出力:
  samples/order_company_a.pdf          サンプルA: 一般的な A4 縦（正常系）
  samples/order_company_b.pdf          サンプルB: A4 横・別項目名・独自コード・和暦・2ページ明細
  samples/order_company_c.pdf          サンプルC: 画像化(スキャン風)PDF・未登録商品・明細金額不整合
  samples/order_company_a_revised.pdf  Aと同一発注番号で内容が異なる版（上書き防止テスト用）
  samples/expected_results.json        生成元データ = 抽出・検証の期待値

実行: python3 scripts/generate_sample_pdfs.py
"""
import io
import json
import random
from pathlib import Path

import fitz  # PyMuPDF: サンプルCのラスタライズ用（生成スクリプトのみで使用）
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
# 日本語フォントは PDF に埋め込む（非埋め込み CID フォントは閲覧環境に日本語フォントが無いと表示されない。
# 例: Streamlit in Snowflake 上の pypdfium2 プレビュー）。PyMuPDF 同梱の Droid Sans Fallback（TrueType, Apache 2.0）を
# reportlab の TTFont として登録し、使用文字のみサブセット埋め込みする。
_FONT_PATH = Path(__file__).resolve().parent / ".font_droid_sans_fallback.ttf"
if not _FONT_PATH.exists():
    _FONT_PATH.write_bytes(fitz.Font("cjk").buffer)
pdfmetrics.registerFont(TTFont("JPGothic", str(_FONT_PATH)))
GOTH = MIN = "JPGothic"
SUPPLIER = "スノービレッジ文具販売株式会社"
DISCLAIMER = "※本書は SnowVillage デモ用に作成した架空の発注書です。実在の企業・取引とは関係ありません。"


def yen(v):
    return f"{v:,}"


def totals(lines, rate=0.10):
    sub = sum(l["line_amount_correct"] for l in lines)
    tax = int(sub * rate)  # 端数切り捨て
    return sub, tax, sub + tax


# ---------------------------------------------------------------- data
ORDER_A = {
    "file": "order_company_a.pdf", "customer_id": "C001",
    "order_number": "SKR-PO-2026-1105", "order_date": "2026-11-05", "order_date_text": "2026年11月5日",
    "buyer_name": "株式会社さくら文具商事", "delivery_date": "2026-11-12", "delivery_date_text": "2026年11月12日",
    "delivery_address": "東京都千代田区架空町1-2-3 さくらビル5F 総務部",
    "contact": "総務部 佐倉 花子", "notes": "午前中着でお願いします。",
    "lines": [
        dict(code="SV-CP-A4", name="コピー用紙 A4 500枚", spec="白色度93%", qty=20, unit="冊", price=480),
        dict(code="SV-TN-K01", name="トナーカートリッジ 黒 TK-01", spec="純正品", qty=2, unit="本", price=8800),
        dict(code="SV-CF-A4", name="クリアファイル A4 10枚入", spec="透明", qty=15, unit="パック", price=350),
        dict(code="SV-BP-BK", name="ボールペン 黒 10本入", spec="0.5mm", qty=5, unit="箱", price=900),
    ],
}
ORDER_A_REV = json.loads(json.dumps(ORDER_A))
ORDER_A_REV.update(file="order_company_a_revised.pdf", notes="数量変更版。午前中着でお願いします。")
ORDER_A_REV["lines"][0]["qty"] = 30

ORDER_B = {
    "file": "order_company_b.pdf", "customer_id": "C002",
    "order_number": "AOB-77812", "order_date": "2026-11-06", "order_date_text": "2026/11/06",
    "buyer_name": "青葉ロジテック(株)", "delivery_date": "2026-11-20", "delivery_date_text": "R8.11.20",
    "delivery_address": "神奈川県横浜市架空区みどり台4-5-6 青葉ロジテック 第2倉庫",
    "contact": "購買課 青葉 太郎", "notes": "納品時は第2倉庫受付へ。",
    "lines": [
        dict(code="AL-1001", name="PPC用紙 A4 (5冊/箱)", spec="64g/m2", qty=4, unit="箱", price=2400),
        dict(code="AL-2050", name="レーザートナー BK", spec="TK-01対応", qty=3, unit="個", price=8500),
        dict(code="AL-3300", name="ステープル針 #10", spec="1000本", qty=10, unit="箱", price=120),
        dict(code="AL-3410", name="フラットファイル 青", spec="A4 10冊", qty=2, unit="P", price=1200),
        dict(code="AL-3120", name="クリアホルダー A4", spec="10枚入", qty=6, unit="P", price=350),
    ],
}
ORDER_C = {
    "file": "order_company_c.pdf", "customer_id": "C003",
    "order_number": "MNT-2611-031", "order_date": "2026-11-07", "order_date_text": "2026.11.07",
    "buyer_name": "みなと工務店株式会社", "delivery_date": "2026-11-21", "delivery_date_text": "2026年11月21日",
    "delivery_address": "大阪府大阪市架空区港南7-8-9 みなと工務店 資材置場",
    "contact": "工務部 湊 一郎", "notes": "現場直送。",
    "lines": [
        dict(code="MK-0101", name="事務用紙 A4", spec="500枚", qty=10, unit="冊", price=480),
        # 意図的な不整合: 2 x 8,800 = 17,600 だが 18,600 と記載
        dict(code="MK-0205", name="トナー 黒", spec="TK-01", qty=2, unit="本", price=8800, printed_amount=18600),
        # 意図的な未登録商品コード
        dict(code="MK-0420", name="クリアホルダー A4 青", spec="10枚", qty=4, unit="パック", price=350),
        dict(code="MK-0310", name="ボールペン 黒", spec="10本", qty=3, unit="箱", price=900),
    ],
}

for o in (ORDER_A, ORDER_A_REV, ORDER_B, ORDER_C):
    for i, l in enumerate(o["lines"], 1):
        l["line_no"] = i
        l["line_amount_correct"] = l["qty"] * l["price"]
        l["line_amount_printed"] = l.get("printed_amount", l["line_amount_correct"])
    o["subtotal"], o["tax_amount"], o["total_amount"] = totals(o["lines"])


# ---------------------------------------------------------------- drawing helpers
def text(c, x, y, s, size=10, font=GOTH, align="l", color=colors.black):
    c.setFont(font, size)
    c.setFillColor(color)
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, y, s)
    c.setFillColor(colors.black)


def table(c, x, y, cols, rows, row_h=8 * mm, header_fill=colors.HexColor("#DDE7F3"), size=9):
    """cols: [(title, width, align)]; rows: list of list[str]. returns bottom y."""
    total_w = sum(w for _, w, _ in cols)
    c.setFillColor(header_fill)
    c.rect(x, y - row_h, total_w, row_h, fill=1, stroke=0)
    c.setFillColor(colors.black)
    yy = y
    for r_i, row in enumerate([[t for t, _, _ in cols]] + rows):
        cx = x
        for (title, w, al), val in zip(cols, row):
            al = "c" if r_i == 0 else al
            tx = {"l": cx + 2 * mm, "r": cx + w - 2 * mm, "c": cx + w / 2}[al]
            text(c, tx, yy - row_h + 2.6 * mm, val, size, align=al)
            cx += w
        yy -= row_h
    # grid
    c.setLineWidth(0.6)
    for i in range(len(rows) + 2):
        c.line(x, y - i * row_h, x + total_w, y - i * row_h)
    cx = x
    for _, w, _ in cols:
        c.line(cx, y, cx, yy)
        cx += w
    c.line(cx, y, cx, yy)
    return yy


# ---------------------------------------------------------------- sample A (A4 portrait, standard)
def draw_a(o, path):
    c = canvas.Canvas(str(path), pagesize=A4)
    W, H = A4
    c.setTitle(f"発注書 {o['order_number']}")
    text(c, W / 2, H - 25 * mm, "発　注　書", 22, align="c")
    c.line(W / 2 - 30 * mm, H - 27 * mm, W / 2 + 30 * mm, H - 27 * mm)
    text(c, W - 20 * mm, H - 38 * mm, f"発注番号：{o['order_number']}", 10, align="r")
    text(c, W - 20 * mm, H - 44 * mm, f"発注日：{o['order_date_text']}", 10, align="r")
    text(c, 20 * mm, H - 45 * mm, f"{SUPPLIER}　御中", 13)
    c.line(20 * mm, H - 47 * mm, 110 * mm, H - 47 * mm)
    # 発注元
    text(c, W - 20 * mm, H - 56 * mm, o["buyer_name"], 12, align="r")
    text(c, W - 20 * mm, H - 62 * mm, "〒100-0000 東京都千代田区架空町1-2-3", 8, align="r")
    text(c, W - 20 * mm, H - 67 * mm, f"担当：{o['contact']}　TEL 03-0000-0000", 8, align="r")
    text(c, 20 * mm, H - 60 * mm, "下記の通り発注いたします。", 10)
    # 金額ボックス
    c.rect(20 * mm, H - 82 * mm, 85 * mm, 12 * mm)
    text(c, 23 * mm, H - 77.5 * mm, "ご発注金額（税込）", 9)
    text(c, 102 * mm, H - 78 * mm, f"¥{yen(o['total_amount'])}-", 14, align="r")
    # 納品情報
    y = table(c, 20 * mm, H - 90 * mm, [("希望納品日", 35 * mm, "l"), ("納品先", 135 * mm, "l")],
              [[o["delivery_date_text"], o["delivery_address"]]])
    cols = [("No.", 12 * mm, "c"), ("商品コード", 28 * mm, "l"), ("商品名", 50 * mm, "l"), ("規格", 22 * mm, "l"),
            ("数量", 15 * mm, "r"), ("単位", 13 * mm, "c"), ("単価", 15 * mm, "r"), ("金額", 15 * mm, "r")]
    rows = [[str(l["line_no"]), l["code"], l["name"], l["spec"], str(l["qty"]), l["unit"], yen(l["price"]),
             yen(l["line_amount_printed"])] for l in o["lines"]]
    rows += [[""] * 8 for _ in range(6 - len(rows))]
    y = table(c, 20 * mm, y - 8 * mm, cols, rows)
    # 合計
    for label, v in (("小計（税抜）", o["subtotal"]), ("消費税（10%）", o["tax_amount"]), ("合計（税込）", o["total_amount"])):
        c.rect(130 * mm, y - 8 * mm, 30 * mm, 8 * mm)
        c.rect(160 * mm, y - 8 * mm, 30 * mm, 8 * mm)
        text(c, 132 * mm, y - 5.5 * mm, label, 9)
        text(c, 188 * mm, y - 5.5 * mm, yen(v), 9, align="r")
        y -= 8 * mm
    text(c, 20 * mm, y - 6 * mm, "備考", 9)
    c.rect(20 * mm, y - 26 * mm, 170 * mm, 18 * mm)
    text(c, 23 * mm, y - 14 * mm, o["notes"], 9)
    text(c, 20 * mm, 15 * mm, DISCLAIMER, 7, color=colors.grey)
    c.showPage()
    c.save()


# ---------------------------------------------------------------- sample B (A4 landscape, 2 pages)
def draw_b(o, path):
    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    W, H = landscape(A4)
    c.setTitle(f"注文書 {o['order_number']}")
    cols = [("品番", 30 * mm, "l"), ("品名", 62 * mm, "l"), ("規格・仕様", 36 * mm, "l"), ("単価(円)", 30 * mm, "r"),
            ("数量", 20 * mm, "r"), ("単位", 16 * mm, "c"), ("金額(円)", 36 * mm, "r"), ("行", 12 * mm, "c")]

    def row(l):
        return [l["code"], l["name"], l["spec"], f"¥{yen(l['price'])}", str(l["qty"]), l["unit"],
                f"¥{yen(l['line_amount_printed'])}", str(l["line_no"])]

    # page 1 — ヘッダーは左サイドバー + 右上に注文番号
    c.setFillColor(colors.HexColor("#1F5F3F"))
    c.rect(0, H - 22 * mm, W, 22 * mm, fill=1, stroke=0)
    text(c, 15 * mm, H - 14 * mm, "AOBA LOGITECH  注文書 / PURCHASE ORDER", 16, color=colors.white)
    text(c, W - 15 * mm, H - 10 * mm, f"注文No. {o['order_number']}", 11, align="r", color=colors.white)
    text(c, W - 15 * mm, H - 17 * mm, "Page 1 / 2", 8, align="r", color=colors.white)
    info = [("注文日", o["order_date_text"]), ("注文者", o["buyer_name"]), ("ご担当", o["contact"]),
            ("仕入先", f"{SUPPLIER} 様"), ("希望納期", o["delivery_date_text"]), ("お届け先", o["delivery_address"])]
    y = H - 32 * mm
    for k, v in info:
        text(c, 15 * mm, y, f"■ {k}", 9, color=colors.HexColor("#1F5F3F"))
        text(c, 40 * mm, y, v, 9)
        y -= 6.5 * mm
    text(c, W - 15 * mm, H - 32 * mm, "取引条件：月末締め翌月末払い", 8, align="r")
    first, rest = o["lines"][:4], o["lines"][4:]
    yb = table(c, 15 * mm, y - 4 * mm, cols, [row(l) for l in first], header_fill=colors.HexColor("#CFE8D8"))
    text(c, W - 15 * mm, yb - 6 * mm, "（次頁へ続く）", 9, align="r")
    text(c, 15 * mm, 10 * mm, DISCLAIMER, 7, color=colors.grey)
    c.showPage()
    # page 2 — 明細の続き + 合計
    c.setFillColor(colors.HexColor("#1F5F3F"))
    c.rect(0, H - 14 * mm, W, 14 * mm, fill=1, stroke=0)
    text(c, 15 * mm, H - 9 * mm, f"注文書（続き）  注文No. {o['order_number']}", 11, color=colors.white)
    text(c, W - 15 * mm, H - 9 * mm, "Page 2 / 2", 8, align="r", color=colors.white)
    yb = table(c, 15 * mm, H - 24 * mm, cols, [row(l) for l in rest], header_fill=colors.HexColor("#CFE8D8"))
    y = yb - 10 * mm
    for label, v in (("小計", o["subtotal"]), ("消費税(10%)", o["tax_amount"]), ("合計金額", o["total_amount"])):
        text(c, 190 * mm, y, label, 10)
        text(c, 257 * mm, y, f"{yen(v)}円", 10, align="r")
        y -= 7 * mm
    text(c, 15 * mm, yb - 10 * mm, f"特記事項：{o['notes']}", 9)
    text(c, 15 * mm, 10 * mm, DISCLAIMER, 7, color=colors.grey)
    c.showPage()
    c.save()


# ---------------------------------------------------------------- sample C (scanned image PDF)
def draw_c_vector(o):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    text(c, 20 * mm, H - 22 * mm, "御 注 文 書", 20, font=MIN)
    text(c, W - 20 * mm, H - 18 * mm, f"PO番号 {o['order_number']}", 10, font=MIN, align="r")
    text(c, W - 20 * mm, H - 24 * mm, f"日付 {o['order_date_text']}", 10, font=MIN, align="r")
    text(c, 20 * mm, H - 36 * mm, f"{SUPPLIER} 殿", 11, font=MIN)
    text(c, 20 * mm, H - 46 * mm, f"注文者：{o['buyer_name']}　{o['contact']}", 9, font=MIN)
    text(c, 20 * mm, H - 52 * mm, f"納入場所：{o['delivery_address']}", 9, font=MIN)
    text(c, 20 * mm, H - 58 * mm, f"納期：{o['delivery_date_text']}　必着", 9, font=MIN)
    # 合計を明細の上に置く（読み取りが難しい配置）
    text(c, W - 20 * mm, H - 46 * mm, f"税抜計 {yen(o['subtotal'])}", 9, font=MIN, align="r")
    text(c, W - 20 * mm, H - 52 * mm, f"消費税 {yen(o['tax_amount'])}", 9, font=MIN, align="r")
    text(c, W - 20 * mm, H - 58 * mm, f"総合計 {yen(o['total_amount'])}", 10, font=MIN, align="r")
    cols = [("#", 8 * mm, "c"), ("コード", 24 * mm, "l"), ("品名", 48 * mm, "l"), ("仕様", 22 * mm, "l"),
            ("数", 14 * mm, "r"), ("単位", 14 * mm, "c"), ("@単価", 18 * mm, "r"), ("金額", 22 * mm, "r")]
    rows = [[str(l["line_no"]), l["code"], l["name"], l["spec"], str(l["qty"]), l["unit"], yen(l["price"]),
             yen(l["line_amount_printed"])] for l in o["lines"]]
    yb = table(c, 20 * mm, H - 66 * mm, cols, rows, row_h=7 * mm, header_fill=colors.HexColor("#EEEEEE"))
    text(c, 20 * mm, yb - 8 * mm, f"備考：{o['notes']}", 9, font=MIN)
    # 押印風
    c.setStrokeColor(colors.red)
    c.circle(W - 30 * mm, H - 36 * mm, 7 * mm)
    text(c, W - 30 * mm, H - 37.5 * mm, "みなと", 8, font=MIN, align="c", color=colors.red)
    c.setStrokeColor(colors.black)
    text(c, 20 * mm, 15 * mm, DISCLAIMER, 7, font=MIN, color=colors.grey)
    c.showPage()
    c.save()
    return buf.getvalue()


def draw_c(o, path):
    vec = fitz.open(stream=draw_c_vector(o), filetype="pdf")
    pix = vec[0].get_pixmap(dpi=150, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    rnd = random.Random(42)
    img = img.rotate(0.8, resample=Image.BICUBIC, expand=False, fillcolor=255)
    px = img.load()
    for _ in range(9000):  # スキャンノイズ
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = rnd.randint(120, 200)
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    jpg = io.BytesIO()
    img.save(jpg, format="JPEG", quality=70)
    out = fitz.open()
    page = out.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=jpg.getvalue())
    out.set_metadata({"title": "scan", "producer": "SnowVillage demo scanner"})
    out.save(str(path))


def expected(o):
    return {
        "file": o["file"], "customer_id": o["customer_id"],
        "header": {
            "order_number": o["order_number"], "order_date": o["order_date"], "buyer_name": o["buyer_name"],
            "supplier_name": SUPPLIER, "delivery_date": o["delivery_date"], "delivery_address": o["delivery_address"],
            "subtotal": o["subtotal"], "tax_amount": o["tax_amount"], "total_amount": o["total_amount"],
            "notes": o["notes"],
        },
        "lines": [{"line_number": l["line_no"], "product_code": l["code"], "product_name": l["name"],
                   "specification": l["spec"], "quantity": l["qty"], "unit": l["unit"], "unit_price": l["price"],
                   "line_amount": l["line_amount_printed"]} for l in o["lines"]],
    }


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    draw_a(ORDER_A, OUT / ORDER_A["file"])
    draw_a(ORDER_A_REV, OUT / ORDER_A_REV["file"])
    draw_b(ORDER_B, OUT / ORDER_B["file"])
    draw_c(ORDER_C, OUT / ORDER_C["file"])
    exp = {
        "order_company_a.pdf": {**expected(ORDER_A), "scenario": "正常系。全検証PASSで確定可能",
                                "expected_validation": "READY", "expected_product_codes": ["SV-CP-A4", "SV-TN-K01", "SV-CF-A4", "SV-BP-BK"]},
        "order_company_b.pdf": {**expected(ORDER_B), "scenario": "別レイアウト・独自品番・和暦・2ページ明細。標準化して確定可能",
                                "expected_validation": "READY",
                                "expected_product_codes": ["SV-CP-A4", "SV-TN-K01", "SV-ST-10", "SV-FF-A4", "SV-CF-A4"],
                                "expected_standard_quantity": [20, 3, 10, 2, 6]},
        "order_company_c.pdf": {**expected(ORDER_C), "scenario": "スキャン画像PDF。未登録商品MK-0420と明細2の金額不整合でERROR",
                                "expected_validation": "NEEDS_REVIEW",
                                "expected_errors": ["PRODUCT_MAPPING", "LINE_AMOUNT", "SUBTOTAL", "TAX"],
                                "fix_steps": [{"line_no": 3, "field": "PRODUCT_CODE", "value": "SV-CF-A4"},
                                              {"line_no": 2, "field": "LINE_AMOUNT", "value": 17600}]},
        "order_company_a_revised.pdf": {**expected(ORDER_A_REV), "scenario": "Aと同一発注番号・内容違い。確定済みAを上書きしない",
                                        "expected_errors": ["DUPLICATE_ORDER_NUMBER"]},
    }
    (OUT / "expected_results.json").write_text(json.dumps(exp, ensure_ascii=False, indent=2), encoding="utf-8")
    print("generated:", sorted(p.name for p in OUT.iterdir()))
