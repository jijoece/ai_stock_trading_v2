"""PR 12 scratch evaluation only — not merged into `src/`, not a project dependency.

Exercises Riskfolio-Lib's public `Portfolio` API against a synthetic returns
fixture to characterize its actual output shape (the thing an eventual
advisory-boundary adapter would have to wrap), independent of any dependency
being declared in `pyproject.toml`. Run inside a scratch virtualenv with
`riskfolio-lib==7.3.0` installed — see `EVALUATION.md` Section 2 for the
exact install command and results.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import riskfolio as rp


def build_synthetic_returns(seed: int = 42, n_days: int = 252) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        rng.normal(loc=0.0005, scale=0.01, size=(n_days, 4)),
        columns=["AAPL", "MSFT", "GOOG", "AMZN"],
    )


def main() -> None:
    returns = build_synthetic_returns()

    port = rp.Portfolio(returns=returns)
    port.assets_stats(method_mu="hist", method_cov="hist")
    weights = port.optimization(model="Classic", rm="MV", obj="Sharpe", rf=0.0, l=0)

    print("output type:", type(weights))
    print("output columns:", list(weights.columns))
    print("output index:", list(weights.index))
    print(weights)
    print()
    print("weights sum to 1.0:", np.isclose(weights["weights"].sum(), 1.0))
    print("no negative weights (long-only default):", (weights["weights"] >= -1e-9).all())
    print()
    print("has any order/share/authorize-shaped attribute:")
    for name in ("submit_order", "shares", "quantity", "order_type", "side"):
        print(f"  {name}: {hasattr(weights, name)}")


if __name__ == "__main__":
    main()
