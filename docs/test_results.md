# テスト結果

実行日: 2026-11-11 / 環境: 検証用アカウント（AWS ap-northeast-1）/ ロール SNOWVILLAGE_APP_OWNER_ROLE / WH SNOWVILLAGE_WH (X-SMALL)
実行場所: Cortex Code サンドボックスから Snowpark で実 Snowflake に接続（アプリと同じサービス層のコードを使用）

## サマリー

| 区分 | 件数 | 結果 |
|---|---|---|
| 決定論テスト（`test_normalization.py`, `test_validation.py`） | 56 | **56 passed** |
| E2E ワークフロー（`test_order_workflow.py`・実 AI_EXTRACT） | 16 | **16 passed** |
| AI 抽出精度（期待値との一致率・揺らぎあり） | 3 | **3 passed**（A 26/26, B 31/31, C 26/26 = 100%） |
| 画面スモーク（`ui_smoke.py`・AppTest・実データ） | 4画面 + 確認画面×6状態 | 例外なし |
| 画面操作（AppTest で「受注確定」クリック → 実テーブル更新） | 1 | B が CONFIRMED、確定ビューに5明細 |

E2E は最終実行（全18件 passed, 約6分）。1回目の実行では2件失敗し、修正後に再実行で合格:
- 監査ログの修正前値が `18600.00` と記録され期待値 `18600` と不一致 → Decimal の表記を正規化
- サンドボックスからの `get_stream` が断続的に HTTP 400 → 原本取得に再試行と、サーバー側プロシージャ `SNOWVILLAGE_READ_PDF` による代替経路を追加

## 受入条件との対応

| 受入条件 | テスト | 結果 |
|---|---|---|
| サンプルAをアップロードし、PDFがステージへ正常保存される | test_10（LIST でパス・サイズ一致、STAGE_VERIFIED） | ✅ |
| サンプルAの発注番号・取引先・商品明細が正しく抽出される | test_11, accuracy[A] | ✅ |
| サンプルBでレイアウトの違いを吸収できる | test_20（注文No./和暦 R8.11.20/2ページ明細/「円」「¥」付き金額） | ✅ |
| サンプルBの取引先商品コードが自社商品コードへ変換される | test_20（AL-1001→SV-CP-A4 他5件、4箱→20冊） | ✅ |
| サンプルCで検証ERRORが表示され、受注確定が禁止される | test_30（PRODUCT_MAPPING, LINE_AMOUNT 他）/ confirm 拒否 / ui_smoke で確定ボタン非表示 | ✅ |
| サンプルCの不備を修正し、再検証・確定できる | test_31（サービス層経由。修正前後が監査ログに残る） | ✅ ※1 |
| サンプルAを再アップロードすると重複検出される | test_13（DUPLICATE、受注は作成されない） | ✅ |
| 同じ発注番号で内容の異なるPDFが届いた場合、無断で上書きされない | test_40（DUPLICATE_ORDER_NUMBER ERROR、元の確定値保持） | ✅ |
| AI抽出失敗時にFAILEDとなり、再処理が可能 | test_50（原本消失→FAILED→復旧→再処理成功・両実行履歴保持）, test_51（応答 error） | ✅ |
| 受注確定後、一覧に反映される | test_12（確定ビュー4明細）, AppTest 確定クリック | ✅ |
| 画面を再読み込みしても受付・処理履歴が残る | 状態はすべて永続テーブル。AppTest の新規セッションで既存受付を全状態復元 | ✅ ※2 |
| 原本PDFと抽出結果・検証結果を受付IDから追跡できる | test_70（原本取得・実行履歴・状態遷移・監査・検証結果） | ✅ |
| 画面の操作に応じてSnowflakeの実テーブルが更新される | AppTest「受注確定」クリック → DOCUMENTS/ORDER_HEADERS 更新を確認 | ✅ ※1 |
| 訂正版として確定（理由必須・旧版はSUPERSEDEDで保持・確定ビューは訂正版のみ） | test_65、AppTestで理由入力→チェック→ボタンクリックまで実施 | ✅ |
| 確定済み受注の再処理で既存確定を無断で上書きしない | test_60（CONFIRMED+DRAFT → 確定で SUPERSEDED、差分記録） | ✅ |
| 完全性（処理結果のない受付の検出） | test_71（失敗・確認待ちを検出、ステージ LIST 突き合わせ） | ✅ |
| PDF以外は受け付けない（実体検証） | test_01（拡張子 .pdf の ZIP を拒否、.txt を拒否） | ✅ |

※1 明細の修正操作（`st.data_editor` への入力）は AppTest で操作できないため、同じ `save_edits` をサービス層から呼んで検証。
※2 ブラウザでの実リロードは未実施（下記）。

## 記録された E2E 実行時の完全性集計（テストデータ削除前のスナップショット）

```
受付試行 6 / 重複 1 / 受付 5 / 保存成功 5 / 抽出実行 5 / 抽出成功 5 / 検証合格 4 / 確定 3
確認待ち(ERROR) 1（同一発注番号の改訂版）/ 確定可能 1 / 失敗 1（障害注入）
```

## AI 抽出（参考値・揺らぎあり）

| サンプル | 処理時間 | スコア（ヘッダー/明細表） | 一致率 |
|---|---|---|---|
| A（A4縦・テキストPDF） | 約4.8秒 | 0.95 / 0.98 | 26/26 |
| B（A4横・2ページ） | 約5.5秒 | 0.95 / 0.98 | 31/31 |
| C（スキャン画像PDF・テキスト層なし） | 約4.7秒 | 0.92 / 0.98 | 26/26 |

AI_PARSE_DOCUMENT による補助解析は不要だったため使用していません。

## Streamlit in Snowflake 環境での確認

| 項目 | 状態 |
|---|---|
| `snow streamlit deploy` 成功・`SHOW/DESCRIBE STREAMLIT` で存在確認 | ✅ runtime_name = `SYSTEM$WAREHOUSE_RUNTIME`, user_packages = `streamlit==1.52.2,pandas,pypdfium2,snowflake-snowpark-python` |
| 利用者ロールへの付与 | ✅ VIEWER は DB/スキーマ/Streamlit の USAGE のみ |
| 所有者権限のサーバー側 Python で put_stream / LIST / get_stream / pypdfium2 | ✅（匿名プロシージャで検証） |
| `st.file_uploader` のウェアハウスランタイム対応 | ✅ 公式ドキュメントで確認（非対応は Native App 内のみ。上限200MB） |
| **Snowsight 上でアプリを開いて行う実画面操作** | ❌ **未検証**（サンドボックスからブラウザ操作ができないため） |

デモ前に Snowsight で一度、A のアップロード → AIで読み取り → 確定までを実行して確認してください。
