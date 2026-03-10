# Backend Functions Overview
# Source: https://docs.base44.com/developers/backend/resources/backend-functions/overview.md

Backend functions run custom code in a secure, isolated environment (Deno runtime).

## Use cases
- Business logic that shouldn't run in browser
- Third-party API connections with protected credentials
- Webhook processing
- Custom endpoints
- Automations (scheduled or event-triggered)

## Limits
- Maximum 50 backend functions per project

## Calling functions
- **Via SDK**: `base44.functions.invoke("functionName", args)` — auto-handles auth
- **Via HTTP**: `https://<app-domain>/functions/<function-name>` — for webhooks, external integrations

## Auth context
- SDK calls: user auth passed through, can use `base44.auth.me()`
- HTTP calls: no auth context, use `asServiceRole` for admin operations

## Secrets
- API keys stored securely via `secrets set` CLI command
- Only accessible in backend functions (not frontend)

## Automations
Three types:
1. **Cron**: Standard 5-field cron expressions
2. **Simple schedule**: Interval-based (minutes, hours, days, weeks, months)
3. **Entity events**: Triggered on create/update/delete of database records

## Deployment
- TypeScript files in functions directory (default: base44/functions/)
- Deploy with `functions deploy` or `deploy`
- Atomic deployment: function + automations succeed or both roll back
