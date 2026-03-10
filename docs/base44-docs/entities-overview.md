# Entities Overview
# Source: https://docs.base44.com/developers/backend/resources/entities/overview.md

Entities are the data models for Base44 apps. Each entity defines the structure for documents in a collection, stored in Base44's NoSQL database (MongoDB-compatible).

## Features
- **Schema flexibility**: Update data model without migrations
- **Realtime updates**: Subscribe to changes (create, update, delete)
- **Fine-grained security**: Row-level and field-level security rules
- **Full CRUD operations**: Automatic via SDK entities module

## Definition
- JSON files in project's entities directory (default: base44/entities/)
- Deploy with `entities push` or `deploy` CLI commands
- Schemas use JSON Schema format (fields, types, validation rules)

## TypeScript types
- Generate from entity schemas for type safety and autocomplete

## Built-in User entity
- Every app includes a User entity
- Can be extended with custom fields (company, phone, preferences)
- User fields can be referenced in security rules

## Security
- Row-level security: Control who can CRUD records based on user attributes
- Field-level security: Not yet available (planned)
