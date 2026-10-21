# Engrebodds

Football predictions and tips site for a Nigerian/African audience, modelled on afriodds.com. Owner: Emmanuel (UK-based; MSc Data Science). Claude builds; Emmanuel guides and reviews.

## Status
- A clickable prototype exists: `prototype/engrebodds.html`. It's a single file that uses generated sample data. Use it as the reference for pages, layout and copy.
- No real data, backend or payments yet.

## Product decisions (agreed)
- Brand: Engrebodds. This is the existing tips business, renamed. A free proofed-record period comes first; the paid launch is planned for **Aug 2027**.
- Pages: predictions by day/league, banker of the day, 2/5/10 odds slips, low-risk picks (never use the claim "sure odds"), GG/NG, over 1.5, over/under 2.5, double chance, correct score, bet builder, leagues, tips by bookmaker, track record, pricing, sign-up, responsible gambling.
- Track record shows hit rate **with** average odds, profit in units and ROI. Nothing is ever deleted. Offer a CSV export.
- Every accumulator shows the model's chance that all legs land.
- Free tips: a few fixtures per day. Everything else is for Premium.
- Pricing (placeholders): ₦2,500 weekly, ₦7,500 monthly, ₦18,000 for 3 months. No auto-renewal.
- Payments: Paystack or Flutterwave (naira: card, transfer, USSD).
- 18+ notice and responsible-gambling messaging on every page.

## Bookmakers
- Never place bets on users' behalf and never hold their bookmaker logins.
- No automated booking codes for now: Nigerian bookmakers have no open API, and unofficial endpoints break their terms. At launch, show each slip as a selections list with a copy button.
- Later: apply to bookmaker affiliate programmes (SportyBet, Bet9ja, 1xBet, BetKing, Betway, MSport, NairaBet). Automate booking codes only where a bookmaker permits it.
- Get legal advice before taking affiliate money (Nigerian regulation is now state by state; owner is UK-based).

## Build plan
1. **Data layer:** API-Football for fixtures, results and odds (it covers the NPFL and CAF). Daily ingestion job.
2. **Model:** Poisson / Dixon-Coles goals model producing 1X2, double chance, over/under, GG/NG and correct-score probabilities. Fair odds = probability with a typical bookmaker margin. Backtest before publishing.
3. **Database:** Supabase (Postgres) holding leagues, teams, fixtures, predictions, tips, results and automatic grading after full time. Use row-level security for Premium content.
4. **Site:** web front end matching the prototype; tips are locked server-side for non-members.
5. **Accounts and payments:** Supabase Auth plus Paystack.
6. **Later:** WhatsApp alerts, bookmaker affiliate links, permitted booking codes.

## Working style
- Plan fully before coding; confirm each phase with Emmanuel before moving on.
- Give honest, pressure-tested advice, not encouragement.
