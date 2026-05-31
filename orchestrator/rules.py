"""불리언 규칙 파서 + 3치(UP/DOWN/NONE) 평가기 — 순수 모듈(LEAN 의존 없음).

사용자가 `(EMA AND MACD) OR (RSI AND MOM)` 같은 식을 쓰면, signal 라벨들의 방향
(UP/DOWN/NONE)을 받아 최종 방향을 계산한다. 대시보드 검증과 LEAN RuleAlpha 평가가 공용.

3치 결합 의미:
  AND : 피연산자가 모두 같은 방향이면 그 방향, 하나라도 다르거나 NONE이면 NONE.
  OR  : UP만 있으면 UP / DOWN만 있으면 DOWN / 충돌(둘 다)이거나 모두 NONE이면 NONE.
"""

from __future__ import annotations

import re

UP, DOWN, NONE = "UP", "DOWN", "NONE"

# AST 노드: ("signal", label) | ("and", [node, ...]) | ("or", [node, ...])


def parse_rule(expr: str):
    """불리언 식 → AST. AND/OR/괄호/라벨 지원(대소문자 무관)."""
    tokens = re.findall(r"\(|\)|[A-Za-z_][A-Za-z0-9_]*", expr or "")
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def advance():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        return tok

    def parse_atom():
        tok = peek()
        if tok == "(":
            advance()
            node = parse_or()
            if peek() != ")":
                raise ValueError("괄호가 닫히지 않음")
            advance()
            return node
        if tok is None or tok in ("(", ")") or tok.upper() in ("AND", "OR"):
            raise ValueError(f"식 오류: '{tok}' 위치")
        advance()
        return ("signal", tok)

    def parse_and():
        node = parse_atom()
        while peek() and peek().upper() == "AND":
            advance()
            node = ("and", [node, parse_atom()])
        return node

    def parse_or():
        node = parse_and()
        while peek() and peek().upper() == "OR":
            advance()
            node = ("or", [node, parse_and()])
        return node

    if not tokens:
        raise ValueError("빈 규칙식")
    ast = parse_or()
    if pos != len(tokens):
        raise ValueError("식 파싱이 끝까지 되지 않음")
    return ast


def signal_labels(ast) -> set[str]:
    """식에 등장하는 signal 라벨 집합."""
    if ast[0] == "signal":
        return {ast[1]}
    out: set[str] = set()
    for child in ast[1]:
        out |= signal_labels(child)
    return out


def eval_rule(ast, directions: dict[str, str]) -> str:
    """라벨→방향 매핑으로 식을 평가해 최종 방향(UP/DOWN/NONE) 반환."""
    if ast[0] == "signal":
        return directions.get(ast[1], NONE)
    vals = [eval_rule(c, directions) for c in ast[1]]
    if ast[0] == "and":
        if NONE in vals:
            return NONE
        return vals[0] if all(v == vals[0] for v in vals) else NONE
    # or
    ups = any(v == UP for v in vals)
    downs = any(v == DOWN for v in vals)
    if ups and not downs:
        return UP
    if downs and not ups:
        return DOWN
    return NONE
