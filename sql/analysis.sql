-- MySQL 8.0+，项目专用数据库。先运行本文件建表/视图，再由 Python 导入 CSV。
-- 原始数据主键为车系×自然月。同比按日期连接，零分母返回 NULL。
-- scope='全样本' 的份额分母是全部样本，scope='MPV' 的分母仅为样本 MPV。
CREATE DATABASE IF NOT EXISTS auto_market_learning_20261018 CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
USE auto_market_learning_20261018;
SET SESSION div_precision_increment=12;
CREATE TABLE IF NOT EXISTS dim_series (
 series_id INT PRIMARY KEY, series_name VARCHAR(100) NOT NULL,
 brand VARCHAR(80) NOT NULL, category VARCHAR(30) NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_sales (
 series_id INT NOT NULL, date DATE NOT NULL, monthly_sales BIGINT NOT NULL,
 data_source VARCHAR(80), PRIMARY KEY(series_id,date), CHECK(monthly_sales>=0),
 FOREIGN KEY(series_id) REFERENCES dim_series(series_id)
);
CREATE TABLE IF NOT EXISTS config_year (
 series_id INT NOT NULL, year INT NOT NULL, energy_type VARCHAR(50), vehicle_class VARCHAR(50),
 official_price_wan DOUBLE, wheelbase_mm DOUBLE, car_name VARCHAR(255),future_name_flag BOOLEAN,
 PRIMARY KEY(series_id,year), FOREIGN KEY(series_id) REFERENCES dim_series(series_id)
);
CREATE OR REPLACE VIEW v_scope_sales AS
SELECT '全样本' AS scope,f.*,d.series_name,d.brand,d.category FROM fact_sales f JOIN dim_series d USING(series_id)
UNION ALL
SELECT 'MPV' AS scope,f.*,d.series_name,d.brand,d.category FROM fact_sales f JOIN dim_series d USING(series_id) WHERE d.category='MPV';
CREATE OR REPLACE VIEW v_series_month AS
SELECT f.*,p.monthly_sales AS previous_month,y.monthly_sales AS previous_year,
 (f.monthly_sales-p.monthly_sales)/NULLIF(p.monthly_sales,0) AS mom,
 (f.monthly_sales-y.monthly_sales)/NULLIF(y.monthly_sales,0) AS yoy
FROM fact_sales f LEFT JOIN fact_sales p ON f.series_id=p.series_id AND p.date=DATE_SUB(f.date,INTERVAL 1 MONTH)
LEFT JOIN fact_sales y ON f.series_id=y.series_id AND y.date=DATE_SUB(f.date,INTERVAL 1 YEAR);
CREATE OR REPLACE VIEW v_brand_month_base AS
SELECT scope,brand,date,SUM(monthly_sales) AS monthly_sales FROM v_scope_sales GROUP BY scope,brand,date;
CREATE OR REPLACE VIEW v_brand_month_rank AS
SELECT *,monthly_sales/NULLIF(SUM(monthly_sales) OVER(PARTITION BY scope,date),0) AS share,
 CASE WHEN monthly_sales>0 THEN DENSE_RANK() OVER(PARTITION BY scope,date ORDER BY monthly_sales DESC) END AS sales_rank
FROM v_brand_month_base;
CREATE OR REPLACE VIEW v_brand_month AS
SELECT a.*,p.monthly_sales AS previous_month,y.monthly_sales AS previous_year,
 (a.monthly_sales-p.monthly_sales)/NULLIF(p.monthly_sales,0) AS mom,
 (a.monthly_sales-y.monthly_sales)/NULLIF(y.monthly_sales,0) AS yoy,
 CAST(p.sales_rank AS SIGNED)-CAST(a.sales_rank AS SIGNED) AS rank_gain,
 LAG(a.monthly_sales) OVER(PARTITION BY a.scope,a.brand ORDER BY a.date) AS lag_month_sales
FROM v_brand_month_rank a
LEFT JOIN v_brand_month_rank p ON a.scope=p.scope AND a.brand=p.brand AND p.date=DATE_SUB(a.date,INTERVAL 1 MONTH)
LEFT JOIN v_brand_month_rank y ON a.scope=y.scope AND a.brand=y.brand AND y.date=DATE_SUB(a.date,INTERVAL 1 YEAR);
CREATE OR REPLACE VIEW v_periods AS
SELECT 'FY2025' AS comparison,DATE('2024-01-01') AS base_start,DATE('2025-01-01') AS current_start,DATE('2026-01-01') AS current_end
UNION ALL SELECT 'H12026',DATE('2025-01-01'),DATE('2026-01-01'),DATE('2026-07-01');
CREATE OR REPLACE VIEW v_series_period AS
SELECT s.scope,p.comparison,s.series_id,s.series_name,s.brand,
 SUM(CASE WHEN s.date>=p.base_start AND s.date<DATE_SUB(p.current_end,INTERVAL 1 YEAR) THEN s.monthly_sales ELSE 0 END) AS base_sales,
 SUM(CASE WHEN s.date>=p.current_start AND s.date<p.current_end THEN s.monthly_sales ELSE 0 END) AS current_sales
FROM v_scope_sales s CROSS JOIN v_periods p GROUP BY s.scope,p.comparison,s.series_id,s.series_name,s.brand;
CREATE OR REPLACE VIEW v_brand_period_base AS
SELECT scope,comparison,brand,SUM(base_sales) AS base_sales,SUM(current_sales) AS current_sales,
 SUM(current_sales)-SUM(base_sales) AS delta FROM v_series_period GROUP BY scope,comparison,brand;
CREATE OR REPLACE VIEW v_brand_period_rank AS
SELECT *,delta/NULLIF(base_sales,0) AS yoy,
 base_sales/NULLIF(SUM(base_sales) OVER(PARTITION BY scope,comparison),0) AS base_share,
 current_sales/NULLIF(SUM(current_sales) OVER(PARTITION BY scope,comparison),0) AS current_share,
 CASE WHEN base_sales>0 THEN DENSE_RANK() OVER(PARTITION BY scope,comparison ORDER BY base_sales DESC) END AS base_rank,
 CASE WHEN current_sales>0 THEN DENSE_RANK() OVER(PARTITION BY scope,comparison ORDER BY current_sales DESC) END AS current_rank
FROM v_brand_period_base;
CREATE OR REPLACE VIEW v_brand_period AS
SELECT *,100*(current_share-base_share) AS share_change_pp,CAST(base_rank AS SIGNED)-CAST(current_rank AS SIGNED) AS rank_gain FROM v_brand_period_rank;
CREATE OR REPLACE VIEW v_series_contribution AS
SELECT *,current_sales-base_sales AS delta,
 SUM(current_sales-base_sales) OVER(PARTITION BY scope,comparison,brand) AS brand_net_delta,
 GREATEST(current_sales-base_sales,0)/NULLIF(SUM(GREATEST(current_sales-base_sales,0)) OVER(PARTITION BY scope,comparison,brand),0) AS positive_contribution,
 CASE WHEN SUM(current_sales-base_sales) OVER(PARTITION BY scope,comparison,brand)>0
 THEN (current_sales-base_sales)/SUM(current_sales-base_sales) OVER(PARTITION BY scope,comparison,brand) END AS net_contribution
FROM v_series_period;
CREATE OR REPLACE VIEW v_mpv_concentration AS
WITH annual AS (
 SELECT YEAR(date) AS year,brand,SUM(monthly_sales) AS sales FROM v_scope_sales
 WHERE scope='MPV' AND YEAR(date)<=2025 GROUP BY YEAR(date),brand
), ranked AS (
 SELECT *,sales/SUM(sales) OVER(PARTITION BY year) AS share,
 ROW_NUMBER() OVER(PARTITION BY year ORDER BY sales DESC,brand) AS position FROM annual
)
SELECT year,SUM(sales) AS sales,SUM(CASE WHEN position<=3 THEN share ELSE 0 END) AS cr3,
SUM(CASE WHEN position<=5 THEN share ELSE 0 END) AS cr5,SUM(share*share)*10000 AS hhi
FROM ranked GROUP BY year;
CREATE OR REPLACE VIEW v_product_snapshot AS
WITH ranked AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY series_id ORDER BY year DESC) AS rn FROM config_year WHERE year<=2026)
SELECT d.series_id,d.series_name,d.brand,d.category,c.year AS config_year,c.energy_type,c.vehicle_class,
c.official_price_wan,c.wheelbase_mm,c.car_name,c.future_name_flag
FROM dim_series d LEFT JOIN ranked c ON d.series_id=c.series_id AND c.rn=1;
-- 在 Workbench 中可单独运行下面示例（取消注释）。
-- SELECT * FROM v_brand_period WHERE scope='MPV' AND comparison='FY2025' ORDER BY delta DESC;
-- SELECT * FROM v_series_contribution WHERE scope='MPV' AND comparison='FY2025' AND brand='别克' ORDER BY delta DESC;
-- SELECT * FROM v_brand_month WHERE scope='MPV' AND brand='魏牌' ORDER BY date;
-- SELECT * FROM v_mpv_concentration ORDER BY year;
