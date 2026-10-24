import numpy as np
import pytest
from scipy.optimize import check_grad

from engrebodds_model.dixon_coles import (
    _objective, decay_weights, fit, prepare, score_matrix,
)

N_TEAMS = 16
TRUE = {"base": np.log(1.35), "home": 0.25, "rho": -0.08}


def simulate(seed: int, seasons: int = 3):
    """Double round-robin seasons drawn from a known Dixon-Coles model."""
    rng = np.random.default_rng(seed)
    attack = rng.normal(0, 0.3, N_TEAMS)
    attack -= attack.mean()
    defence = rng.normal(0, 0.25, N_TEAMS)
    defence -= defence.mean()
    home_ids, away_ids, hg, ag = [], [], [], []
    for _ in range(seasons):
        for h in range(N_TEAMS):
            for a in range(N_TEAMS):
                if h == a:
                    continue
                lam = np.exp(TRUE["base"] + TRUE["home"] + attack[h] + defence[a])
                mu = np.exp(TRUE["base"] + attack[a] + defence[h])
                m = score_matrix(lam, mu, TRUE["rho"])
                cell = rng.choice(m.size, p=m.ravel())
                i, j = np.unravel_index(cell, m.shape)
                home_ids.append(h), away_ids.append(a), hg.append(i), ag.append(j)
    return attack, defence, home_ids, away_ids, hg, ag


@pytest.fixture(scope="module")
def season():
    return simulate(seed=7)


def test_gradient_is_exact(season):
    _, _, home_ids, away_ids, hg, ag = season
    data, _ = prepare(home_ids[:200], away_ids[:200], hg[:200], ag[:200],
                      decay_weights(np.linspace(0, 400, 200), 0.003))
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.2, 2 * data.n_teams + 1)
    x[-1] = -0.05
    err = check_grad(lambda v: _objective(v, data, 3.0)[0], lambda v: _objective(v, data, 3.0)[1], x)
    assert err < 1e-3 * np.linalg.norm(_objective(x, data, 3.0)[1])


def test_recovers_known_parameters(season):
    attack, defence, home_ids, away_ids, hg, ag = season
    data, teams = prepare(home_ids, away_ids, hg, ag)
    model = fit(data, teams)
    assert model.converged
    assert np.corrcoef(model.attack, attack)[0, 1] > 0.85
    assert np.corrcoef(model.defence, defence)[0, 1] > 0.8
    assert abs(model.home_adv - TRUE["home"]) < 0.08
    assert abs(model.base - TRUE["base"]) < 0.08
    assert abs(model.rho - TRUE["rho"]) < 0.1
    assert abs(model.attack.sum()) < 1e-9 and abs(model.defence.sum()) < 1e-9


def test_penalty_pulls_ratings_to_league_average(season):
    _, _, home_ids, away_ids, hg, ag = season
    data, teams = prepare(home_ids, away_ids, hg, ag)
    free, shrunk = fit(data, teams, penalty=0.0), fit(data, teams, penalty=50.0)
    assert np.linalg.norm(shrunk.attack) < np.linalg.norm(free.attack)
    assert np.linalg.norm(shrunk.defence) < np.linalg.norm(free.defence)
    assert abs(shrunk.base - free.base) < 0.05      # shrinking teams doesn't move the league's goal level


def test_rates_use_home_advantage(season):
    _, _, home_ids, away_ids, hg, ag = season
    data, teams = prepare(home_ids, away_ids, hg, ag)
    model = fit(data, teams)
    lam, mu = model.rates(0, 1)
    lam_rev, mu_rev = model.rates(1, 0)
    assert lam / mu_rev == pytest.approx(np.exp(model.home_adv))   # team 0 scores more at home than away
    assert model.score_matrix(0, 1).sum() == pytest.approx(1.0)


def test_decay_weights():
    w = decay_weights([0, 100, 365], 0.0065)
    assert w[0] == 1.0
    assert w[1] == pytest.approx(np.exp(-0.65))
    assert np.all(decay_weights([0, 500], 0.0) == 1.0)


def test_score_matrix_without_correction_is_independent_poisson():
    m = score_matrix(1.0, 1.0, rho=0.0)
    assert m[0, 0] == pytest.approx(np.exp(-2), rel=1e-6)
    assert m.sum() == pytest.approx(1.0)


def test_negative_rho_adds_low_score_draws():
    base, corrected = score_matrix(1.3, 1.1, 0.0), score_matrix(1.3, 1.1, -0.1)
    assert corrected[0, 0] > base[0, 0] and corrected[1, 1] > base[1, 1]
    assert corrected[1, 0] < base[1, 0] and corrected[0, 1] < base[0, 1]


# ---------- linked divisions, priors, shots variant ----------

def two_divisions(seed=11, seasons=3, movers=3):
    """Two divisions on one scale: division 1 teams are weaker and score less; some teams move each season."""
    rng = np.random.default_rng(seed)
    strength = {f"T{i}": rng.normal(0.25 if i < 10 else -0.25, 0.2) for i in range(20)}
    level = {0: np.log(1.35), 1: np.log(1.2)}
    div_of = {t: (0 if i < 10 else 1) for i, t in enumerate(strength)}
    rows = []
    for s in range(seasons):
        for d in (0, 1):
            members = [t for t in strength if div_of[t] == d]
            for h in members:
                for a in members:
                    if h != a:
                        lam = np.exp(level[d] + 0.25 + strength[h] - strength[a] / 2)
                        mu = np.exp(level[d] + strength[a] - strength[h] / 2)
                        rows.append((h, a, rng.poisson(lam), rng.poisson(mu), d))
        # promotion and relegation: weakest of division 0 swap with strongest of division 1
        d0 = sorted((t for t in strength if div_of[t] == 0), key=strength.get)[:movers]
        d1 = sorted((t for t in strength if div_of[t] == 1), key=strength.get)[-movers:]
        for t in d0:
            div_of[t] = 1
        for t in d1:
            div_of[t] = 0
    return strength, rows


def test_linked_divisions_gradient_and_scale():
    strength, rows = two_divisions()
    h, a, hg, ag, d = zip(*rows)
    data, teams = prepare(h, a, hg, ag, divisions=d)
    assert data.n_divisions == 2
    rng = np.random.default_rng(2)
    x = rng.normal(0, 0.2, 2 * data.n_teams + data.n_divisions)
    x[-1] = -0.05
    prior = rng.normal(0, 0.1, data.n_teams)
    f = lambda v: _objective(v, data, 2.0, prior, -prior)[0]
    g = lambda v: _objective(v, data, 2.0, prior, -prior)[1]
    assert check_grad(f, g, x) < 1e-3 * np.linalg.norm(g(x))

    model = fit(data, teams, penalty=1.0)
    assert model.converged and len(model.bases) == 2
    true = np.array([strength[t] for t in teams])
    fitted = model.attack - model.defence
    assert np.corrcoef(fitted, true)[0, 1] > 0.85      # one scale across both divisions
    assert model.bases[0] > model.bases[1]            # division 0 scores more


def test_penalty_pulls_towards_prior():
    _, _, home_ids, away_ids, hg, ag = simulate(seed=5, seasons=1)
    data, teams = prepare(home_ids[:60], away_ids[:60], hg[:60], ag[:60])
    prior = np.full(data.n_teams, -0.4)
    pulled = fit(data, teams, penalty=200.0, prior_attack=prior - prior.mean(), prior_defence=None)
    plain = fit(data, teams, penalty=200.0)
    assert np.allclose(pulled.attack, prior - prior.mean(), atol=0.1)   # sum-to-zero: a flat prior centres at 0
    custom = np.linspace(-0.3, 0.3, data.n_teams)
    pulled = fit(data, teams, penalty=500.0, prior_attack=custom)
    assert np.corrcoef(pulled.attack, custom)[0, 1] > 0.95
    assert np.linalg.norm(plain.attack) < np.linalg.norm(pulled.attack)


def test_fixed_rho_is_plain_poisson(season):
    _, _, home_ids, away_ids, hg, ag = season
    data, teams = prepare(home_ids, away_ids, hg, ag)
    assert fit(data, teams, fix_rho=True).rho == 0.0
