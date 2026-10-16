"""Decision engine: ratings, reasons, and train/live input consistency (no network)."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from checker.decision import decide, reasons, z_distance
from research import trade_model as tm


def trade(**kw):
    base = dict(option_type="call", strike=772.0, spot=770.0, minutes_left=200.0, iv=0.18, prob_itm=0.25,
                bid=0.40, ask=0.42, mid=0.41, premium=0.42, move_vs_expected=0.8, expected={"atm_iv": 0.18})
    base.update(kw)
    return SimpleNamespace(**base)


class RecordingModel:
    """Fake model: remembers the feature row and returns a fixed score."""
    def __init__(self, score):
        self.score, self.rows = score, []

    def predict(self, X):
        self.rows.append(X)
        return np.array([self.score])


def bundle(score):
    return {"model": RecordingModel(score), "sorted_preds": np.linspace(-1, 0.2, 1001),
            "ratings": [{"rating": "avoid", "return_per_dollar": -0.48}, {"rating": "poor", "return_per_dollar": -0.15},
                        {"rating": "typical", "return_per_dollar": -0.05}], "trained_through": "2026-10-14"}


def test_z_distance_sign():
    assert z_distance(trade(strike=772)) > 0                       # OTM call
    assert z_distance(trade(strike=768)) < 0                       # ITM call
    assert z_distance(trade(option_type="put", strike=772)) < 0    # ITM put


@pytest.mark.parametrize("score,rating", [(-0.95, "avoid"), (-0.6, "poor"), (0.0, "typical")])
def test_rating_from_percentile(score, rating):
    d = decide(bundle(score), trade(), 0.2, 0.95, 0.17, 0.003, None)
    assert d["available"] and d["rating"] == rating
    assert d["group"]["rating"] == rating


def test_live_inputs_use_training_units():
    b = bundle(-0.3)
    decide(b, trade(), hist_intraday=0.22, ml_ratio=0.9, vix_prev=0.169, move_so_far=0.004, similar=None)
    row = b["model"].rows[0].iloc[0]
    assert list(b["model"].rows[0].columns) == tm.FEATURES
    assert 0 < row["vix_prev"] < 1.5                 # a fraction like training (0.17), never 16.9
    assert 0 <= row["move_so_far"] < 0.2
    assert row["hist_edge"] == pytest.approx(0.22 - 0.25)
    assert row["log_price"] == pytest.approx(np.log(0.41))


def test_missing_inputs_are_reported_not_fatal():
    d = decide(bundle(-0.3), trade(), None, None, None, None, None)
    assert d["available"] and set(d["missing"]) == {"history", "ml_forecast"}


def test_not_available_for_next_day_or_without_model():
    assert decide(bundle(0), trade(minutes_left=1500), 0.2, 1, 0.17, 0, None)["available"] is False
    assert decide(None, trade(), 0.2, 1, 0.17, 0, None)["available"] is False


def test_reasons_far_otm_and_big_move():
    r = reasons(trade(strike=775, move_vs_expected=1.8), z=1.4, ml_ratio=None, similar=None)
    assert len(r) <= 3 and all(x["tone"] == "bad" for x in r[:2])
    assert "Far out of the money" in r[0]["text"] or "1.8×" in r[0]["text"]


def test_reasons_ml_and_spread():
    r = reasons(trade(bid=0.30, ask=0.40, mid=0.35), z=0.1, ml_ratio=0.8, similar=None)
    texts = " ".join(x["text"] for x in r)
    assert "20% smaller move" in texts and "spread" in texts


def test_reasons_last_hour():
    assert any("Under an hour" in x["text"] for x in reasons(trade(minutes_left=40), 0.1, None, None))


def test_rating_bands():
    assert tm.rating_for(0.05)[0] == "avoid" and tm.rating_for(0.3)[0] == "poor" and tm.rating_for(0.99)[0] == "typical"


def test_near_money_reason_states_the_actual_odds():
    r = reasons(trade(prob_itm=0.41), z=0.1, ml_ratio=None, similar=None)
    assert any("41% chance" in x["text"] for x in r)


def test_overpaying_is_flagged_and_forces_avoid():
    mild = decide(bundle(0.0), trade(premium=0.47), 0.2, 1.0, 0.17, 0.003, None)     # +12% over the ask
    assert mild["rating"] == "typical" and "above the ask" in mild["reasons"][0]["text"] and mild["override"] is None
    big = decide(bundle(0.0), trade(premium=0.60), 0.2, 1.0, 0.17, 0.003, None)      # +43% over the ask
    assert big["rating"] == "avoid" and big["override"] and "above the ask" in big["reasons"][0]["text"]
    fair = decide(bundle(0.0), trade(premium=0.42), 0.2, 1.0, 0.17, 0.003, None)
    assert not any("above the ask" in r["text"] for r in fair["reasons"])
