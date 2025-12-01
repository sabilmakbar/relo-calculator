def calculate_stats(net_home, net_new, col_excl_rent, rent):
    w_rent = 0.3
    w_non_rent = 0.5

    p_rent = rent["valuePct"] / 100
    p_col = col_excl_rent["valuePct"] / 100

    rent_home = w_rent * net_home
    non_rent_home = w_non_rent * net_home
    savings_home = net_home - rent_home - non_rent_home

    rent_new = rent_home * (1 + p_rent)
    non_rent_new = non_rent_home * (1 + p_col)
    savings_new = net_new - rent_new - non_rent_new

    denom = 1 - w_rent * (1 + p_rent) - w_non_rent * (1 + p_col)
    equiv = savings_home / denom if denom > 0 else None

    return {
        "net_home": net_home,
        "net_new": net_new,
        "rent_home": rent_home,
        "non_rent_home": non_rent_home,
        "savings_home": savings_home,
        "rent_new": rent_new,
        "non_rent_new": non_rent_new,
        "savings_new": savings_new,
        "equiv_net_new_for_same_savings": equiv,
    }