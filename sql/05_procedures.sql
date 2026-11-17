-- =====================================================================
-- SnowVillage 受注デモ: 05 ストアドプロシージャ
--   SNOWVILLAGE_READ_PDF: 受付IDの原本PDFをサーバー側で読み、Base64で返す
--   （クライアント側ファイル転送 get_stream が使えない／不安定な環境向けの代替経路）
--   引数は受付ID(UUID)のみ。任意のステージパスは受け付けない。
-- 実行ロール: SNOWVILLAGE_APP_OWNER_ROLE（EXECUTE AS OWNER）
-- =====================================================================
USE ROLE SNOWVILLAGE_APP_OWNER_ROLE;
USE WAREHOUSE SNOWVILLAGE_WH;

CREATE PROCEDURE IF NOT EXISTS SNOWVILLAGE_DB.SNOWVILLAGE_APP.SNOWVILLAGE_READ_PDF(DOCUMENT_ID VARCHAR)
RETURNS VARCHAR
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
COMMENT = '受付IDの原本PDFをBase64で返す（最大20MB）'
EXECUTE AS OWNER
AS
$$
import base64
import re
from snowflake.snowpark.files import SnowflakeFile

STAGE = "@SNOWVILLAGE_DB.SNOWVILLAGE_RAW.SNOWVILLAGE_PDF_STAGE"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

def run(session, document_id):
    if document_id is None or not UUID_RE.match(document_id):
        raise ValueError("invalid document id")
    with SnowflakeFile.open(f"{STAGE}/{document_id}/original.pdf", "rb", require_scoped_url=False) as f:
        data = f.read()
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("file too large")
    return base64.b64encode(data).decode("ascii")
$$;
