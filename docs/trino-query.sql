-- Canonical query: WixPayments-connected apps + app owner conversation summary
-- Use this for underwriting pipeline data source.

SELECT
  ba.entity_id,
  ap.account_id,
  ar.manual_review_status,
  ba.date_updated,
  ba.app_id,
  ba.app_url,
  ba.app_name,
  ba._revision,
  ba.msid,
  ba.base44_email,
  ba.date_created,
  ba.is_production_app,
  ba._changes_counter,
  m.conversation_summary,
  ap.date_created
FROM prod_encrypted.payments.pp_base44_apps_replica ba
LEFT JOIN prod.marketing.base44_app_context_snapshots_mongo m ON ba.app_id = m.app_id
LEFT JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
LEFT JOIN prod.payments.wp_account_reviews_replica ar ON ar.account_id = ap.account_id
LEFT JOIN prod.base44.base44_app_agents_conversations_mongo am ON am.app_id = ba.app_id
WHERE ap.account_id IS NOT NULL
  -- AND ap.date_created >= NOW() - INTERVAL '48' HOUR  -- optional: recent only
  AND (
    (ba.base44_email NOT LIKE '%wix.com%' OR ba.base44_email NOT LIKE '%@base44.com%')
    OR ba.base44_email LIKE '%shalev%'
  );
