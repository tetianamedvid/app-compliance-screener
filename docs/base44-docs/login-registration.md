# Managing Login and Registration
# Source: https://docs.base44.com/Setting-up-your-app/Managing-login-and-registration.md

## Authentication options
- Email and password
- Google (default Base44 OAuth or custom Google OAuth)
- Microsoft
- Facebook (requires verified Facebook account)
- Apple
- Single sign-on (SSO) via OIDC (Elite plan only)

Multiple login options can be enabled simultaneously.

## Custom Google OAuth
- Requires Builder plan or higher
- Requires custom domain connected to app
- Shows app's domain instead of "base44.com" in Google login
- Requires Google Cloud Console setup and verification (up to 5 days)

## Data collection at sign-up
- Can prompt AI chat to generate custom sign-up forms
- Collect fields beyond email/password (name, company, role, etc.)
- Storage options:
  - Users dataset (admin-only, secure)
  - Connected dataset (public-facing, for in-app display)

## Key facts for UW
- Authentication is platform-managed (not custom-built)
- Custom auth pages/flows NOT currently supported
- Password reset built-in via login screen
- Auth only works on published app (not in preview)
- Cannot make some pages public and others private (planned feature)
- Workaround: public landing page + login-required for other pages
