"""규칙 파서 + 3치 평가기 단위 테스트 (핵심 로직)."""

import pytest

from orchestrator.rules import UP, DOWN, NONE, parse_rule, eval_rule, signal_labels


def ev(expr, directions):
    return eval_rule(parse_rule(expr), directions)


def test_single_signal():
    assert ev("EMA", {"EMA": UP}) == UP
    assert ev("EMA", {"EMA": DOWN}) == DOWN
    assert ev("EMA", {}) == NONE


def test_and_requires_same_direction():
    assert ev("EMA AND MACD", {"EMA": UP, "MACD": UP}) == UP
    assert ev("EMA AND MACD", {"EMA": DOWN, "MACD": DOWN}) == DOWN
    assert ev("EMA AND MACD", {"EMA": UP, "MACD": DOWN}) == NONE   # 방향 충돌
    assert ev("EMA AND MACD", {"EMA": UP, "MACD": NONE}) == NONE   # 하나 NONE


def test_or_semantics():
    assert ev("EMA OR MACD", {"EMA": UP, "MACD": NONE}) == UP
    assert ev("EMA OR MACD", {"EMA": NONE, "MACD": DOWN}) == DOWN
    assert ev("EMA OR MACD", {"EMA": UP, "MACD": DOWN}) == NONE    # 충돌
    assert ev("EMA OR MACD", {"EMA": NONE, "MACD": NONE}) == NONE


def test_nested_expression():
    expr = "(EMA AND MACD) OR (RSI AND MOM)"
    assert ev(expr, {"EMA": UP, "MACD": UP, "RSI": NONE, "MOM": NONE}) == UP
    assert ev(expr, {"EMA": UP, "MACD": DOWN, "RSI": UP, "MOM": UP}) == UP   # 둘째 그룹
    assert ev(expr, {"EMA": UP, "MACD": DOWN, "RSI": UP, "MOM": DOWN}) == NONE


def test_signal_labels():
    assert signal_labels(parse_rule("(EMA AND MACD) OR RSI")) == {"EMA", "MACD", "RSI"}


def test_parse_errors():
    with pytest.raises(ValueError):
        parse_rule("(EMA AND")        # 괄호 안 닫힘
    with pytest.raises(ValueError):
        parse_rule("EMA AND AND MACD")  # 연산자 연속
    with pytest.raises(ValueError):
        parse_rule("")                  # 빈 식
