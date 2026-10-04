-- Multi-touch attribution of closed-won revenue. A won deal's journey is every touch on its lead
-- up to the moment the deal was created. Each model splits the deal amount across those touches.
-- Grain: company x close month x channel, so dashboards can filter by date.
WITH journey AS (
    SELECT d.portfolio_company, substr(d.close_date, 1, 7) AS close_month, d.deal_id, d.amount,
           t.channel, t.touch_datetime,
           ROW_NUMBER() OVER (PARTITION BY d.deal_id ORDER BY t.touch_datetime ASC)  AS pos_first,
           ROW_NUMBER() OVER (PARTITION BY d.deal_id ORDER BY t.touch_datetime DESC) AS pos_last,
           COUNT(*)     OVER (PARTITION BY d.deal_id)                                AS n_touches
    FROM deals d
    JOIN touchpoints t
      ON t.portfolio_company = d.portfolio_company AND t.lead_id = d.lead_id
     AND t.touch_datetime <= d.created_date
    WHERE d.stage = 'Won'
)
SELECT portfolio_company, close_month, channel,
       SUM(CASE WHEN pos_first = 1 THEN amount ELSE 0 END) AS first_touch_revenue,
       SUM(CASE WHEN pos_last  = 1 THEN amount ELSE 0 END) AS last_touch_revenue,
       SUM(amount * 1.0 / n_touches)                       AS linear_revenue,
       COUNT(DISTINCT deal_id)                             AS deals_touched
FROM journey
GROUP BY portfolio_company, close_month, channel
ORDER BY portfolio_company, close_month, channel;
