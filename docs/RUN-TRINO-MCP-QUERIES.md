# Run Trino MCP Queries in Cursor

The Trino MCP tool is **not available to the AI in this chat**. You need to run the queries yourself in Cursor.

## Step 1: Open Trino MCP in Cursor

1. Ensure Trino MCP server is configured in Cursor (Settings → MCP).
2. In a **new chat**, ask the AI to run a Trino query, or use the Trino MCP tool directly if you have a UI for it.

## Step 2: Run These 4 Queries

Copy each query below and run it via Trino MCP. Save each response.

### Query 1: User logs

```sql
SELECT u.app_id,
       COUNT(*) AS user_app_events_count,
       MIN(u.created_date) AS first_activity_at,
       MAX(u.created_date) AS last_activity_at
FROM prod.base44.base44_user_apps_logs_mongo u
WHERE u.app_id IN (
  SELECT ba.app_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
)
GROUP BY u.app_id
ORDER BY u.app_id
```

**Save response to:** `data/mcp_responses/user_logs.json`

---

### Query 2: Conversation snapshots

```sql
SELECT m.app_id, m.updated_date,
       COALESCE(SUBSTR(CAST(m.conversation_summary AS varchar), 1, 50000), '') AS conversation_summary
FROM prod.marketing.base44_app_context_snapshots_mongo m
WHERE m.app_id IN (
  SELECT ba.app_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
)
AND m.conversation_summary IS NOT NULL
ORDER BY m.app_id, m.updated_date ASC NULLS LAST
```

**Save response to:** `data/mcp_responses/conversations.json`

---

### Query 3: App metadata

```sql
SELECT ba.app_id, COALESCE(CAST(uga.user_description AS varchar), '') AS user_description,
       COALESCE(CAST(uga.public_settings AS varchar), '') AS public_settings,
       COALESCE(CAST(uga.categories AS varchar), '') AS categories
FROM (SELECT ba.app_id FROM prod_encrypted.payments.pp_base44_apps_replica ba
      INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid) ba
LEFT JOIN prod.marketing.base44_user_generated_apps_v2_mongo uga ON uga._id = ba.app_id
ORDER BY ba.app_id
```

**Save response to:** `data/mcp_responses/app_metadata.json`

---

### Query 4: Earliest conversation preview

See `docs/trino-query-earliest-conversation-preview.sql` or run:

```sql
WITH filter_app_ids AS (
  SELECT ba.app_id
  FROM prod_encrypted.payments.pp_base44_apps_replica ba
  INNER JOIN prod.payments.wp_accounts_replica ap ON ap.external_account_id = ba.msid
  WHERE ap.account_id IS NOT NULL
),
earliest_conversation_per_app AS (
  SELECT app_id, conversation_id AS earliest_conversation_id, first_ts AS earliest_conversation_first_at
  FROM (
    SELECT app_id, conversation_id, MIN(created_date) AS first_ts,
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
WHERE ecp.earliest_conversation_preview IS NOT NULL
ORDER BY f.app_id
```

**Save response to:** `data/mcp_responses/earliest_preview.json`

---

## Step 3: Save MCP responses

1. Create folder: `mkdir -p data/mcp_responses`
2. For each query, copy the **full MCP response** (the JSON with `rows` and `col_names`) into the corresponding file.
3. If the response is wrapped (e.g. `{"content":[{"type":"text","text":"{...}"}]}`), you can paste the whole thing — the script will extract the inner JSON.

## Step 4: Run the save script

```bash
python3 scripts/save_mcp_trino_results.py
```

This writes `data/trino_user_logs.json`, `data/trino_conversations.json`, `data/trino_app_metadata.json`, `data/trino_earliest_conversation_preview.json`.

## Step 5: Build full profiles

```bash
python3 scripts/build_full_profiles_from_trino.py
```

Then refresh the dashboard to see real data.
