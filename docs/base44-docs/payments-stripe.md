# Setting up Stripe on Base44
# Source: https://docs.base44.com/documentation/setting-up-your-app/setting-up-payments.md

Base44 lets you set up Stripe payments directly from the AI chat. Everything starts in Stripe test mode.

## Requirements
- Stripe is available on the **Builder** plan and above.

## Setup flow
1. Install Stripe via AI chat (test mode)
2. Create products via AI chat (products created in Stripe)
3. Test checkout flow (published app only, not preview)
4. Claim Stripe sandbox (connect your Stripe account, 60-day window)
5. Add live Stripe API keys

## Payment flows supported
- One-time purchases (products, digital goods)
- Credit/token systems
- Subscriptions/recurring billing
- Bookings/appointments
- Event tickets
- Donations/tips
- Invoicing
- Marketplace (Stripe Connect)
- International payments (multiple currencies, local methods)

## Accepted methods (via Stripe)
- Credit and debit cards
- Apple Pay, Google Pay
- Local payment methods (iDEAL, Bancontact, etc.)
- Multiple currencies with automatic conversion

## Key notes
- Products/prices are managed in Stripe Dashboard, not Base44
- Checkout only works on published app (not editor preview)
- Stripe Connect for marketplace requires additional Stripe approval
- Backend functions handle payment logic server-side
