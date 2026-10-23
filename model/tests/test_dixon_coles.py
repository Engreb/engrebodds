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
