"""
train_model.py — One-time XGBoost training script.

SECURITY NOTES:
  - Uses TIME-BASED train/test split (NEVER random) to prevent lookahead bias.
    Random splitting on time-series data leaks future information and produces
    falsely high accuracy. This is a critical correctness requirement.
  - Output models/classifier.pkl is a pickle file. Never accept this file from
    an external source — see BRAIN.md "Pickle Security Note".
  - Generates models/classifier.sha256 alongside the pkl as an integrity anchor.
  - Both files written atomically (write to .tmp, then os.replace) to prevent
    a partial write from being loaded by model.py.
  - AUC-ROC >= 0.55 gate: model is rejected and not saved if below this bar.

Usage:
  python -m backend.scripts.train_model --tickers SPY,QQQ,IWM --days 756

  756 calendar days ≈ 3 years of trading data (252 trading days/year).
  Minimum recommended: 504 days (2 years).

  Multi-ticker training concatenates per-ticker rows after a chronological
  train/test split PER TICKER — never across tickers — to preserve the
  no-lookahead invariant within each symbol.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import pickle  # noqa: S403  # security: write only — used to serialize trained model
import sys

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("train_model")


# ── Data loading ─────────────────────────────────────────────────────────────

def _load_single_ticker(
    ticker: str, days: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch OHLCV + rolling features for a single ticker."""
    from backend.core.data_fetcher import fetch_ohlcv
    from backend.core.feature_engineering import (
        _MIN_ROWS,
        build_features,
        feature_row_to_dataframe,
    )

    logger.info("Fetching %d days of OHLCV data for %s...", days, ticker)
    ohlcv = fetch_ohlcv(ticker=ticker, days=days)
    logger.info("[%s] Got %d bars.", ticker, len(ohlcv))

    feature_rows = []
    for i in range(_MIN_ROWS, len(ohlcv)):
        window = ohlcv.iloc[:i + 1]
        try:
            row = build_features(window)
            df = feature_row_to_dataframe(row)
            df.index = [ohlcv.index[i]]
            feature_rows.append(df)
        except Exception as exc:
            logger.warning("[%s] Skipping bar %s: %s", ticker, ohlcv.index[i], exc)

    if not feature_rows:
        logger.error("[%s] No valid feature rows — insufficient data.", ticker)
        sys.exit(1)

    features_df = pd.concat(feature_rows)
    logger.info("[%s] Computed features for %d bars.", ticker, len(features_df))
    return features_df, ohlcv


def load_training_data(
    tickers: list[str], days: int
) -> list[tuple[str, pd.DataFrame, pd.DataFrame]]:
    """
    Fetch OHLCV + compute features for every ticker in `tickers`.

    Returns:
        List of (ticker, features_df, ohlcv_df) tuples — one per ticker,
        each chronologically contiguous on its own index. The caller MUST
        split each tuple chronologically before concatenating across tickers;
        a global concat-then-split would reintroduce lookahead bias at ticker
        boundaries.
    """
    return [(t, *_load_single_ticker(t, days)) for t in tickers]


# ── Label creation ────────────────────────────────────────────────────────────

def create_labels(ohlcv: pd.DataFrame, features_df: pd.DataFrame,
                  forward_days: int = 5) -> pd.Series:
    """
    Create binary labels: 1 if close[t+forward_days] > close[t], else 0.

    CRITICAL: The label uses forward data (close[t+forward_days]). The
    time-based split must cut BEFORE the last forward_days bars to prevent
    future data leaking into the training set.

    Args:
        ohlcv:        Full OHLCV DataFrame.
        features_df:  Features DataFrame (aligned index subset of ohlcv).
        forward_days: Number of days ahead to define a successful trade.

    Returns:
        pd.Series of binary labels (0 or 1) aligned to features_df.index.
    """
    # Future return: positive = 1 (bullish success), negative/zero = 0
    future_close = ohlcv["close"].shift(-forward_days)
    returns = (future_close - ohlcv["close"]) / ohlcv["close"]
    labels = (returns > 0).astype(int)

    # Align labels to features_df index
    aligned = labels.reindex(features_df.index)

    # Drop rows where the label is NaN (i.e., the last forward_days bars
    # don't have a valid future close)
    valid_mask = aligned.notna()
    n_dropped = (~valid_mask).sum()
    if n_dropped > 0:
        logger.info(
            "Dropping %d rows at end of series (no future close for labeling).", n_dropped
        )

    return aligned[valid_mask].astype(int)


# ── Time-based split ──────────────────────────────────────────────────────────

def time_based_split(
    features: pd.DataFrame,
    labels: pd.Series,
    test_fraction: float = 0.20,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """
    Chronological train/test split: first (1-test_fraction) = train,
    last test_fraction = test.

    CRITICAL: NEVER use random shuffling on time-series data.
    Random splitting introduces lookahead bias and produces falsely high accuracy.

    Logs the split boundary date for audit purposes.
    """
    if len(features) != len(labels):
        raise ValueError("features and labels must have same length")

    split_idx = int(len(features) * (1 - test_fraction))
    if split_idx < 10:
        logger.error("Too few training samples (%d). Need more historical data.", split_idx)
        sys.exit(1)

    split_date = features.index[split_idx]
    logger.info(
        "TIME-BASED SPLIT: train [%s → %s] (%d bars) | test [%s → %s] (%d bars)",
        features.index[0].date(), features.index[split_idx - 1].date(), split_idx,
        split_date.date(), features.index[-1].date(), len(features) - split_idx,
    )

    X_train = features.iloc[:split_idx]
    X_test = features.iloc[split_idx:]
    y_train = labels.iloc[:split_idx]
    y_test = labels.iloc[split_idx:]

    return X_train, X_test, y_train, y_test


# ── Model training ────────────────────────────────────────────────────────────

def train_and_evaluate(
    X_train: pd.DataFrame,
    X_test: pd.DataFrame,
    y_train: pd.Series,
    y_test: pd.Series,
    min_auc: float = 0.55,
):
    """
    Train an XGBoost classifier and evaluate on the test set.

    Rejects (exits with code 1) if AUC-ROC < min_auc.

    Returns:
        Trained XGBClassifier.
    """
    try:
        import xgboost as xgb
    except ImportError:
        logger.error("xgboost is not installed. Run: pip install xgboost==2.0.3")
        sys.exit(1)

    from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

    logger.info(
        "Training XGBoost on %d samples, %d features...",
        len(X_train), X_train.shape[1],
    )

    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        random_state=42,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    auc = roc_auc_score(y_test, y_proba)
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)

    logger.info(
        "Evaluation — AUC-ROC: %.4f | Accuracy: %.4f | Precision: %.4f | Recall: %.4f",
        auc, acc, prec, rec,
    )

    if auc < min_auc:
        logger.error(
            "Model AUC-ROC %.4f is below minimum threshold %.4f. "
            "Model NOT saved. Gather more data or adjust features.",
            auc, min_auc,
        )
        sys.exit(1)

    logger.info("AUC-ROC %.4f passes minimum bar of %.4f — model accepted.", auc, min_auc)
    return model


# ── Serialization ─────────────────────────────────────────────────────────────

def serialize_model(model, output_dir: str = "models") -> None:
    """
    Serialize the trained model to models/classifier.pkl and write
    its SHA-256 hash to models/classifier.sha256.

    Both files are written atomically (write to .tmp, then os.replace)
    to prevent a partial write from being loaded by model.py.
    """
    os.makedirs(output_dir, exist_ok=True)

    pkl_path = os.path.join(output_dir, "classifier.pkl")
    hash_path = os.path.join(output_dir, "classifier.sha256")

    # Serialize model to bytes and compute hash
    model_bytes = pickle.dumps(model)  # noqa: S301  # serialize trained model for storage
    sha256_hash = hashlib.sha256(model_bytes).hexdigest()

    # Atomic write: pkl
    pkl_tmp = pkl_path + ".tmp"
    with open(pkl_tmp, "wb") as f:
        f.write(model_bytes)
    os.replace(pkl_tmp, pkl_path)

    # Atomic write: hash
    hash_tmp = hash_path + ".tmp"
    with open(hash_tmp, "w") as f:
        f.write(sha256_hash)
    os.replace(hash_tmp, hash_path)

    logger.info("Model saved to %s", pkl_path)
    logger.info("SHA-256 hash saved to %s", hash_path)
    logger.info("Hash: %s", sha256_hash)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Train and serialize the XGBoost trading classifier.\n\n"
            "IMPORTANT: Uses time-based train/test split to prevent lookahead bias.\n"
            "Minimum recommended days: 504 (2 years). Ideal: 756 (3 years)."
        )
    )
    parser.add_argument(
        "--tickers", default=None,
        help="Comma-separated tickers to train on, e.g. SPY,QQQ,IWM",
    )
    parser.add_argument(
        "--ticker", default=None,
        help="(Deprecated) Single ticker. Prefer --tickers.",
    )
    parser.add_argument(
        "--days", type=int, default=756,
        help="Calendar days of historical data to fetch (default: 756 ≈ 3 years)",
    )
    parser.add_argument(
        "--forward-days", type=int, default=5,
        help="Days ahead to define a successful trade for labeling (default: 5)",
    )
    parser.add_argument(
        "--test-fraction", type=float, default=0.20,
        help="Fraction of data reserved for testing (default: 0.20)",
    )
    parser.add_argument(
        "--output-dir", default="models",
        help="Directory to save classifier.pkl and classifier.sha256 (default: models/)",
    )
    parser.add_argument(
        "--min-auc", type=float, default=0.55,
        help="Minimum AUC-ROC required to save model (default: 0.55)",
    )
    args = parser.parse_args()

    # ── Validate environment ────────────────────────────────────────────────
    if not os.environ.get("ALPACA_API_KEY") or not os.environ.get("ALPACA_SECRET_KEY"):
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set.")
        sys.exit(1)

    # ── Resolve ticker list ─────────────────────────────────────────────────
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.ticker:
        tickers = [args.ticker.strip().upper()]
    else:
        parser.error("Provide --tickers SPY,QQQ,IWM (or legacy --ticker SPY).")
    if not tickers:
        parser.error("Empty ticker list after parsing.")

    logger.info("Training on %d ticker(s): %s", len(tickers), ", ".join(tickers))

    # ── Per-ticker load + chronological split, then concatenate ─────────────
    X_train_parts: list[pd.DataFrame] = []
    X_test_parts: list[pd.DataFrame] = []
    y_train_parts: list[pd.Series] = []
    y_test_parts: list[pd.Series] = []

    for ticker, features_df, ohlcv in load_training_data(tickers, args.days):
        labels = create_labels(ohlcv, features_df, forward_days=args.forward_days)
        features_df = features_df.reindex(labels.index)

        logger.info("[%s] Splitting %d feature rows chronologically.", ticker, len(features_df))
        X_tr, X_te, y_tr, y_te = time_based_split(
            features_df, labels, test_fraction=args.test_fraction
        )
        X_train_parts.append(X_tr)
        X_test_parts.append(X_te)
        y_train_parts.append(y_tr)
        y_test_parts.append(y_te)

    X_train = pd.concat(X_train_parts)
    X_test = pd.concat(X_test_parts)
    y_train = pd.concat(y_train_parts)
    y_test = pd.concat(y_test_parts)

    logger.info(
        "Combined train=%d rows / test=%d rows across %d ticker(s).",
        len(X_train), len(X_test), len(tickers),
    )

    model = train_and_evaluate(X_train, X_test, y_train, y_test, min_auc=args.min_auc)
    serialize_model(model, output_dir=args.output_dir)

    logger.info("Training complete.")


if __name__ == "__main__":
    main()
