# Managing your app's security
# Source: https://docs.base44.com/Setting-up-your-app/Managing-security-settings.md

## App visibility levels
- **Private**: Only invited people can access (paid plans only)
- **Workspace**: Anyone in workspace can access
- **Public**: Anyone with the link can access (optionally require login)

## Data entity access
- **Public**: All users can access every record
- **Restricted**: Only users matching access rules can access

## Access rule types (Row-Level Security)
- **No restrictions**: Anyone can access
- **Creator only**: Only the user who created a record can access it
- **Entity-user field comparison**: Match record field with logged-in user property
- **User property check**: Allow access for users with specific property (e.g. role)

## CRUD rules
Separate rules for Create, Read, Update, Delete. Multiple rules per action (OR logic).

## Security check tool
Built-in scanner that checks for:
- Missing access rules on data entities
- Unsafe backend function exposure
- Secrets/API keys left in frontend code

## Key facts for UW
- Data tables and private apps are encrypted
- Data is NOT end-to-end encrypted (admins can access)
- Field-level security (FLS) not yet available
- Backend functions run server-side, never exposed to users
- API keys should be stored via secrets management
- All backend code is secure and inaccessible from outside the app

## Custom roles
- Default roles: Admin, User
- Custom roles via User entity fields (e.g. business-role with values like manager, staff, viewer)
- Security rules reference user properties for access control
