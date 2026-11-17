-- =====================================================================
-- SnowVillage 受注デモ: 02 マスタデータ投入（すべて架空）
--   実行順: 01_setup.sql -> 03_tables.sql -> 02_master_data.sql -> 04_grants.sql
--   MERGE による冪等投入（再実行しても重複しない）
-- =====================================================================
USE ROLE SNOWVILLAGE_APP_OWNER_ROLE;
USE WAREHOUSE SNOWVILLAGE_WH;
USE SCHEMA SNOWVILLAGE_DB.SNOWVILLAGE_CORE;

-- 税率（税区分で判定。コードに税率を埋め込まない）
MERGE INTO SNOWVILLAGE_TAX_RATES t
USING (SELECT * FROM VALUES
  ('STANDARD', 0.10, '2019-10-01'::DATE, NULL::DATE, '標準税率 10%'),
  ('REDUCED',  0.08, '2019-10-01'::DATE, NULL::DATE, '軽減税率 8%')
  AS s(TAX_CATEGORY, TAX_RATE, VALID_FROM, VALID_TO, DESCRIPTION)) s
ON t.TAX_CATEGORY = s.TAX_CATEGORY AND t.VALID_FROM = s.VALID_FROM
WHEN MATCHED THEN UPDATE SET TAX_RATE = s.TAX_RATE, VALID_TO = s.VALID_TO, DESCRIPTION = s.DESCRIPTION
WHEN NOT MATCHED THEN INSERT VALUES (s.TAX_CATEGORY, s.TAX_RATE, s.VALID_FROM, s.VALID_TO, s.DESCRIPTION);

-- 取引先（架空）
MERGE INTO SNOWVILLAGE_CUSTOMERS t
USING (
  SELECT 'C001' id, '株式会社さくら文具商事' nm, ARRAY_CONSTRUCT('さくら文具商事', '(株)さくら文具商事', 'サクラ文具商事') al, TRUE act UNION ALL
  SELECT 'C002', '青葉ロジテック株式会社', ARRAY_CONSTRUCT('青葉ロジテック', '青葉ロジテック(株)', 'AOBA LOGITECH'), TRUE UNION ALL
  SELECT 'C003', 'みなと工務店株式会社', ARRAY_CONSTRUCT('みなと工務店', '(株)みなと工務店', 'ミナト工務店'), TRUE UNION ALL
  SELECT 'C099', '旧取引 ひまわり事務機株式会社', ARRAY_CONSTRUCT('ひまわり事務機'), FALSE
) s ON t.CUSTOMER_ID = s.id
WHEN MATCHED THEN UPDATE SET OFFICIAL_NAME = s.nm, NAME_ALIASES = s.al, IS_ACTIVE = s.act
WHEN NOT MATCHED THEN INSERT (CUSTOMER_ID, OFFICIAL_NAME, NAME_ALIASES, IS_ACTIVE) VALUES (s.id, s.nm, s.al, s.act);

-- 商品（自社）
MERGE INTO SNOWVILLAGE_PRODUCTS t
USING (SELECT * FROM VALUES
  ('SV-CP-A4',  'コピー用紙 A4 500枚',            '冊',     480,  'STANDARD', TRUE),
  ('SV-TN-K01', 'トナーカートリッジ 黒 TK-01',     '本',     8800, 'STANDARD', TRUE),
  ('SV-CF-A4',  'クリアファイル A4 10枚入',        'パック', 350,  'STANDARD', TRUE),
  ('SV-BP-BK',  'ボールペン 黒 0.5mm 10本入',      '箱',     900,  'STANDARD', TRUE),
  ('SV-ST-10',  'ホッチキス針 No.10 1000本入',     '箱',     120,  'STANDARD', TRUE),
  ('SV-FF-A4',  'フラットファイル A4 青 10冊入',   'パック', 1200, 'STANDARD', TRUE)
  AS s(PRODUCT_CODE, PRODUCT_NAME, STANDARD_UNIT, REFERENCE_PRICE, TAX_CATEGORY, IS_ACTIVE)) s
ON t.PRODUCT_CODE = s.PRODUCT_CODE
WHEN MATCHED THEN UPDATE SET PRODUCT_NAME = s.PRODUCT_NAME, STANDARD_UNIT = s.STANDARD_UNIT,
  REFERENCE_PRICE = s.REFERENCE_PRICE, TAX_CATEGORY = s.TAX_CATEGORY, IS_ACTIVE = s.IS_ACTIVE
WHEN NOT MATCHED THEN INSERT VALUES (s.PRODUCT_CODE, s.PRODUCT_NAME, s.STANDARD_UNIT, s.REFERENCE_PRICE, s.TAX_CATEGORY, s.IS_ACTIVE);

-- 取引先商品コード対応
--   C001 さくら: 自社コードをそのまま使う取引先（対応表にも登録）
--   C002 青葉  : 独自コード。コピー用紙は「箱(=5冊)」単位で発注、トナーは契約単価あり
--   C003 みなと: 独自コード（ラベル類は未登録 → 照合エラーのデモ）
MERGE INTO SNOWVILLAGE_CUSTOMER_PRODUCT_MAP t
USING (SELECT * FROM VALUES
  ('C001', 'SV-CP-A4',  'SV-CP-A4',  'コピー用紙 A4 500枚',        '冊',     1, NULL, TRUE),
  ('C001', 'SV-TN-K01', 'SV-TN-K01', 'トナーカートリッジ 黒',       '本',     1, NULL, TRUE),
  ('C001', 'SV-CF-A4',  'SV-CF-A4',  'クリアファイル A4',           'パック', 1, NULL, TRUE),
  ('C001', 'SV-BP-BK',  'SV-BP-BK',  'ボールペン 黒',               '箱',     1, NULL, TRUE),
  ('C002', 'AL-1001',   'SV-CP-A4',  'PPC用紙 A4 (5冊/箱)',         '箱',     5, 2400, TRUE),
  ('C002', 'AL-2050',   'SV-TN-K01', 'レーザートナー BK',           '個',     1, 8500, TRUE),
  ('C002', 'AL-3300',   'SV-ST-10',  'ステープル針 #10',            '箱',     1, NULL, TRUE),
  ('C002', 'AL-3410',   'SV-FF-A4',  'フラットファイル 青',         'P',      1, NULL, TRUE),
  ('C002', 'AL-3120',   'SV-CF-A4',  'クリアホルダー A4',           'P',      1, NULL, TRUE),
  ('C003', 'MK-0101',   'SV-CP-A4',  '事務用紙 A4',                 '冊',     1, NULL, TRUE),
  ('C003', 'MK-0205',   'SV-TN-K01', 'トナー 黒',                   '本',     1, NULL, TRUE),
  ('C003', 'MK-0310',   'SV-BP-BK',  'ボールペン 黒',               '箱',     1, NULL, TRUE)
  AS s(CUSTOMER_ID, CUSTOMER_PRODUCT_CODE, PRODUCT_CODE, CUSTOMER_PRODUCT_NAME, CUSTOMER_UNIT, UNIT_CONVERSION_FACTOR, CONTRACT_UNIT_PRICE, IS_ACTIVE)) s
ON t.CUSTOMER_ID = s.CUSTOMER_ID AND t.CUSTOMER_PRODUCT_CODE = s.CUSTOMER_PRODUCT_CODE
WHEN MATCHED THEN UPDATE SET PRODUCT_CODE = s.PRODUCT_CODE, CUSTOMER_PRODUCT_NAME = s.CUSTOMER_PRODUCT_NAME,
  CUSTOMER_UNIT = s.CUSTOMER_UNIT, UNIT_CONVERSION_FACTOR = s.UNIT_CONVERSION_FACTOR,
  CONTRACT_UNIT_PRICE = s.CONTRACT_UNIT_PRICE, IS_ACTIVE = s.IS_ACTIVE
WHEN NOT MATCHED THEN INSERT VALUES (s.CUSTOMER_ID, s.CUSTOMER_PRODUCT_CODE, s.PRODUCT_CODE, s.CUSTOMER_PRODUCT_NAME,
  s.CUSTOMER_UNIT, s.UNIT_CONVERSION_FACTOR, s.CONTRACT_UNIT_PRICE, s.IS_ACTIVE);
