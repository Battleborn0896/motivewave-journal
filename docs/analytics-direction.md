# Carver-inspired journal analytics

Robert Carver's [book resources](https://www.systematicmoney.org/systematic-trading-resources) link
his open-source framework. The maintained [pysystemtrade introduction](https://github.com/pst-group/pysystemtrade/blob/develop/docs/introduction.md)
demonstrates performance statistics, drawdowns, gross/cost/net accounting and volatility-based sizing.
Its [backtesting guide](https://github.com/pst-group/pysystemtrade/blob/develop/docs/backtesting.md)
provides the deeper accounting and portfolio context.

This is a design direction, not an installed integration or a claim that a metric proves an edge.
Keep capture independent of analytics and preserve source/precision labels.

| Journal capability | Input required | Delivery boundary |
|---|---|---|
| Gross vs net results and fee drag | Executions, commissions and multipliers | Recorded fees only; estimates/missing fees visible |
| Risk-normalized outcomes | Planned initial currency risk | Use R, never call trade R an account return |
| Drawdown and recovery | Time-ordered P&L | Closed-trade equity is not intraday marked account equity |
| Volatility-normalized risk | Pre-entry historical prices and contract value | No future data or automatic order sizing |
| Sharpe/Sortino and return drawdown | Regular account equity, cash flows, session calendar | Do not annualize irregular per-trade dollar P&L as returns |
| Setup uncertainty / robustness | Enough trades, ordered sessions, stable tagging | Block/session bootstrap; account for dependence and selection |
| Instrument/setup diversification | Aligned return history | Missing observations are not assumed zero returns |

Current journal statistics include expectancy, profit factor, closed-equity drawdown, R expectancy,
gross/net curves and recorded fee attribution. Near-term additions should be uncertainty, followed
by opt-in account-equity and pre-entry volatility imports. No broker credentials or execution
permissions are needed for those analyses.

`pysystemtrade` is [GPLv3](https://github.com/pst-group/pysystemtrade/blob/develop/LICENSE).
This journal is MIT. No Carver source has been copied or dependency installed here. Direct reuse
would need a deliberate license/distribution decision; for now implement ordinary statistical
definitions independently and credit research sources. The framework does not solve MotiveWave
fill access or supply this user's tick history.
