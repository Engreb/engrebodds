-- Market catalogue. Grading is driven by these rows: `rule` says how a selection settles,
-- `period` and `stat` say which numbers it settles on.
--
-- Selections by rule:
--   1x2          '1' | 'X' | '2'
--   dc           '1X' | 'X2' | '12'
--   ou           'O' | 'U'                with line (total of both teams)
--   team_ou      'HO' | 'HU' | 'AO' | 'AU' with line (one team's total; "home to score" = HO 0.5)
--   btts         'Y' | 'N'                (GG / NG)
--   clean_sheet  'H' | 'A'                (that team concedes nothing)
--   cs           'h-a', e.g. '2-1'        (correct score)
-- Lines are whole or half numbers only; a total landing exactly on a whole line is void.

create table public.markets (
  code        text primary key,
  name        text not null,
  rule        text not null check (rule in ('1x2', 'dc', 'ou', 'team_ou', 'btts', 'clean_sheet', 'cs')),
  period      text not null default 'FT' check (period in ('FT', '1H')),
  stat        text not null default 'goals'
              check (stat in ('goals', 'corners', 'cards', 'shots', 'shots_on_target', 'fouls')),
  needs_line  boolean not null default false,
  min_tier    text not null default 'goals' check (min_tier in ('full', 'goals')),
  check (period = 'FT' or stat = 'goals')   -- we only have full-match statistics
);

insert into public.markets (code, name, rule, period, stat, needs_line, min_tier) values
  ('1X2',         'Match result',              '1x2',         'FT', 'goals',           false, 'goals'),
  ('DC',          'Double chance',             'dc',          'FT', 'goals',           false, 'goals'),
  ('OU',          'Total goals',               'ou',          'FT', 'goals',           true,  'goals'),
  ('TEAM_OU',     'Team goals',                'team_ou',     'FT', 'goals',           true,  'goals'),
  ('BTTS',        'Both teams to score',       'btts',        'FT', 'goals',           false, 'goals'),
  ('CLEAN_SHEET', 'Clean sheet',               'clean_sheet', 'FT', 'goals',           false, 'goals'),
  ('CS',          'Correct score',             'cs',          'FT', 'goals',           false, 'goals'),
  ('HT_1X2',      'Half-time result',          '1x2',         '1H', 'goals',           false, 'goals'),
  ('HT_OU',       'First-half goals',          'ou',          '1H', 'goals',           true,  'goals'),
  ('HT_CS',       'Half-time correct score',   'cs',          '1H', 'goals',           false, 'goals'),
  ('CORNERS_OU',  'Total corners',             'ou',          'FT', 'corners',         true,  'full'),
  ('CARDS_OU',    'Total cards',               'ou',          'FT', 'cards',           true,  'full'),
  ('SHOTS_OU',    'Total shots',               'ou',          'FT', 'shots',           true,  'full'),
  ('SOT_OU',      'Total shots on target',     'ou',          'FT', 'shots_on_target', true,  'full'),
  ('FOULS_OU',    'Total fouls',               'ou',          'FT', 'fouls',           true,  'full');
