-- sync_price_cache_cloud.sql — 增量同步 price_cache 到云端 portfoliom2-pg
-- 策略：CREATE TEMP TABLE → COPY CSV → INSERT WHERE NOT EXISTS (幂等)
-- 只插入云端不存在的 (stock_code, trade_date) 行，不覆盖已有数据

-- 1. 创建临时表（与 price_cache 相同 schema，去掉 id 自增列）
CREATE TEMP TABLE pc_import (
    stock_code VARCHAR(20),
    trade_date DATE,
    open_px FLOAT,
    close_px FLOAT,
    high_px FLOAT,
    low_px FLOAT,
    volume FLOAT,
    source VARCHAR(20),
    created_at TIMESTAMP
);

-- 2. 从 CSV 导入到临时表
COPY pc_import FROM '/tmp/price_cache_holdings.csv' WITH CSV HEADER;

-- 3. 查看导入行数
SELECT COUNT(*) AS imported_rows FROM pc_import;

-- 4. 增量插入：只插入云端不存在的行
INSERT INTO price_cache (stock_code, trade_date, open_px, close_px, high_px, low_px, volume, source, created_at)
SELECT stock_code, trade_date, open_px, close_px, high_px, low_px, volume, source, created_at
FROM pc_import i
WHERE NOT EXISTS (
    SELECT 1 FROM price_cache p
    WHERE p.stock_code = i.stock_code AND p.trade_date = i.trade_date
);

-- 5. 验证：持仓代码的总行数
SELECT COUNT(*) AS total_pc_rows, COUNT(DISTINCT stock_code) AS unique_codes
FROM price_cache
WHERE stock_code IN (SELECT DISTINCT security_code FROM holdings);

-- 6. 验证：每个代码的覆盖范围
SELECT stock_code, COUNT(*) AS rows, MIN(trade_date) AS min_d, MAX(trade_date) AS max_d
FROM price_cache
WHERE stock_code IN (SELECT DISTINCT security_code FROM holdings)
GROUP BY stock_code
ORDER BY stock_code;
