-- =====================================================================
-- SnowVillage 受注デモ: 99 後片付け（SNOWVILLAGE 専用リソースのみ削除）
--
--   ！！ このファイルはセットアップやテストからは呼び出しません ！！
--   手順:
--     1) STEP 1 の確認クエリだけを実行し、削除対象が SNOWVILLAGE_ 接頭辞の
--        専用リソースのみであることを確認する
--     2) 問題なければ STEP 2 の各行のコメントを外して実行する
--   削除対象: Streamlit アプリ / SNOWVILLAGE_DB（スキーマ・テーブル・ステージ・
--             プロシージャを含む）/ SNOWVILLAGE_WH / SNOWVILLAGE_* ロール 2 つ
--   SNOWFLAKE.CORTEX_USER の付与は対象ロールの削除とともに消えます。
--   アカウント設定（クロスリージョン推論など）は本デモで変更していないため対象外。
-- =====================================================================

-- ---------- STEP 1: 削除対象の確認 ----------
USE ROLE SYSADMIN;
SHOW STREAMLITS LIKE 'SNOWVILLAGE%' IN ACCOUNT;
SHOW DATABASES  LIKE 'SNOWVILLAGE%';
SHOW WAREHOUSES LIKE 'SNOWVILLAGE%';
SHOW ROLES      LIKE 'SNOWVILLAGE%';
-- DB 内に想定外のオブジェクトがないか（他用途で使われていないか）確認
SHOW OBJECTS IN DATABASE SNOWVILLAGE_DB;

-- ---------- STEP 2: 削除（確認後にコメントを外して実行） ----------
-- USE ROLE SNOWVILLAGE_APP_OWNER_ROLE;
-- DROP STREAMLIT IF EXISTS SNOWVILLAGE_DB.SNOWVILLAGE_APP.SNOWVILLAGE_ORDER_INTAKE_APP;
-- DROP DATABASE  IF EXISTS SNOWVILLAGE_DB;
-- DROP WAREHOUSE IF EXISTS SNOWVILLAGE_WH;
-- USE ROLE USERADMIN;
-- DROP ROLE IF EXISTS SNOWVILLAGE_VIEWER_ROLE;
-- DROP ROLE IF EXISTS SNOWVILLAGE_APP_OWNER_ROLE;
