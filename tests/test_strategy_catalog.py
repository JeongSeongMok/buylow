"""전략 카탈로그(순수 스펙) 단위 테스트."""

import pytest

from orchestrator import strategy_catalog as sc


def test_catalog_has_alphas():
    names = {a.name for a in sc.CATALOG}
    assert {"ema_cross", "bnf", "rsi", "momentum"} <= names


def test_cast_params_uses_defaults_when_blank():
    p = sc.cast_params("ema_cross", {"fast": "", "slow": "30"})
    assert p["fast"] == 20      # 기본값
    assert p["slow"] == 30      # 입력
    assert isinstance(p["slow"], int)


def test_cast_params_float_type():
    p = sc.cast_params("bnf", {"threshold": "0.08"})
    assert p["threshold"] == 0.08 and isinstance(p["threshold"], float)


def test_cast_params_unknown_raises():
    with pytest.raises(ValueError):
        sc.cast_params("nope", {})
