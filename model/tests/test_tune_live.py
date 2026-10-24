import pandas as pd

from engrebodds_model.tune_live import groups, tune


def api_like(seed=9):
    """API-Football-shaped results: integer season labels as text, numeric league ids, two leagues in one country."""
    import numpy as np
    rng = np.random.default_rng(seed)
    rows, day = [], pd.Timestamp("2021-08-07")
    teams = {"39": [f"E{i}" for i in range(8)], "40": [f"C{i}" for i in range(8)]}
    for w in range(4 * 40):
        date = day + pd.Timedelta(days=7 * w)
        season = str(date.year if date.month >= 7 else date.year - 1)
        for league, members in teams.items():
            order = rng.permutation(members)
            for k in range(0, 8, 2):
                rows.append({"league": league, "country": "England", "season": season, "date": date,
                             "home": order[k], "away": order[k + 1], "hg": rng.poisson(1.5), "ag": rng.poisson(1.1),
                             "hthg": 0, "htag": 0, "hst": None, "ast": None})
    return pd.DataFrame(rows)


def test_groups_link_by_country():
    df = api_like()
    assert len(groups(df, link=False)) == 2
    assert len(groups(df, link=True)) == 1


def test_tune_produces_settings_from_rolling_validation():
    df = api_like()
    settings = tune(df, grid=[(0.002, 2.0)], shots=True)
    assert settings["source"].startswith("API-Football")
    assert settings["validated_on"] == sorted(df.season.unique())[-2:]
    names = [step["experiment"] for step in settings["ladder"]]
    assert names[:2] == ["linked divisions + new-team prior", "season-boundary reset"]
    assert any("skipped" in step for step in settings["ladder"])        # no shots data yet
    assert settings["config"]["xi"] == 0.002
