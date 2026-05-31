# KRX 수급 커스텀 데이터 (LEAN PythonData).
#
# 수급은 LEAN 표준 타입(TradeBar 등)이 아니므로 커스텀 데이터로 읽는다. etl/flow.py 가 만든
# data/krx/flow/<ticker>.csv (라인: YYYYMMDD,foreign,institution,individual) 를 파싱한다.
#
# look-ahead 방지: 수급(D)은 장 마감 후 확정 → 시각을 D+1로 둬서 다음날부터 보이게 한다.
import os
from datetime import datetime, timedelta

from AlgorithmImports import *


class KrxFlow(PythonData):
    def get_source(self, config, date, is_live):
        path = os.path.join(Globals.data_folder, "krx", "flow", f"{config.symbol.value}.csv")
        return SubscriptionDataSource(path, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        if not line or not line[0].isdigit():
            return None
        p = line.split(",")
        flow = KrxFlow()
        flow.symbol = config.symbol
        # 수급(D)은 D+1부터 사용 가능하게 (look-ahead 방지)
        flow.time = datetime.strptime(p[0], "%Y%m%d") + timedelta(days=1)
        flow["foreign"] = float(p[1])
        flow["institution"] = float(p[2])
        flow["individual"] = float(p[3])
        flow.value = float(p[1])  # 기본값 = 외국인 순매수
        return flow


class KrxFundamental(PythonData):
    """KRX 투자지표 커스텀 데이터. etl/fundamental.py 의 data/krx/fundamental/<ticker>.csv
    (라인: YYYYMMDD,per,pbr,div) 를 파싱. 수급과 동일하게 D+1부터 노출."""

    def get_source(self, config, date, is_live):
        path = os.path.join(Globals.data_folder, "krx", "fundamental", f"{config.symbol.value}.csv")
        return SubscriptionDataSource(path, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        if not line or not line[0].isdigit():
            return None
        p = line.split(",")
        f = KrxFundamental()
        f.symbol = config.symbol
        f.time = datetime.strptime(p[0], "%Y%m%d") + timedelta(days=1)
        f["per"] = float(p[1])
        f["pbr"] = float(p[2])
        f["div"] = float(p[3])
        f.value = float(p[2])  # 기본값 = PBR
        return f
