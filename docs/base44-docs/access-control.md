# Choosing Who Can Access Your App
# Source: https://docs.base44.com/Setting-up-your-app/Managing-access.md

## App visibility levels
- **Private**: Only invited people (paid plans only, from Feb 6 2026)
- **Workspace**: Everyone in Base44 workspace
- **Public**: Anyone with link (optionally require login)

Smart app visibility auto-suggests based on app type (e.g. landing pages = Public).

## Roles
Default roles:
- **Admin**: Can manage admin-restricted areas in live app
- **User**: Can view and use app, no special permissions

Custom roles: Add fields to User entity (e.g. "Staff Manager", "Viewer")

## Key distinction: Collaborators vs Admins
- **Collaborators**: Can access app editor and dashboard. Invited from editor.
  - Automatically get Admin role in Users
  - Must have paid Base44 plan or workspace seat
- **Admins (Users)**: Sign in to live app only. Cannot access editor/dashboard.

## Inviting users
- From Dashboard: enter email, choose role, send invitation
- From within app: AI chat can set up in-app invites
- Private apps: Only admins can invite
- Public apps: Admins invite with role choice; Users can invite other Users

## Testing as user
- "Act as a user" feature in Preview mode
- Can impersonate any user to test permissions and flows

## App examples and recommended settings
- **Personal/Family app**: Private, require login, creator-only rules
- **Public website**: Public, no login, admin-only for form submissions
- **Business tool**: Workspace, require login, role-based rules
- **User dashboard**: Public/Workspace, require login, creator-only for user data
- **Admin panel**: Private/Workspace, require login, admin/manager role only
- **Content site**: Public for reading, admin/editor for CRUD
- **Multi-tenant**: Workspace/Private, require login, advanced RLS by tenant
