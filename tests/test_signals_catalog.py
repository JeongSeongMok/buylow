"""signal 카탈로그 + 조건 그룹 빌더(그룹 안 AND, 그룹끼리 OR) 단위 테스트."""

from orchestrator import signals_catalog as sc


def test_rule_from_groups_and_or():
    assert sc.rule_from_groups([["EMA", "MACD"], ["RSI"]]) == "(EMA AND MACD) OR RSI"
    assert sc.rule_from_groups([["EMA"]]) == "EMA"               # 단일 조건은 괄호 생략
    assert sc.rule_from_groups([["RSI", "MOM"]]) == "(RSI AND MOM)"


def test_rule_from_groups_orders_by_catalog_and_skips_empty():
    # 입력 순서와 무관하게 카탈로그 순서(EMA<MACD<RSI<MOM)로 정렬, 빈 그룹은 제외
    assert sc.rule_from_groups([["MACD", "EMA"], [], ["MOM", "RSI"]]) == "(EMA AND MACD) OR (RSI AND MOM)"


def test_groups_from_form_reconstructs_structure():
    form = {"g0_EMA": "1", "g0_MACD": "1", "g1_RSI": "1",
            "EMA__fast": "12", "bogus_X": "1", "g2_UNKNOWN": "1"}
    # 미정의 라벨(UNKNOWN)과 g 형식 아닌 키는 무시
    assert sc.groups_from_form(form) == [["EMA", "MACD"], ["RSI"]]


def test_groups_from_form_empty():
    assert sc.groups_from_form({"period_days": "5"}) == []


def test_default_strategy_has_groups_and_defaults():
    s = sc.default_strategy()
    assert s["groups"] == [["EMA", "MACD"], ["RSI"]]
    assert s["rule"] == "(EMA AND MACD) OR RSI"
    assert s["signals"]["EMA"]["params"]["fast"] == 12
    assert s["period_days"] == 5


def test_descriptions_hide_internal_tokens():
    # 사용자 노출 설명에 내부 신호값(UP/DOWN/NONE)이 없어야 함
    for spec in sc.CATALOG:
        assert "UP" not in spec.description
        assert "DOWN" not in spec.description
        assert "NONE" not in spec.description
