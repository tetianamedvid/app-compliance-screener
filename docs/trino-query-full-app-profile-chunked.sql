-- =============================================================================
-- Full App Profile – CHUNKED (same columns as trino-query-full-app-profile.sql, no truncation)
-- =============================================================================
-- Use this when Quix says the full result is "too big". Run this query multiple times,
-- each time changing LIMIT and OFFSET below. Export each run to a file (e.g. chunk_0.csv, chunk_1.csv).
-- Then merge:  python3 scripts/merge_export_chunks.py chunk_0.csv chunk_1.csv ... --out data/real_apps.json
--
-- Edit only these two numbers for each run:
--   CHUNK_SIZE: rows per run (e.g. 500). Smaller = smaller export per run.
--   OFFSET:     start row (0, 500, 1000, ... for chunk 0, 1, 2, ...).
-- =============================================================================

WITH filter_app_ids AS (
  SELECT app_id FROM (
    SELECT ba.app_id
    FROM prod_encrypted.payments.pp_base44_apps_replica ba
    INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
    WHERE ap.account_id IS NOT NULL
    ORDER BY ba.app_id
    LIMIT 500 OFFSET 0   -- Chunk 0: OFFSET 0. Chunk 1: OFFSET 500. Chunk 2: OFFSET 1000. ...
  ) t
),
app_owners AS (
  SELECT ab.app_id, ab.app_name, ab.owner_account_id AS app_owner_wix_account_id
  FROM prod.wt_apps.base ab
  INNER JOIN filter_app_ids f ON f.app_id = ab.app_id
  WHERE EXISTS (
    SELECT 1 FROM prod.users.base44_wix_user_mapping_dim m
    WHERE m.wix_parent_account_id = ab.owner_account_id AND m.mapping_end_date IS NULL
  )

  UNION ALL

  ( SELECT ug._id AS app_id, ug.name AS app_name, u.wix_account_id AS app_owner_wix_account_id
    FROM prod.base44.base44_apps_mongo_incmnt ug
    JOIN prod.base44.base44_users_mongo u ON u._id = ug.owner_id
    INNER JOIN filter_app_ids f ON f.app_id = ug._id
    WHERE (ug.is_deleted IS NULL OR ug.is_deleted = false)
    UNION
    SELECT ug._id AS app_id, ug.name AS app_name, u.wix_account_id AS app_owner_wix_account_id
    FROM prod.base44.base44_user_generated_apps_v2 ug
    JOIN prod.base44.base44_users_mongo u ON u._id = ug.owner_id
    INNER JOIN filter_app_ids f ON f.app_id = ug._id
    WHERE (ug.is_deleted IS NULL OR ug.is_deleted = false)
  )

  UNION ALL

  SELECT ba.app_id, ba.app_name, ba.msid AS app_owner_wix_account_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN filter_app_ids f ON f.app_id = ba.app_id
),

agent_conversations_per_app AS (
  SELECT app_id, COUNT(*) AS agent_conversations_count
  FROM prod.base44.base44_app_agents_conversations_mongo
  WHERE app_id IS NOT NULL
  GROUP BY app_id
),
support_conversations_per_app AS (
  SELECT app_id, COUNT(*) AS support_conversations_count
  FROM prod.base44.base44_support_conversations_mongo
  WHERE app_id IS NOT NULL
  GROUP BY app_id
),
conversation_messages_per_app AS (
  SELECT app_id, COUNT(*) AS conversation_messages_count
  FROM prod.base44.base44_conversation_messages_mongo
  WHERE app_id IS NOT NULL
  GROUP BY app_id
),
ownership_transfers_per_app AS (
  SELECT app_id, COUNT(*) AS ownership_transfers_count
  FROM prod.base44.base44_app_ownership_transfers_mongo
  WHERE app_id IS NOT NULL
  GROUP BY app_id
),
user_apps_logs_per_app AS (
  SELECT
    app_id,
    COUNT(*) AS user_app_events_count,
    MIN(created_date) AS first_activity_at,
    MAX(created_date) AS last_activity_at
  FROM prod.base44.base44_user_apps_logs_mongo
  WHERE app_id IS NOT NULL
  GROUP BY app_id
),
usage_per_app AS (
  SELECT
    app_id,
    COUNT(*) AS usage_record_count,
    MAX(created_date) AS last_usage_at
  FROM prod.base44.base44_usage_logs_mongo
  WHERE app_id IS NOT NULL
  GROUP BY app_id
),
integrations_per_app AS (
  SELECT app_id, COUNT(*) AS integrations_count
  FROM prod.base44.base44_app_integrations_mongo
  WHERE app_id IS NOT NULL
  GROUP BY app_id
),
catalog_items_per_app AS (
  SELECT app_id, COUNT(*) AS catalog_items_count
  FROM prod.base44.base44_app_catalog_items_mongo
  WHERE app_id IS NOT NULL
  GROUP BY app_id
),

-- messages: use substr to avoid Parquet "page size exceeds maximum" on oversized column
first_agent_conversation_per_app AS (
  SELECT
    app_id,
    _id AS first_agent_conversation_id,
    created_date AS first_agent_conversation_created_date,
    agent_name AS first_agent_conversation_agent_name,
    substr(cast(messages AS varchar), 1, 100000) AS first_agent_conversation_messages
  FROM (
    SELECT
      _id,
      app_id,
      created_date,
      agent_name,
      messages,
      ROW_NUMBER() OVER (PARTITION BY app_id ORDER BY created_date NULLS LAST, _id) AS rn
    FROM prod.base44.base44_app_agents_conversations_mongo
    WHERE app_id IS NOT NULL
  ) t
  WHERE rn = 1
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
  SELECT e.app_id,
    array_join(array_agg(m.role || ': ' || substr(cast(coalesce(m.content, '') AS varchar), 1, 300) ORDER BY m.created_date), chr(10)) AS earliest_conversation_preview
  FROM earliest_conversation_per_app e
  JOIN (
    SELECT app_id, conversation_id, role, content, created_date,
      ROW_NUMBER() OVER (PARTITION BY app_id, conversation_id ORDER BY created_date) AS rn
    FROM prod.base44.base44_conversation_messages_mongo
  ) m ON m.app_id = e.app_id AND m.conversation_id = e.earliest_conversation_id AND m.rn <= 10
  GROUP BY e.app_id
),

user_counts_per_owner AS (
  SELECT
    m.wix_parent_account_id AS wix_account_id,
    COUNT(DISTINCT m.base44_user_id) AS base44_user_count
  FROM prod.users.base44_wix_user_mapping_dim m
  WHERE m.mapping_end_date IS NULL
  GROUP BY m.wix_parent_account_id
),
wp_account_per_owner AS (
  SELECT ap.external_account_id AS wix_account_id, ap.account_id AS wp_account_id
  FROM prod.payments.wp_accounts_replica ap
  WHERE ap.external_account_id IS NOT NULL
)

SELECT
  a.app_id,
  a.app_name,
  a.app_owner_wix_account_id,
  COALESCE(ac.agent_conversations_count, 0)    AS agent_conversations_count,
  COALESCE(sc.support_conversations_count, 0)  AS support_conversations_count,
  COALESCE(cm.conversation_messages_count, 0)  AS conversation_messages_count,
  COALESCE(ot.ownership_transfers_count, 0)   AS ownership_transfers_count,
  fac.first_agent_conversation_id              AS first_agent_conversation_id,
  fac.first_agent_conversation_created_date    AS first_agent_conversation_created_date,
  fac.first_agent_conversation_agent_name      AS first_agent_conversation_agent_name,
  fac.first_agent_conversation_messages        AS first_agent_conversation_messages,
  ecp.earliest_conversation_id                 AS earliest_conversation_id,
  ecp.earliest_conversation_first_at           AS earliest_conversation_first_at,
  ecp.earliest_conversation_message_count      AS earliest_conversation_message_count,
  ecpreview.earliest_conversation_preview      AS earliest_conversation_preview,
  ual.first_activity_at,
  ual.last_activity_at                         AS user_apps_last_activity_at,
  COALESCE(ual.user_app_events_count, 0)      AS user_app_events_count,
  COALESCE(up.usage_record_count, 0)           AS usage_record_count,
  up.last_usage_at,
  COALESCE(ig.integrations_count, 0)           AS integrations_count,
  COALESCE(ci.catalog_items_count, 0)          AS catalog_items_count,
  acc.account_id                               AS wix_account_id,
  COALESCE(u.base44_user_count, 0)             AS base44_user_count,
  wp.wp_account_id                             AS linked_wp_account_id,
  m.conversation_summary                       AS app_context_conversation_summary,
  m.updated_date                               AS app_context_snapshot_updated,
  COALESCE(CAST(uga.additional_user_data_schema AS varchar), CAST(ugb.additional_user_data_schema AS varchar))   AS additional_user_data_schema,
  COALESCE(CAST(uga.agents AS varchar), CAST(ugb.agents AS varchar))                                             AS agents,
  COALESCE(CAST(uga.agents_enabled AS varchar), CAST(ugb.agents_enabled AS varchar))                             AS agents_enabled,
  COALESCE(CAST(uga.app_info AS varchar), CAST(ugb.app_info AS varchar))                                         AS app_info,
  COALESCE(CAST(uga.app_publish_info AS varchar), CAST(ugb.app_publish_info AS varchar))                         AS app_publish_info,
  COALESCE(CAST(uga.app_stage AS varchar), CAST(ugb.app_stage AS varchar))                                       AS app_stage,
  COALESCE(CAST(uga.app_type AS varchar), CAST(ugb.app_type AS varchar))                                         AS app_type,
  COALESCE(CAST(uga.backend_project AS varchar), CAST(ugb.backend_project AS varchar))                           AS backend_project,
  COALESCE(CAST(uga.captured_from_url AS varchar), CAST(ugb.captured_from_url AS varchar))                       AS captured_from_url,
  COALESCE(CAST(uga.categories AS varchar), CAST(ugb.categories AS varchar))                                     AS categories,
  COALESCE(CAST(uga.conversation AS varchar), CAST(ugb.conversation AS varchar))                                 AS conversation,
  COALESCE(CAST(uga.created_by_id AS varchar), CAST(ugb.created_by_id AS varchar))                               AS created_by_id,
  COALESCE(CAST(uga.created_date AS varchar), CAST(ugb.created_date AS varchar))                                 AS ugm_created_date,
  COALESCE(CAST(uga.custom_domain_suggestion_analysis_result AS varchar), CAST(ugb.custom_domain_suggestion_analysis_result AS varchar)) AS custom_domain_suggestion_analysis_result,
  COALESCE(CAST(uga.custom_domain_suggestion_count AS varchar), CAST(ugb.custom_domain_suggestion_count AS varchar)) AS custom_domain_suggestion_count,
  COALESCE(CAST(uga.custom_domain_suggestion_last_shown_count AS varchar), CAST(ugb.custom_domain_suggestion_last_shown_count AS varchar)) AS custom_domain_suggestion_last_shown_count,
  COALESCE(CAST(uga.custom_domain_suggestion_shown AS varchar), CAST(ugb.custom_domain_suggestion_shown AS varchar)) AS custom_domain_suggestion_shown,
  COALESCE(CAST(uga.custom_instructions AS varchar), CAST(ugb.custom_instructions AS varchar))                   AS custom_instructions,
  COALESCE(CAST(uga.custom_slug AS varchar), CAST(ugb.custom_slug AS varchar))                                   AS custom_slug,
  COALESCE(CAST(uga.has_backend_functions_enabled AS varchar), CAST(ugb.has_backend_functions_enabled AS varchar)) AS has_backend_functions_enabled,
  COALESCE(CAST(uga.has_non_prod_entities AS varchar), CAST(ugb.has_non_prod_entities AS varchar))                AS has_non_prod_entities,
  COALESCE(CAST(uga.hide_entity_created_by AS varchar), CAST(ugb.hide_entity_created_by AS varchar))              AS hide_entity_created_by,
  COALESCE(CAST(uga.installable_integrations AS varchar), CAST(ugb.installable_integrations AS varchar))         AS installable_integrations,
  COALESCE(CAST(uga.installed_integration_context_items AS varchar), CAST(ugb.installed_integration_context_items AS varchar)) AS installed_integration_context_items,
  COALESCE(CAST(uga.is_app_public AS varchar), CAST(ugb.is_app_public AS varchar))                                AS is_app_public,
  COALESCE(CAST(uga.is_blocked AS varchar), CAST(ugb.is_blocked AS varchar))                                     AS is_blocked,
  COALESCE(CAST(uga.status AS varchar), CAST(ugb.status AS varchar))                                             AS status,
  COALESCE(CAST(uga.technical_description AS varchar), CAST(ugb.technical_description AS varchar))               AS technical_description,
  COALESCE(CAST(uga.theme AS varchar), CAST(ugb.theme AS varchar))                                               AS theme,
  COALESCE(CAST(uga.unblocked_at AS varchar), CAST(ugb.unblocked_at AS varchar))                                 AS unblocked_at,
  COALESCE(CAST(uga.unblocked_by_script AS varchar), CAST(ugb.unblocked_by_script AS varchar))                   AS unblocked_by_script,
  COALESCE(CAST(uga.updated_date AS varchar), CAST(ugb.updated_date AS varchar))                                 AS ugm_updated_date,
  COALESCE(CAST(uga.use_agentic_builder AS varchar), CAST(ugb.use_agentic_builder AS varchar))                   AS use_agentic_builder,
  COALESCE(CAST(uga.user_description AS varchar), CAST(ugb.user_description AS varchar))                          AS user_description,
  COALESCE(CAST(uga.user_facing_chat_system_prompt AS varchar), CAST(ugb.user_facing_chat_system_prompt AS varchar)) AS user_facing_chat_system_prompt,
  ar.risk_level                                AS wp_account_risk_level,
  ar.manual_review_status                      AS wp_account_manual_review_status,
  ar.automatic_review_status                   AS wp_account_automatic_review_status
FROM app_owners a
LEFT JOIN agent_conversations_per_app ac ON ac.app_id = a.app_id
LEFT JOIN support_conversations_per_app sc ON sc.app_id = a.app_id
LEFT JOIN conversation_messages_per_app cm ON cm.app_id = a.app_id
LEFT JOIN first_agent_conversation_per_app fac ON fac.app_id = a.app_id
LEFT JOIN earliest_conversation_per_app ecp ON ecp.app_id = a.app_id
LEFT JOIN earliest_conversation_preview_per_app ecpreview ON ecpreview.app_id = a.app_id
LEFT JOIN ownership_transfers_per_app ot ON ot.app_id = a.app_id
LEFT JOIN user_apps_logs_per_app ual ON ual.app_id = a.app_id
LEFT JOIN usage_per_app up ON up.app_id = a.app_id
LEFT JOIN integrations_per_app ig ON ig.app_id = a.app_id
LEFT JOIN catalog_items_per_app ci ON ci.app_id = a.app_id
LEFT JOIN prod.wt_accounts.base acc ON acc.account_id = a.app_owner_wix_account_id
LEFT JOIN user_counts_per_owner u ON u.wix_account_id = a.app_owner_wix_account_id
LEFT JOIN wp_account_per_owner wp ON wp.wix_account_id = a.app_owner_wix_account_id
LEFT JOIN prod.marketing.base44_app_context_snapshots_mongo m ON m.app_id = a.app_id
LEFT JOIN prod.marketing.base44_user_generated_apps_v2_mongo uga ON uga._id = a.app_id
LEFT JOIN prod.base44.base44_user_generated_apps_v2 ugb ON ugb._id = a.app_id
LEFT JOIN prod.payments.wp_account_reviews_replica ar ON ar.account_id = wp.wp_account_id
ORDER BY a.app_id;
