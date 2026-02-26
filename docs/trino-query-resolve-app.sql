-- =============================================================================
-- Resolve app_id from: app_id (passthrough), msid, or WP account_id
-- Returns one row: app_id, app_name, app_url, conversation_summary, msid, wp_account_id
-- Use for the UW internal app lookup. Run exactly ONE of the three queries below
-- depending on what the user submitted (app_id, msid, or wp_account_id).
-- =============================================================================

-- ---------- 1) Resolve by app_id (passthrough) ----------
-- Bind: app_id (e.g. '698406273ade17b9bd851188')
/*
SELECT
  ba.app_id,
  ba.app_name,
  ba.app_url,
  ba.msid,
  ap.account_id AS wp_account_id,
  m.conversation_summary
FROM prod_encrypted.payments.pp_base44_apps_replica ba
LEFT JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
LEFT JOIN prod.marketing.base44_app_context_snapshots_mongo m ON m.app_id = ba.app_id
WHERE ba.app_id = ?
*/

-- ---------- 2) Resolve by msid ----------
-- Bind: msid (e.g. 'c6ab1a9b-1830-4f53-a221-5d2ae0597796')
/*
SELECT
  ba.app_id,
  ba.app_name,
  ba.app_url,
  ba.msid,
  ap.account_id AS wp_account_id,
  m.conversation_summary
FROM prod_encrypted.payments.pp_base44_apps_replica ba
LEFT JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
LEFT JOIN prod.marketing.base44_app_context_snapshots_mongo m ON m.app_id = ba.app_id
WHERE ba.msid = ?
*/

-- ---------- 3) Resolve by WP account_id (WixPayments account) ----------
-- Bind: wp_account_id = ap.account_id (UUID)
/*
SELECT
  ba.app_id,
  ba.app_name,
  ba.app_url,
  ba.msid,
  ap.account_id AS wp_account_id,
  m.conversation_summary
FROM prod_encrypted.payments.pp_base44_apps_replica ba
INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
LEFT JOIN prod.marketing.base44_app_context_snapshots_mongo m ON m.app_id = ba.app_id
WHERE ap.account_id = ?
*/
