# Parquet error: `base44_app_agents_conversations_mongo.messages`

## Error

When running the full app profile query (or any query that reads the `messages` column from `prod.base44.base44_app_agents_conversations_mongo`), Trino fails with:

```
io.trino.parquet.ParquetCorruptionException: Malformed Parquet file. Parquet page size 1401800711 bytes exceeds maximum allowed size 1073741824 bytes for column [messages] optional binary messages (STRING)
```

File: `s3a://wix-bi-iceberg-warehouse/base44.db/base44_app_agents_conversations_mongo_writetemp-.../data/00000-1-0b0b18f4-281a-4c32-a153-a8e0ab0d5b17.parquet`

## Cause

One or more Parquet **pages** in the `messages` column are larger than Trino’s allowed maximum (1 GiB). The column is crucial for the full profile (first agent conversation messages), but as long as that table has such a page, **any** read of `messages` triggers the error — SQL-side truncation (e.g. `SUBSTR(messages, 1, 100000)`) does not help because the column is still read fully before the function is applied.

## Workarounds in this repo

1. **Use the “no messages column” query**  
   `docs/trino-query-full-app-profile-no-messages-column.sql`  
   Same profile as the full query except it **does not read** `messages`. The four columns `first_agent_conversation_id`, `first_agent_conversation_created_date`, `first_agent_conversation_agent_name`, `first_agent_conversation_messages` are set to NULL. All other columns (including `earliest_conversation_preview` from `base44_conversation_messages_mongo`) are populated. Use this so the full profile export and Trino MCP flow can run until the table is fixed.

2. **Slim export**  
   If you only need app list fields (e.g. for the UW Lookup app), use a query that does not join to `base44_app_agents_conversations_mongo` at all (e.g. replica + context snapshots only).

## Fix (data / platform team)

The table must be changed so that no single Parquet page in `messages` exceeds the cluster limit (default 1073741824 bytes). Options:

1. **Compact / rewrite the Iceberg table**  
   Rewrite the table (e.g. Iceberg `rewrite_data_files` or equivalent) so that large `messages` values are split across pages or written with a smaller row-group/page size.

2. **Increase Trino’s Parquet max page size**  
   If the cluster allows, increase the limit (e.g. `parquet.max-read-block-size` or equivalent in the Trino/connector config) so that the existing 1.4 GB page is accepted. This is a cluster/config change, not a table change.

3. **Cap or split the column at write time**  
   If the pipeline that writes to this table can limit or split very large `messages` values (e.g. by length or by splitting into multiple rows), future data would not produce oversized pages. Existing data would still need compaction (1) or limit increase (2).

Once the table or cluster is fixed, you can switch back to the full profile query that includes `first_agent_conversation_messages` (`docs/trino-query-full-app-profile.sql` and chunked variant).
