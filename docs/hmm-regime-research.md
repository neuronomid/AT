# Hidden Markov Models For ETH/USD Regime Detection

Date: 2026-03-11

## Research Basis

This note is grounded in:

- [Rabiner, "A Tutorial on Hidden Markov Models and Selected Applications in Speech Recognition"](https://www.cs.ubc.ca/~murphyk/Bayes/rabiner.pdf)
- [Bilmes, "A Gentle Tutorial of the EM Algorithm and its Application to Parameter Estimation for Gaussian Mixture and Hidden Markov Models"](https://simbios.stanford.edu/svn/bhmm/references/em-gentle-tutorial.pdf)
- [hmmlearn tutorial](https://hmmlearn.readthedocs.io/en/0.3.3/tutorial.html)
- [hmmlearn API docs](https://hmmlearn.readthedocs.io/en/latest/api.html)
- [Alpaca Crypto Spot Trading docs](https://docs.alpaca.markets/docs/crypto-trading)

## Why HMM Fits This Problem

An HMM is useful when the process we care about is not directly observable but leaves statistical traces in observed data. In trading, the hidden variable is the market regime. The regime itself is not labeled by the exchange, but return behavior, realized volatility, range expansion, participation, and trend structure change when the market moves from trend to range or from calm to stress.

That fits ETH/USD well because:

- the market is continuous and regime changes are gradual rather than event-driven only
- returns are noisy at 1-minute granularity, but their distribution still changes across trend, range, and stress phases
- the strategy already relies on regime gating, so replacing hand-built scores with a probabilistic latent-state model is a direct upgrade rather than a new architecture

## Core HMM Principles

An HMM has three moving parts:

- hidden states
  Example for this project: `bull_trend`, `bear_trend`, `quiet_range`, `stress`
- transition matrix
  This captures regime persistence and the probability of moving from one state to another on the next step
- emission model
  This describes how observed features are distributed while the process is in a given hidden state

For V3, the hidden states are not supervised labels. They are inferred from the data. That makes the state-labeling step critical because raw state ids from the model are arbitrary.

## Forward-Backward vs. Viterbi

Rabiner separates two different inference questions:

- posterior state probabilities
  "What is the probability of each state at the current time?"
- single best path
  "What is the most likely sequence of hidden states over the whole sample?"

For trading, posterior probabilities are more useful than Viterbi path labels for live decision-making because the strategy needs uncertainty, not just a hard label. That is why V3 uses posterior regime probabilities for gating and sizing logic, then combines them with one-step continuation probabilities from the transition matrix.

The practical rule in V3 is:

- use posterior state probabilities for the current regime estimate
- use `posterior @ transition_matrix` for the next-step continuation estimate
- do not trade from raw state ids alone

## EM / Baum-Welch Training

Bilmes and Rabiner both emphasize that HMM parameters are typically estimated with EM, often called Baum-Welch in the HMM context. EM improves likelihood iteratively but is not guaranteed to find the global optimum. That matters in markets because:

- different seeds can converge to different local optima
- non-stationary data means yesterday's best fit can degrade quickly
- a model can overfit noisy high-frequency structure that is not tradeable after fees and slippage

That leads to the V3 design choices:

- fixed random seed for reproducibility
- rolling retraining instead of one global fit
- feature scaling on the training window only
- state mapping based on state statistics instead of trusting arbitrary state ids

## Feature Design For Financial Regime Models

Raw price level should not be fed directly into the HMM. Price is non-stationary, and the model will often waste state capacity on price location instead of market behavior.

The feature set should be regime-descriptive and as stationary as practical. V3 uses:

- short-horizon log return
- 3-bar log return
- realized volatility
- ATR in basis points
- ATR percentile over a rolling window
- bar range in basis points
- EMA gap and EMA slope
- volume z-score

This combination matters because the HMM sees the distributional shape of the market, while EMA, ATR, and volume make the latent states more interpretable:

- EMA gap and slope help distinguish directional regimes from non-directional ones
- ATR and realized volatility separate calm range from stress
- volume z-score helps distinguish quiet drift from expansion with participation

## Why 15-Minute Regime Bars And 1-Minute Execution Bars

Using the HMM directly on 1-minute bars is possible, but it is a poor first choice in this repo:

- 1-minute ETH/USD microstructure is noisy
- higher-frequency emissions are less Gaussian and more fragile
- retraining cost rises materially
- the model may overreact to short-lived order-flow noise

V3 therefore splits the problem:

- 15-minute bars for regime inference
- 1-minute bars for entry timing and execution confirmation

That separation keeps the regime model focused on structure while preserving tactical timing at the existing backtest resolution.

## State Labeling And The Label-Switching Problem

Hidden states from EM are not semantically ordered. State `0` in one fit might correspond to a trend regime, and state `0` in the next fit might correspond to a stress regime.

V3 solves this deterministically after every retrain:

- `stress` = state with the highest mean volatility / ATR percentile
- `quiet_range` = state with the lowest absolute return among the remaining states
- `bull_trend` = state with the highest mean return among the remaining states
- `bear_trend` = final remaining state

This is not a theoretical guarantee of "true" market labels. It is an engineering rule that makes the model stable enough to use in a rolling backtest.

## How EMA, ATR, And Volume Help Rather Than Replace The HMM

The HMM is the top-level regime model, not the only decision tool. The repo's original principle still applies: the probabilistic layer is advisory, and deterministic checks remain responsible for execution quality.

V3 uses the HMM to answer:

- is the market currently in a bull, bear, quiet, or stress regime?
- how confident is that estimate?
- how likely is the current regime to persist one step ahead?

It uses deterministic signals to answer:

- is the current minute a good entry moment inside the approved bull regime?
- is volatility tradeable rather than dead or chaotic?
- is there participation and breakout confirmation?
- is the expected edge still positive after estimated costs?

That division is important because HMMs are good at market context, not at precise execution timing.

## Failure Modes And Limits

HMMs are useful but fragile if treated like magic. The main risks are:

- non-stationarity
  Crypto distributions shift. A model trained too far in the past becomes stale.
- local optima
  EM can fit a plausible but weak explanation of the same window.
- emission mismatch
  Gaussian emissions are an approximation, not a literal truth for crypto returns.
- label instability
  Without deterministic mapping, regime ids drift across retrains.
- lookahead leakage
  Using partially formed higher-timeframe bars or future normalization statistics invalidates the backtest.
- overfitting
  Too many states or too many handcrafted filters can make the system "smart" on paper and useless after costs.

The implementation response is:

- fixed 4-state scope
- rolling 20-day retraining window
- daily retrain cadence
- closed 15-minute bars only
- training-window-only normalization
- long-only execution because Alpaca crypto spot cannot short

## Why V3 Is Long-Only

The strategy still models bearish and stress regimes, but Alpaca's crypto spot docs explicitly state that crypto can not be sold short. That means bearish regimes are still valuable, but only for:

- rejecting new long entries
- forcing exits
- reducing time spent in adverse conditions

They are not used for short execution in this repo's paper-trading path.

## V3 Implementation Translation

The research above becomes the following concrete strategy shape:

- fit a 4-state Gaussian HMM on rolling 15-minute ETH/USD features
- convert raw states into semantic regimes after every retrain
- enter long only when:
  - posterior `bull_trend` probability is high enough
  - one-step bull continuation probability is high enough
  - higher-timeframe EMA alignment is bullish
  - 1-minute breakout and participation confirm the move
  - ATR percentile is tradeable, not dead and not stress-like
  - cost-adjusted edge is still positive
- exit when:
  - stress or bear probability takes over
  - bull continuation decays sharply
  - short-term momentum breaks down
  - stop, trailing stop, or time stop fires

## Practical Conclusion

HMMs are appropriate here if they are treated as a regime-context layer rather than a sovereign trading brain. The model adds value by estimating hidden market structure and uncertainty. EMA, ATR, breakout, volume, and risk rules remain essential because they translate regime probability into auditable execution behavior.

That combination is the right direction for V3: probabilistic regime inference on the higher timeframe, deterministic execution discipline on the lower timeframe, and offline review before any refinement is accepted.
