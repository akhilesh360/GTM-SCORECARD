-- Standard channel funnel: spend -> leads -> opportunities -> won -> revenue, per portfolio company.
-- Corrections vs the design-doc pseudo-SQL:
--   1. Opportunities use stage_reached, so deals that were qualified and later Lost still count
--      (filtering on current stage IN ('Qualified','Proposal','Won') undercounts opps and inflates win rate).
--   2. Gross margin is averaged inside the won CTE; joining deals again in the final SELECT
--      fans out the rows and multiplies spend.
--   3. Every CTE is keyed by portfolio_company so one query serves the whole portfolio.
WITH spend_c AS (
    SELECT portfolio_company, channel, SUM(spend) AS total_spend
    FROM spend
    GROUP BY portfolio_company, channel
),
leads_c AS (
    SELECT portfolio_company, lead_source AS channel, COUNT(*) AS leads
    FROM leads
    GROUP BY portfolio_company, lead_source
),
opps_c AS (
    SELECT portfolio_company, lead_source AS channel, COUNT(*) AS opps,
           SUM(CASE WHEN stage IN ('Won', 'Lost') THEN 1 ELSE 0 END) AS closed_opps
    FROM deals
    WHERE stage_reached IN ('Qualified', 'Proposal', 'Won')
    GROUP BY portfolio_company, lead_source
),
won_c AS (
    SELECT portfolio_company, lead_source AS channel, COUNT(*) AS won, SUM(amount) AS revenue,
           SUM(amount * gross_margin) / NULLIF(SUM(CASE WHEN gross_margin IS NOT NULL THEN amount END), 0) AS avg_margin
    FROM deals
    WHERE stage = 'Won'
    GROUP BY portfolio_company, lead_source
)
SELECT
    s.portfolio_company,
    s.channel,
    s.total_spend,
    COALESCE(l.leads, 0)        AS leads,
    COALESCE(o.opps, 0)         AS opps,
    COALESCE(o.closed_opps, 0)  AS closed_opps,
    COALESCE(w.won, 0)          AS won,
    COALESCE(w.revenue, 0)      AS revenue,
    s.total_spend / NULLIF(w.won, 0)      AS cac,
    w.revenue / NULLIF(s.total_spend, 0)  AS roas,
    w.avg_margin
FROM spend_c s
LEFT JOIN leads_c l ON l.portfolio_company = s.portfolio_company AND l.channel = s.channel
LEFT JOIN opps_c  o ON o.portfolio_company = s.portfolio_company AND o.channel = s.channel
LEFT JOIN won_c   w ON w.portfolio_company = s.portfolio_company AND w.channel = s.channel
ORDER BY s.portfolio_company, s.total_spend DESC;
