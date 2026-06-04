/*
 * buylow KIS 라이브 어댑터 — 상수 단일 출처.
 *
 * KIS OpenAPI의 엔드포인트/TR ID/URL을 한곳에 모은다. 실전(real)/모의(demo)는 TR 접두(T/V)와
 * 베이스 URL로 갈린다. (파이썬 brokers/kis.py의 시세 클라이언트와 값이 일치해야 한다 —
 * 어댑터는 라이브 주문/실시간, 파이썬은 데이터/오케스트레이터로 역할만 다르다.)
 */

namespace MyTrading.Kis
{
    /// <summary>KIS REST/WebSocket 상수 (실전/모의 분기 포함).</summary>
    public static class KisConstants
    {
        // ── REST 베이스 URL ────────────────────────────────────────────────
        public const string RealRestUrl = "https://openapi.koreainvestment.com:9443";
        public const string DemoRestUrl = "https://openapivts.koreainvestment.com:29443";

        // ── WebSocket 베이스 URL ───────────────────────────────────────────
        public const string RealWsUrl = "ws://ops.koreainvestment.com:21000";
        public const string DemoWsUrl = "ws://ops.koreainvestment.com:31000";

        // ── REST 경로 ──────────────────────────────────────────────────────
        public const string PathToken = "/oauth2/tokenP";
        public const string PathApprovalKey = "/oauth2/Approval";       // 웹소켓 접속키
        public const string PathOrderCash = "/uapi/domestic-stock/v1/trading/order-cash";
        public const string PathOrderRvseCncl = "/uapi/domestic-stock/v1/trading/order-rvsecncl";
        public const string PathInquireBalance = "/uapi/domestic-stock/v1/trading/inquire-balance";
        public const string PathInquirePsblOrder = "/uapi/domestic-stock/v1/trading/inquire-psbl-order";
        public const string PathDailyChart = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice";
        public const string PathChkHoliday = "/uapi/domestic-stock/v1/quotations/chk-holiday";

        // ── TR ID (실전 T / 모의 V) ────────────────────────────────────────
        // 주식주문(현금): 매수/매도
        public const string TrOrderBuyReal = "TTTC0012U";
        public const string TrOrderSellReal = "TTTC0011U";
        public const string TrOrderBuyDemo = "VTTC0012U";
        public const string TrOrderSellDemo = "VTTC0011U";
        // 주식주문(정정취소)
        public const string TrReviseCancelReal = "TTTC0013U";
        public const string TrReviseCancelDemo = "VTTC0013U";
        // 주식잔고조회
        public const string TrBalanceReal = "TTTC8434R";
        public const string TrBalanceDemo = "VTTC8434R";
        // 매수가능조회
        public const string TrPsblOrderReal = "TTTC8908R";
        public const string TrPsblOrderDemo = "VTTC8908R";
        // 국내휴장일조회 (실전/모의 공통)
        public const string TrChkHoliday = "CTCA0903R";
        // 기간별시세(일봉) — GetHistory 용
        public const string TrDailyChart = "FHKST03010100";

        // ── WebSocket TR ID ────────────────────────────────────────────────
        public const string WsTrPrice = "H0STCNT0";       // 실시간 체결가
        public const string WsTrQuote = "H0STASP0";        // 실시간 호가
        public const string WsTrFillReal = "H0STCNI0";     // 실전 체결통보
        public const string WsTrFillDemo = "H0STCNI9";     // 모의 체결통보

        // ── 주문구분(ORD_DVSN) ─────────────────────────────────────────────
        public const string OrdDvsnLimit = "00";   // 지정가
        public const string OrdDvsnMarket = "01";  // 시장가

        // ── 정정취소구분(RVSE_CNCL_DVSN_CD) ────────────────────────────────
        public const string RviseCode = "01";   // 정정
        public const string CancelCode = "02";  // 취소

        // ── 시장/통화 (market/krx.py와 동일해야 함) ─────────────────────────
        public const string KrxMarket = "krx";
        public const int KrxMarketId = 50;
        public const string KrwCurrency = "KRW";

        // 레이트리밋 에러코드 (초당 호출 초과)
        public const string RateLimitCode = "EGW00201";

        public static bool IsDemo(string env) =>
            string.Equals(env, "demo", System.StringComparison.OrdinalIgnoreCase);

        public static string RestUrl(string env) => IsDemo(env) ? DemoRestUrl : RealRestUrl;
        public static string WsUrl(string env) => IsDemo(env) ? DemoWsUrl : RealWsUrl;
    }
}
