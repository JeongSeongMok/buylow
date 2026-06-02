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
    assert s["period_days"] == 3


def test_bollinger_signal_in_catalog():
    labels = {s.label: s for s in sc.CATALOG}
    assert "BB" in labels and labels["BB"].type == "bollinger"
    # 폼에서 float 파라미터(스위칭 임계)가 제대로 캐스팅돼 시그널 구성에 들어감
    sig = sc.signals_from_form({"BB__period": "20", "BB__k": "2.5", "BB__switch_pct": "1.5"})["BB"]
    assert sig["type"] == "bollinger"
    assert sig["params"]["k"] == 2.5 and sig["params"]["switch_pct"] == 1.5
    assert sig["params"]["period"] == 20


def test_value_signal_in_catalog():
    labels = {s.label: s for s in sc.CATALOG}
    assert "VAL" in labels and labels["VAL"].type == "value"
    sig = sc.signals_from_form({"VAL__per_max": "12", "VAL__pbr_max": "0.8",
                                "VAL__roe_min": "10", "VAL__div_min": "2"})["VAL"]
    assert sig["type"] == "value"
    assert sig["params"]["per_max"] == 12.0 and sig["params"]["pbr_max"] == 0.8
    assert sig["params"]["roe_min"] == 10.0 and sig["params"]["div_min"] == 2.0


def test_flow_signal_in_catalog():
    labels = {s.label: s for s in sc.CATALOG}
    assert "FLOW" in labels and labels["FLOW"].type == "flow"
    # 체크박스: 체크한 주체만 폼에 키가 존재(외국인·개인 체크, 기관 미체크 → 키 부재)
    sig = sc.signals_from_form({"FLOW__lookback": "5", "FLOW__foreign": "1",
                                "FLOW__individual": "1"})["FLOW"]
    assert sig["params"]["lookback"] == 5
    assert sig["params"]["foreign"] == 1 and sig["params"]["individual"] == 1
    assert sig["params"]["institution"] == 0  # 미체크(키 부재) → 0


def test_bool_param_unchecked_is_zero():
    # bool 파라미터는 키가 없으면 0(기본값 1을 쓰지 않음)
    p = sc.cast_params("FLOW", {"FLOW__lookback": "7"})  # 아무 주체도 체크 안 함
    assert p["foreign"] == 0 and p["institution"] == 0 and p["individual"] == 0


def test_descriptions_hide_internal_tokens():
    # 사용자 노출 설명에 내부 신호값(UP/DOWN/NONE)이 없어야 함
    for spec in sc.CATALOG:
        assert "UP" not in spec.description
        assert "DOWN" not in spec.description
        assert "NONE" not in spec.description
