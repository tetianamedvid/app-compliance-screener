-- Earliest conversation preview per app from prod.base44.base44_conversation_messages_mongo
-- For WP-connected apps. Output: app_id, earliest_conversation_first_at, earliest_conversation_preview
-- Use for MCP or direct Trino; save to data/trino_earliest_conversation_preview.json

WITH filter_app_ids AS (
  SELECT ba.app_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
  WHERE ap.account_id IS NOT NULL
),
earliest_conversation_per_app AS (
  SELECT app_id, conversation_id AS earliest_conversation_id, first_ts AS earliest_conversation_first_at, msg_cnt AS earliest_conversation_message_count
  FROM (
    SELECT app_id, conversation_id, MIN(created_date) AS first_ts, COUNT(*) AS msg_cnt,
      ROW_NUMBER() OVER (PARTITION BY app_id ORDER BY MIN(created_date) NULLS LAST) AS rn
    FROM prod.base44.base44_conversation_messages_mongo
    WHERE app_id IS NOT NULL
    GROUP BY app_id, conversation_id
  ) t
  WHERE rn = 1
),
earliest_conversation_preview_per_app AS (
  SELECT e.app_id, e.earliest_conversation_first_at,
    array_join(array_agg(m.role || ': ' || substr(cast(coalesce(m.content, '') AS varchar), 1, 300) ORDER BY m.created_date), chr(10)) AS earliest_conversation_preview
  FROM earliest_conversation_per_app e
  JOIN (
    SELECT app_id, conversation_id, role, content, created_date,
      ROW_NUMBER() OVER (PARTITION BY app_id, conversation_id ORDER BY created_date) AS rn
    FROM prod.base44.base44_conversation_messages_mongo
  ) m ON m.app_id = e.app_id AND m.conversation_id = e.earliest_conversation_id AND m.rn <= 10
  GROUP BY e.app_id, e.earliest_conversation_first_at
)
SELECT f.app_id, ecp.earliest_conversation_first_at, ecp.earliest_conversation_preview
FROM filter_app_ids f
LEFT JOIN earliest_conversation_preview_per_app ecp ON ecp.app_id = f.app_id
ORDER BY f.app_id
