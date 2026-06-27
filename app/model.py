W_RENT = 0.3
W_NON_RENT = 0.5


def required_net_new_for_savings_increase(net_home, savings_pct, col_excl_rent, rent,
                                          fx_rate=1.0, w_rent=W_RENT, w_non_rent=W_NON_RENT):
    """Destination-currency salary needed to grow savings by `savings_pct`
    relative to current (home) savings.

    Living costs scale only with the home spending baseline (converted to
    destination currency), so the required salary is:
        net_new = fx_rate * (target_savings_home + net_home * cost_factor)
    where target_savings_home = current_savings * (1 + savings_pct/100).
    """
    p_rent = rent["valuePct"] / 100
    p_col = col_excl_rent["valuePct"] / 100

    savings_home = net_home * (1 - w_rent - w_non_rent)
    target_savings_home = savings_home * (1 + savings_pct / 100)
    cost_factor = w_rent * (1 + p_rent) + w_non_rent * (1 + p_col)

    return fx_rate * (target_savings_home + net_home * cost_factor)


def calculate_stats(net_home, net_new, col_excl_rent, rent,
                    fx_rate=1.0, w_rent=W_RENT, w_non_rent=W_NON_RENT):
    p_rent = rent["valuePct"] / 100
    p_col = col_excl_rent["valuePct"] / 100

    # Home figures — always in home currency.
    rent_home = w_rent * net_home
    non_rent_home = w_non_rent * net_home
    savings_home = net_home - rent_home - non_rent_home

    # Scale home spending baseline to destination currency before applying
    # Numbeo percentages (which are already FX-adjusted / USD-normalised).
    net_home_dest = net_home * fx_rate

    rent_new = w_rent * net_home_dest * (1 + p_rent)
    non_rent_new = w_non_rent * net_home_dest * (1 + p_col)
    savings_new = net_new - rent_new - non_rent_new

    # Convert destination savings back to home currency for apples-to-apples comparison.
    savings_new_home_equiv = savings_new / fx_rate if fx_rate != 1.0 else savings_new

    # Absolute difference and % change (both in home currency).
    savings_home_diff = savings_new_home_equiv - savings_home
    if savings_home and savings_home != 0:
        savings_pct_delta = savings_home_diff / abs(savings_home) * 100
    else:
        savings_pct_delta = None

    # Minimum destination salary to maintain home savings rate (destination currency).
    equiv = net_home_dest * (1 + w_rent * p_rent + w_non_rent * p_col)

    return {
        "net_home": net_home,
        "net_new": net_new,
        "rent_home": rent_home,
        "non_rent_home": non_rent_home,
        "savings_home": savings_home,
        "rent_new": rent_new,
        "non_rent_new": non_rent_new,
        "savings_new": savings_new,
        "savings_new_home_equiv": savings_new_home_equiv,
        "savings_home_diff": savings_home_diff,
        "savings_pct_delta": savings_pct_delta,
        "equiv_net_new_for_same_savings": equiv,
        "fx_rate": fx_rate,
    }
