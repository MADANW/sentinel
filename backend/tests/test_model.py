"""
test_model.py — Unit tests for model.py.

Tests SHA-256 integrity verification, path security, output clamping,
and model caching. Uses a real (tiny) XGBoost model in some tests.
"""

from __future__ import annotations

import hashlib
import math
import os
import pickle
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.core.feature_engineering import FEATURE_COLUMNS


def _make_feature_df() -> pd.DataFrame:
    """Build a minimal valid single-row feature DataFrame."""
    values = [0.0, 0.001, -0.001, 55.0, 0.01, 0.05, 0.002, 0.003, 0.48, math.log1p(12.0), -0.5, 0.0]
    assert len(values) == len(FEATURE_COLUMNS), "update _make_feature_df when FEATURE_COLUMNS changes"
    return pd.DataFrame([values], columns=FEATURE_COLUMNS)


def _write_model_files(tmpdir: str, model) -> tuple[str, str]:
    """Write model pkl and sha256 to tmpdir. Returns (pkl_path, hash_path)."""
    pkl_path = os.path.join(tmpdir, "classifier.pkl")
    hash_path = os.path.join(tmpdir, "classifier.sha256")

    model_bytes = pickle.dumps(model)
    sha256 = hashlib.sha256(model_bytes).hexdigest()

    with open(pkl_path, "wb") as f:
        f.write(model_bytes)
    with open(hash_path, "w") as f:
        f.write(sha256)

    return pkl_path, hash_path


class _DummyModel:
    """A picklable dummy model that returns a fixed probability."""
    def __init__(self, return_proba: float = 0.70):
        self.return_proba = return_proba

    def predict_proba(self, features):
        import numpy as np
        n = len(features)
        return np.array([[1 - self.return_proba, self.return_proba]] * n)


def _make_mock_model(return_proba: float = 0.70) -> "_DummyModel":
    """Create a picklable dummy model that returns a specific probability."""
    return _DummyModel(return_proba)


def _reset_cache():
    """Reset the model cache between tests."""
    import backend.core.model as m
    m._reset_model_cache()


# ── Integrity verification tests ─────────────────────────────────────────────

class TestModelIntegrity:
    def test_hash_match_loads_successfully(self):
        from backend.core.model import _load_model

        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model()
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            # Should not raise
            loaded = _load_model(model_path=pkl_path, hash_path=hash_path)
            assert loaded is not None

    def test_hash_mismatch_raises_tampering_error(self):
        from backend.core.model import _load_model, ModelTamperingError

        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model()
            pkl_path, hash_path = _write_model_files(tmpdir, model)

            # Corrupt the hash file
            with open(hash_path, "w") as f:
                f.write("deadbeef" * 8)  # wrong hash (64 hex chars)

            with pytest.raises(ModelTamperingError):
                _load_model(model_path=pkl_path, hash_path=hash_path)

    def test_missing_hash_file_raises_model_error(self):
        from backend.core.model import _load_model, ModelError

        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model()
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            os.remove(hash_path)

            with pytest.raises(ModelError, match="[Hh]ash file"):
                _load_model(model_path=pkl_path, hash_path=hash_path)

    def test_missing_model_file_raises_model_error(self):
        from backend.core.model import _load_model, ModelError

        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model()
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            os.remove(pkl_path)

            with pytest.raises(ModelError, match="[Mm]odel file"):
                _load_model(model_path=pkl_path, hash_path=hash_path)


# ── Path security tests ───────────────────────────────────────────────────────

class TestPathSecurity:
    """
    Test that directory traversal sequences in env-var-sourced paths are rejected.
    The attack surface is MODEL_PATH / MODEL_HASH_PATH environment variables.
    Absolute paths are allowed (admin env var access implies file access anyway).
    """

    def test_directory_traversal_in_model_env_var_rejected(self):
        from backend.core.model import _load_model, ModelError

        env = {"MODEL_PATH": "../../etc/passwd", "MODEL_HASH_PATH": "models/classifier.sha256"}
        with patch.dict(os.environ, env):
            with pytest.raises(ModelError, match="traversal"):
                _load_model()

    def test_directory_traversal_in_hash_env_var_rejected(self):
        from backend.core.model import _load_model, ModelError

        env = {"MODEL_PATH": "models/classifier.pkl", "MODEL_HASH_PATH": "../evil.sha256"}
        with patch.dict(os.environ, env):
            with pytest.raises(ModelError, match="traversal"):
                _load_model()

    def test_relative_dotdot_in_model_path_rejected(self):
        from backend.core.model import _load_model, ModelError

        env = {"MODEL_PATH": "models/../../../tmp/evil.pkl", "MODEL_HASH_PATH": "models/classifier.sha256"}
        with patch.dict(os.environ, env):
            with pytest.raises(ModelError, match="traversal"):
                _load_model()


# ── Inference tests ───────────────────────────────────────────────────────────

class TestPredictDirection:
    def setup_method(self):
        _reset_cache()

    def teardown_method(self):
        _reset_cache()

    def test_returns_float_in_range(self):
        from backend.core.model import predict_direction

        features = _make_feature_df()
        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model(return_proba=0.70)
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            with patch.dict(os.environ, {"MODEL_PATH": pkl_path, "MODEL_HASH_PATH": hash_path}):
                result = predict_direction(features)

        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
        assert abs(result - 0.70) < 1e-6

    def test_clamps_above_one(self):
        from backend.core.model import predict_direction

        features = _make_feature_df()
        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model(return_proba=1.5)  # out of range
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            with patch.dict(os.environ, {"MODEL_PATH": pkl_path, "MODEL_HASH_PATH": hash_path}):
                result = predict_direction(features)

        assert result == 1.0

    def test_clamps_below_zero(self):
        from backend.core.model import predict_direction

        features = _make_feature_df()
        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model(return_proba=-0.1)  # out of range
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            with patch.dict(os.environ, {"MODEL_PATH": pkl_path, "MODEL_HASH_PATH": hash_path}):
                result = predict_direction(features)

        assert result == 0.0

    def test_wrong_feature_columns_raises(self):
        from backend.core.model import predict_direction, ModelError

        bad_features = pd.DataFrame([[0.0] * 8], columns=[f"bad_{i}" for i in range(8)])
        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model()
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            with patch.dict(os.environ, {"MODEL_PATH": pkl_path, "MODEL_HASH_PATH": hash_path}):
                with pytest.raises(ModelError, match="[Cc]olumn"):
                    predict_direction(bad_features)

    def test_nan_in_features_raises(self):
        from backend.core.model import predict_direction, ModelError

        features = _make_feature_df()
        features.iloc[0, 0] = float("nan")
        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model()
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            with patch.dict(os.environ, {"MODEL_PATH": pkl_path, "MODEL_HASH_PATH": hash_path}):
                with pytest.raises(ModelError, match="NaN"):
                    predict_direction(features)

    def test_multi_row_dataframe_raises(self):
        from backend.core.model import predict_direction, ModelError

        n = len(FEATURE_COLUMNS)
        features = pd.DataFrame(
            [[0.0] * n, [0.1] * n], columns=FEATURE_COLUMNS
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model()
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            with patch.dict(os.environ, {"MODEL_PATH": pkl_path, "MODEL_HASH_PATH": hash_path}):
                with pytest.raises(ModelError, match="single-row"):
                    predict_direction(features)

    def test_model_loaded_once_via_cache(self):
        from backend.core.model import predict_direction

        features = _make_feature_df()
        with tempfile.TemporaryDirectory() as tmpdir:
            model = _make_mock_model()
            pkl_path, hash_path = _write_model_files(tmpdir, model)
            with patch.dict(os.environ, {"MODEL_PATH": pkl_path, "MODEL_HASH_PATH": hash_path}):
                # Patch _load_model to count calls
                with patch("backend.core.model._load_model", wraps=lambda mp=None, hp=None: model) as mock_load:
                    predict_direction(features)
                    predict_direction(features)
                    # Should be called exactly once (second call uses cache)
                    assert mock_load.call_count == 1
