"""Tests for the savings model and the savings-target salary solver."""
import pytest

from app.model import (
    W_RENT,
    W_NON_RENT,
    calculate_stats,
    required_net_new_for_savings_increase,
)

HIGHER = {"valuePct": 134.5, "direction": "higher"}
RENT_HIGHER = {"valuePct": 409.4, "direction": "higher"}
LOWER = {"valuePct": -12.0, "direction": "lower"}
RENT_LOWER = {"valuePct": -25.5, "direction": "lower"}


def test_home_buckets_use_fixed_weights():
    m = calculate_stats(10000, 12000, HIGHER, RENT_HIGHER)
    assert m["rent_home"] == pytest.approx(W_RENT * 10000)
    assert m["non_rent_home"] == pytest.approx(W_NON_RENT * 10000)
    # savings = remainder (20%)
    assert m["savings_home"] == pytest.approx(10000 * (1 - W_RENT - W_NON_RENT))


def test_same_currency_no_fx():
    m = calculate_stats(10000, 10000, HIGHER, RENT_HIGHER, fx_rate=1.0)
    # With identical salary and higher costs, dest savings must be lower.
    assert m["savings_new"] < m["savings_home"]
    assert m["fx_rate"] == 1.0
    # home-equiv equals raw when no FX
    assert m["savings_new_home_equiv"] == pytest.approx(m["savings_new"])


def test_fx_scaling_keeps_costs_sane():
    # Regression for the original currency-mismatch bug: destination rent must
    # be on the destination-currency scale, not the home-currency scale.
    fx = 0.30
    m = calculate_stats(16000, 24000, HIGHER, RENT_HIGHER, fx_rate=fx)
    net_home_dest = 16000 * fx
    assert m["rent_new"] == pytest.approx(W_RENT * net_home_dest * (1 + 4.094))
    # Savings should be a plausible positive number, not hugely negative.
    assert m["savings_new"] > 0


def test_savings_pct_delta_sign():
    # Cheaper destination + same salary => more savings => positive delta.
    m = calculate_stats(10000, 10000, LOWER, RENT_LOWER, fx_rate=1.0)
    assert m["savings_pct_delta"] > 0
    assert m["savings_home_diff"] > 0


def test_savings_home_diff_consistent_with_pct():
    m = calculate_stats(10000, 11000, HIGHER, RENT_HIGHER, fx_rate=1.0)
    expected_pct = m["savings_home_diff"] / abs(m["savings_home"]) * 100
    assert m["savings_pct_delta"] == pytest.approx(expected_pct)


@pytest.mark.parametrize("fx", [1.0, 0.30, 3.3])
@pytest.mark.parametrize("target_pct", [0, 10, 20, -15])
def test_savings_target_roundtrip(fx, target_pct):
    """required_net_new_for_savings_increase must produce a salary that,
    when fed back into calculate_stats, yields the requested savings delta."""
    net_home = 16000
    need = required_net_new_for_savings_increase(net_home, target_pct, HIGHER, RENT_HIGHER, fx_rate=fx)
    m = calculate_stats(net_home, need, HIGHER, RENT_HIGHER, fx_rate=fx)
    assert m["savings_pct_delta"] == pytest.approx(target_pct, abs=1e-6)


def test_custom_weights_change_buckets():
    # Savings 40%, of the remaining 60% rent takes half → w_rent=0.3, w_non=0.3.
    m = calculate_stats(10000, 10000, HIGHER, RENT_HIGHER, fx_rate=1.0, w_rent=0.3, w_non_rent=0.3)
    assert m["rent_home"] == pytest.approx(3000)
    assert m["non_rent_home"] == pytest.approx(3000)
    assert m["savings_home"] == pytest.approx(4000)  # 1 - 0.3 - 0.3


def test_savings_target_respects_custom_weights():
    # Round-trip must still hold with non-default weights.
    need = required_net_new_for_savings_increase(
        12000, 15, HIGHER, RENT_HIGHER, fx_rate=0.30, w_rent=0.25, w_non_rent=0.45
    )
    m = calculate_stats(12000, need, HIGHER, RENT_HIGHER, fx_rate=0.30, w_rent=0.25, w_non_rent=0.45)
    assert m["savings_pct_delta"] == pytest.approx(15, abs=1e-6)


def test_zero_target_matches_breakeven():
    # 0% savings increase should equal the model's break-even salary.
    fx = 0.30
    need = required_net_new_for_savings_increase(16000, 0, HIGHER, RENT_HIGHER, fx_rate=fx)
    m = calculate_stats(16000, need, HIGHER, RENT_HIGHER, fx_rate=fx)
    assert need == pytest.approx(m["equiv_net_new_for_same_savings"])
