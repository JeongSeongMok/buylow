"""신호 진단(추정) — 백테스트가 왜 매수하지 않았는지 사후 추정.

저장된 전략 spec(signals/rule)과 적재 데이터(일봉/수급/펀더멘털)로, 기간 동안 각 신호가
얼마나 자주 UP이었는지와 규칙(rule)이 매수(UP)로 평가된 비율을 계산한다. 매수신호가 적으면
UP 비율이 낮은 신호가 '차단 후보'다(예: 추세는 좋아도 FLOW가 0% → 수급이 매수를 막음).

⚠️ 추정의 한계: 분봉 백테스트는 장중 매분 평가하지만 이 진단은 **일봉 종가 기준 근사**다(가격계열은
그날 종가, 수급·가치는 전날값). 실제 체결과 정확히 일치하지 않으며 '경향 파악'용이다. 순수 함수
(orchestrator.indicators + rules)만 쓰므로 LEAN/재실행 없이 결과 화면에서 가볍게 계산한다.
"""

from __future__ import annotations

from pathlib import Path

from etl.catalog import read_price_daily, read_flow
from orchestrator import indicators as ind
from orchestrator.rules import eval_rule, parse_rule, signal_labels

# 유니버스가 크면(전체종목 등) 전부 계산하면 무거우므로 앞에서 이만큼만 표본으로 본다.
DEFAULT_CAP = 12


def _read_fund(data_dir: str | Path, ticker: str) -> list[dict]:
    """펀더멘털 CSV(YYYYMMDD,per,pbr,div) → [{date(ISO),per,pbr,div}]. 없으면 []."""
    p = Path(data_dir) / "krx" / "fundamental" / f"{ticker}.csv"
    out: list[dict] = []
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        x = ln.split(",")
        if len(x) >= 4 and x[0].isdigit() and len(x[0]) == 8:
            out.append({"date": f"{x[0][:4]}-{x[0][4:6]}-{x[0][6:8]}",
                        "per": float(x[1] or 0), "pbr": float(x[2] or 0), "div": float(x[3] or 0)})
    return out


def _D(up: bool, dn: bool) -> str:
    return "UP" if up else "DOWN" if dn else "NONE"


def _direction(label: str, signals: dict, closes: list[float],
               flows: list[dict], funds: list[dict], dt: str) -> str:
    """한 신호의 방향(UP/DOWN/NONE) — strategies/signals.py의 판정 규칙과 동일(순수 재현)."""
    spec = signals[label]
    t, p = spec.get("type"), spec.get("params") or {}
    if t == "ema":
        f, sl = ind.ema(closes, int(p["fast"])), ind.ema(closes, int(p["slow"]))
        return "NONE" if None in (f, sl) else _D(f > sl, f < sl)
    if t == "macd":
        m = ind.macd(closes, int(p["fast"]), int(p["slow"]), int(p["signal"]))
        return "NONE" if (not m or m[1] is None) else _D(m[0] > m[1], m[0] < m[1])
    if t == "rsi":
        v = ind.rsi(closes, int(p["period"]))
        return "NONE" if v is None else _D(v < float(p["oversold"]), v > float(p["overbought"]))
    if t == "momentum":
        v = ind.roc(closes, int(p["lookback"]))
        return "NONE" if v is None else _D(v > 0, v < 0)
    if t == "bollinger":
        b = ind.bollinger(closes, int(p["period"]), float(p["k"]))
        if not b:
            return "NONE"
        up, _mid, lo = b
        pr, sw = closes[-1], float(p["switch_pct"])
        if pr >= up:
            return "UP" if pr >= up * (1 + sw / 100) else "DOWN"
        if pr <= lo:
            return "DOWN" if pr <= lo * (1 - sw / 100) else "UP"
        return "NONE"
    if t == "flow":
        keys = [k for k in ("foreign", "institution", "individual") if p.get(k)]
        rec = [f for f in flows if f["date"] < dt][-int(p["lookback"]):]  # 전날까지 N거래일
        if len(rec) < int(p["lookback"]) or not keys:
            return "NONE"
        net = sum(sum(f[k] for k in keys) for f in rec)
        return _D(net > 0, net < 0)
    if t == "value":
        rec = [f for f in funds if f["date"] < dt]  # 전날값
        if not rec:
            return "NONE"
        x = rec[-1]
        per, pbr, div = x["per"], x["pbr"], x["div"]
        if not (0 < per <= float(p["per_max"])):
            return "NONE"
        if not (0 < pbr <= float(p["pbr_max"])):
            return "NONE"
        if (pbr / per) * 100 < float(p["roe_min"]):
            return "NONE"
        if div < float(p["div_min"]):
            return "NONE"
        return "UP"
    return "NONE"


def analyze_run(spec: dict, data_dir: str | Path, *, cap: int = DEFAULT_CAP) -> dict | None:
    """백테스트 spec + 적재 데이터로 신호 진단 요약. 정보 부족 시 None.

    반환: {start,end,tickers,universe_total,sampled,evals,buy_pct,up_pct,blockers}.
    up_pct: 라벨→UP 비율(%). buy_pct: rule이 매수(UP)로 평가된 비율(%). blockers: rule에 쓰였는데
    UP 비율이 낮아(매수를 막은 것으로 보이는) 신호 라벨.
    """
    signals = spec.get("signals") or {}
    rule = spec.get("rule")
    uni = spec.get("universe") or []
    start, end = spec.get("start"), spec.get("end")
    if not (signals and rule and uni and start and end):
        return None
    ast = parse_rule(rule)
    used = [L for L in signals if L in signal_labels(ast)]  # 규칙에 실제 쓰인 신호만
    if not used:
        return None

    tickers = uni[:cap]
    total = buy = 0
    up = {L: 0 for L in used}
    for tk in tickers:
        prices = read_price_daily(data_dir, tk)
        flows = read_flow(data_dir, tk)
        funds = _read_fund(data_dir, tk)
        closes_all = [(p["date"], p["close"]) for p in prices]
        for i, (dt, _c) in enumerate(closes_all):
            if not (start <= dt <= end):
                continue
            closes = [c for _d, c in closes_all[:i + 1]]
            dirs = {L: _direction(L, signals, closes, flows, funds, dt) for L in used}
            for L in used:
                if dirs[L] == "UP":
                    up[L] += 1
            if eval_rule(ast, dirs) == "UP":
                buy += 1
            total += 1
    if total == 0:
        return None
    up_pct = {L: round(up[L] / total * 100) for L in used}
    return {
        "start": start, "end": end,
        "tickers": len(tickers), "universe_total": len(uni), "sampled": len(uni) > cap,
        "evals": total, "buy_pct": round(buy / total * 100, 1),
        "up_pct": up_pct,
        # 매수신호가 적을 때, UP 비율이 낮은(20% 미만) 신호를 차단 후보로(낮은 순).
        "blockers": sorted([L for L in used if up_pct[L] < 20], key=lambda L: up_pct[L]),
    }
