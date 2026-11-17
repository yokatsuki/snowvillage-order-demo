-- =====================================================================
-- SnowVillage 受注デモ: 04 権限付与（最小権限）
--   OWNER  : 全オブジェクト所有 + Cortex AI 利用 + Streamlit 作成
--   VIEWER : アプリの利用のみ（Streamlit は所有者権限で実行されるため
--            テーブル・ステージ・ウェアハウスへの直接権限は付与しない）
-- 実行者: SECURITYADMIN（Cortexデータベースロール）/ SNOWVILLAGE_APP_OWNER_ROLE
-- =====================================================================

-- Cortex AI 関数 (AI_EXTRACT / AI_PARSE_DOCUMENT) の利用権限
USE ROLE ACCOUNTADMIN;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE SNOWVILLAGE_APP_OWNER_ROLE;

USE ROLE SNOWVILLAGE_APP_OWNER_ROLE;
-- VIEWER: アプリを開くための USAGE のみ
GRANT USAGE ON DATABASE SNOWVILLAGE_DB TO ROLE SNOWVILLAGE_VIEWER_ROLE;
GRANT USAGE ON SCHEMA SNOWVILLAGE_DB.SNOWVILLAGE_APP TO ROLE SNOWVILLAGE_VIEWER_ROLE;

-- 以下はStreamlitデプロイ後に実行（deploy前はオブジェクトが存在しない）
GRANT USAGE ON STREAMLIT SNOWVILLAGE_DB.SNOWVILLAGE_APP.SNOWVILLAGE_ORDER_INTAKE_APP TO ROLE SNOWVILLAGE_VIEWER_ROLE;

-- 利用者への割り当て例（ユーザー名は環境に合わせて置き換え。SECURITYADMIN等で実行）
-- USE ROLE SECURITYADMIN;
-- GRANT ROLE SNOWVILLAGE_VIEWER_ROLE TO USER "<demo_user>";
