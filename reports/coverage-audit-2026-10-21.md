# API-Football coverage audit, 2026-10-21

Plan: **Free** · daily limit 100 · requests used by this audit: 14

## Leagues

| | Count |
|---|---|
| Leagues listed | 1249 |
| With a current season | 1242 |
| Current season has odds | 415 |
| Current season has odds **and** match statistics | 98 |
| **Proposed allowlist** (odds + statistics, plus pinned) | **108** |

By type: League 82, Cup 26

## Pinned leagues (Nigeria and CAF)

| League | Country | Current season | Odds | Match stats | Standings | Events |
|---|---|---|---|---|---|---|
| Federation Cup (1231) | Nigeria | 2026 | no | no | no | yes |
| NPFL (399) | Nigeria | 2027 | yes | no | yes | yes |
| Africa Cup of Nations (6) | World | 2025 | no | yes | yes | yes |
| Africa Cup of Nations - Qualification (36) | World | 2027 | yes | yes | yes | yes |
| Africa Cup of Nations - Women (922) | World | 2025 | no | no | yes | yes |
| Africa Cup of Nations U20 (538) | World | 2026 | yes | no | no | yes |
| CAF Champions League (12) | World | 2026 | yes | yes | no | yes |
| CAF Confederation Cup (20) | World | 2026 | yes | yes | no | yes |
| CAF Cup of Nations - U17 (973) | World | 2026 | no | no | yes | yes |
| CAF Super Cup (533) | World | 2026 | no | no | no | no |
| CAF U23 Cup of Nations (1015) | World | 2027 | no | no | no | no |
| CAF Women's Champions League (1164) | World | 2025 | no | no | no | yes |
| World Cup - Qualification Africa (29) | World | 2023 | no | yes | yes | yes |

## Fixtures per day

| Date | All fixtures | On proposed allowlist |
|---|---|---|
| 2026-10-20 | 233 | 60 |
| 2026-10-21 | 271 | 40 |
| 2026-10-22 | 160 | 52 |

## Past-season completeness

| Season | Fixtures | Finished | Postponed | Void | Finished, no FT score | Finished, no HT score |
|---|---|---|---|---|---|---|
| NPFL 2024 | 380 | 380 | 0 | 0 | 0 | 1 |
| Premier League 2024 | 380 | 380 | 0 | 0 | 0 | 0 |

## Match statistics sample

- NPFL: Doma United v Abia Warriors: **no statistics returned**
- Premier League: Nottingham Forest v Chelsea: Ball Possession, Blocked Shots, Corner Kicks, Fouls, Goalkeeper Saves, Offsides, Passes %, Passes accurate, Shots insidebox, Shots off Goal, Shots on Goal, Shots outsidebox, Total Shots, Total passes, Yellow Cards, expected_goals, goals_prevented

## Odds samples (upcoming fixtures)

- Benchmark league: no upcoming fixture found in the date window
- NPFL: no upcoming fixture found in the date window
- CAF: no upcoming fixture found in the date window

## Bookmakers API-Football carries

32 in total: 10Bet, 188Bet, 1xBet, 5Dimes, 888Sport, Bet365, BetFred, BetVictor, Betano, Betcris, Betfair, Betsson, Betway, Bovada, Bwin, ComeOn, Dafabet, Expekt, Fonbet, Intertops, Interwetten, Ladbrokes, Marathonbet, Netbet, NordicBet, Pinnacle, SBO, Sportingbet, Superbet, Tipico, Unibet, William Hill

Nigerian-market bookmakers present: SportyBet no, Bet9ja no, BetKing no, 1xBet yes, Betway yes, MSport no, NairaBet no

## Errors returned

- `/fixtures` {'date': '2026-10-23'}: {'plan': 'Free plans do not have access to this date, try from 2026-10-20 to 2026-10-22.'}
- `/fixtures` {'league': 399, 'season': 2026}: {'plan': 'Free plans do not have access to this season, try from 2022 to 2024.'}
- `/fixtures` {'league': 399, 'season': 2025}: {'plan': 'Free plans do not have access to this season, try from 2022 to 2024.'}
- `/fixtures` {'league': 39, 'season': 2025}: {'plan': 'Free plans do not have access to this season, try from 2022 to 2024.'}

## Addendum (follow-up samples, same day)

The free plan also caps requests at **10 per minute**, so the client now waits and retries. These samples were fetched afterwards (3 requests):

- **Europa League: Ararat-Armenia v AZ Alkmaar** (upcoming): 10 bookmakers, 178 markets: 1xBet, Bet365, BetVictor, Betano, Betfair, Dafabet, Marathonbet, Pinnacle, SBO, William Hill. All key markets present, including corners and cards.
- **CAF Champions League: 15 de Agosto v Simba** (finished today): 7 bookmakers, 52 markets: 1xBet, Bet365, Betano, Betfair, Dafabet, Marathonbet, William Hill. Result, double chance, goals, GG/NG and correct score are present; no corners, cards or clean-sheet markets.
- **CAF match statistics** for the same match: possession, shots, corners, yellow/red cards, free kicks. No fouls figure.
- **NPFL odds: not verified.** No NPFL fixture fell inside the free plan's date window (yesterday to tomorrow).

### Fixtures per day by coverage tier

| Date | Odds + match stats | Odds (any) | All |
|---|---|---|---|
| 2026-10-20 | 60 | 210 | 233 |
| 2026-10-21 | 36 | 215 | 271 |
| 2026-10-22 | 50 | 143 | 160 |

### Season labels
API-Football labels NPFL seasons by their **end** year: "2027" is 2026/27 (28 Aug 2026 to 30 May 2027). So "NPFL 2024" above is the 2023/24 season. Ingestion must take the season label from the API, not work it out from dates.
