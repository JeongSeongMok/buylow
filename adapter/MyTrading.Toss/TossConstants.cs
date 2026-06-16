/*
 * buylow 토스(Toss) 라이브 어댑터 — 상수 단일 출처.
 *
 * 토스증권 OpenAPI의 엔드포인트/URL을 한곳에 모은다. KIS와 달리:
 *   - 모의투자 서버가 없다(실전 단일 베이스 URL).
 *   - 인증은 OAuth2 client-credentials(form-urlencoded), 계좌는 X-Tossinvest-Account 헤더.
 *   - 체결통보/실시간 시세 웹소켓이 없어 주문/시세 모두 REST 폴링으로 처리한다.
 * 시장/통화/수수료는 KIS 어댑터·market/krx.py와 동일(krx, 50, KRW).
 */

namespace MyTrading.Toss
{
    /// <summary>토스증권 OpenAPI 상수.</summary>
    public static class TossConstants
    {
        // ── REST 베이스 URL (실전 단일) ────────────────────────────────────
        public const string RestUrl = "https://openapi.tossinvest.com";

        // ── REST 경로 ──────────────────────────────────────────────────────
        public const string PathToken = "/oauth2/token";
        public const string PathAccounts = "/api/v1/accounts";
        public const string PathHoldings = "/api/v1/holdings";
        public const string PathBuyingPower = "/api/v1/buying-power";
        public const string PathOrders = "/api/v1/orders";          // POST 생성 / GET 목록
        public const string PathMarketCalendarKr = "/api/v1/market-calendar/KR";
        public const string PathPrices = "/api/v1/prices";
        // 주문 상세/정정/취소는 {orderId}를 경로에 끼운다.
        public static string PathOrder(string orderId) => PathOrders + "/" + orderId;
        public static string PathOrderModify(string orderId) => PathOrder(orderId) + "/modify";
        public static string PathOrderCancel(string orderId) => PathOrder(orderId) + "/cancel";

        // ── 시장/통화 (market/krx.py·KIS 어댑터와 동일해야 함) ───────────────
        public const string KrxMarket = "krx";
        public const int KrxMarketId = 50;
        public const string KrwCurrency = "KRW";
    }
}
