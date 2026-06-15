/*
 * KIS 어댑터 순수 로직 테스트 — 웹소켓 프레임 파싱(인덱스 산식), 심볼 매핑, 환경 분기.
 * 이 부분이 e2e 없이 회귀를 잡을 수 있는 핵심이라 우선 커버한다.
 */

using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Xunit;
using MyTrading.Kis;

namespace MyTrading.Kis.Tests
{
    public class KisConstantsTests
    {
        [Fact]
        public void Demo_uses_vts_urls()
        {
            Assert.True(KisConstants.IsDemo("demo"));
            Assert.Equal(KisConstants.DemoRestUrl, KisConstants.RestUrl("demo"));
            Assert.Equal(KisConstants.DemoWsUrl, KisConstants.WsUrl("demo"));
        }

        [Fact]
        public void Real_uses_prod_urls()
        {
            Assert.False(KisConstants.IsDemo("real"));
            Assert.Equal(KisConstants.RealRestUrl, KisConstants.RestUrl("real"));
            Assert.Equal(KisConstants.RealWsUrl, KisConstants.WsUrl("real"));
        }
    }

    // 참고: KisSymbolMapper 라운드트립은 Symbol.Create(equity)가 LEAN 맵파일 프로바이더(라이브
    // config에서 주입)를 요구해 단위테스트로 격리되지 않는다 → 라이브 경로에서 검증한다.

    public class KisFrameParsingTests
    {
        [Fact]
        public void ParseTradeBody_extracts_price_and_volume()
        {
            // H0STCNT0: 0=종목, 1=시각, 2=현재가, 12=체결량 (그 사이 필드는 임의값)
            var fields = new string[13];
            for (var i = 0; i < fields.Length; i++) fields[i] = "0";
            fields[0] = "005930"; fields[1] = "093015"; fields[2] = "71500"; fields[12] = "30";
            var body = string.Join("^", fields);

            var trade = KisWebSocketClient.ParseTradeBody(body);
            Assert.NotNull(trade);
            Assert.Equal("005930", trade.Item1);
            Assert.Equal(71500m, trade.Item2);
            Assert.Equal(30m, trade.Item3);
            Assert.Equal("093015", trade.Item4);
        }

        [Fact]
        public void ParseTradeBody_returns_null_when_too_short()
        {
            Assert.Null(KisWebSocketClient.ParseTradeBody("005930^093015"));
            Assert.Null(KisWebSocketClient.ParseTradeBody(null));
        }

        [Fact]
        public void ParseFillBody_extracts_fill()
        {
            // H0STCNI0: 2=주문번호, 4=매도매수(02매수), 8=종목, 9=체결수량, 10=체결단가, 11=시각, 13=체결여부(2=체결)
            var fields = new string[14];
            for (var i = 0; i < fields.Length; i++) fields[i] = "";
            fields[2] = "0000123456"; fields[4] = "02"; fields[8] = "005930";
            fields[9] = "10"; fields[10] = "71000"; fields[11] = "093020"; fields[13] = "2";
            var body = string.Join("^", fields);

            var fill = KisWebSocketClient.ParseFillBody(body);
            Assert.NotNull(fill);
            Assert.Equal("0000123456", fill.OrderNo);
            Assert.True(fill.Buy);
            Assert.Equal("005930", fill.Ticker);
            Assert.Equal(10, fill.FillQty);
            Assert.Equal(71000m, fill.FillPrice);
            Assert.True(fill.IsFilled);
        }

        [Fact]
        public void ParseFillBody_sell_and_unfilled()
        {
            var fields = new string[14];
            for (var i = 0; i < fields.Length; i++) fields[i] = "";
            fields[2] = "9"; fields[4] = "01"; fields[8] = "000660";
            fields[9] = "5"; fields[10] = "120000"; fields[11] = "1"; fields[13] = "1"; // 접수(미체결)
            var fill = KisWebSocketClient.ParseFillBody(string.Join("^", fields));
            Assert.False(fill.Buy);          // 01 = 매도
            Assert.False(fill.IsFilled);     // 13 != "2"
        }
    }

    // 잔고조회 레이트리밋 재시도 + 단기 캐시 — LEAN init이 inquire-balance를 연달아 때려
    // KIS 초당 한도(rt_cd=1)에 막혀 라이브가 죽던 회귀를 고정한다.
    public class KisBalanceRetryTests
    {
        // 토큰 POST는 토큰을 주고, inquire-balance는 1회차 초당한도(rt_cd=1) → 이후 성공으로 응답.
        class FakeHandler : HttpMessageHandler
        {
            public int BalanceCalls;

            private HttpResponseMessage Respond(HttpRequestMessage request)
            {
                var path = request.RequestUri.AbsolutePath;
                string body;
                if (path.Contains("tokenP"))
                    body = "{\"access_token\":\"T\",\"expires_in\":86400}";
                else if (path.Contains("inquire-balance"))
                {
                    BalanceCalls++;
                    body = BalanceCalls == 1
                        ? "{\"rt_cd\":\"1\",\"msg1\":\"초당 거래건수를 초과하였습니다.\"}"
                        : "{\"rt_cd\":\"0\",\"output1\":[],\"output2\":[{\"dnca_tot_amt\":\"1000\",\"prvs_rcdl_excc_amt\":\"900\"}]}";
                }
                else body = "{}";
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body) };
            }

            protected override HttpResponseMessage Send(HttpRequestMessage request, CancellationToken ct)
                => Respond(request);

            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
                => Task.FromResult(Respond(request));
        }

        private static KisRestClient Client(FakeHandler h) =>
            new KisRestClient("ak123456", "sk", "demo", null, new HttpClient(h));

        [Fact]
        public void InquireBalance_retries_on_rate_limit_then_succeeds()
        {
            var h = new FakeHandler();
            var bal = Client(h).InquireBalance("12345678", "01");
            Assert.Equal(900m, bal.D2Deposit);   // 성공 응답 파싱
            Assert.Equal(2, h.BalanceCalls);      // 1회 한도초과 + 1회 성공
        }

        [Fact]
        public void InquireBalance_caches_within_ttl()
        {
            var h = new FakeHandler();
            var client = Client(h);
            client.InquireBalance("12345678", "01");   // 한도(1) + 성공(2)
            var after = h.BalanceCalls;
            client.InquireBalance("12345678", "01");   // TTL(2초) 내 → 캐시 반환, 추가 호출 없음
            Assert.Equal(after, h.BalanceCalls);
        }
    }

    // 주문 페이싱 + 레이트리밋/전송오류 재시도 — 장 시작 대량 주문 폭주에 EGW00201 거부·전송예외가
    // 나며 라이브가 RuntimeError로 종료되던 회귀를 고정한다(주문은 끝까지 결과로 반환, 예외 전파 X).
    public class KisOrderRetryTests
    {
        class OrderHandler : HttpMessageHandler
        {
            public int OrderCalls;
            public int FailFirstHttp;    // 처음 N회 전송예외(HttpRequestException)
            public int RateLimitFirst;   // 처음 N회 rt_cd=1(초당 거래건수 초과)

            private HttpResponseMessage Respond(HttpRequestMessage request)
            {
                var path = request.RequestUri.AbsolutePath;
                if (path.Contains("tokenP"))
                    return Ok("{\"access_token\":\"T\",\"expires_in\":86400}");
                if (path.Contains("order-cash"))
                {
                    OrderCalls++;
                    if (OrderCalls <= FailFirstHttp)
                        throw new HttpRequestException("An error occurred while sending the request.");
                    if (OrderCalls <= FailFirstHttp + RateLimitFirst)
                        return Ok("{\"rt_cd\":\"1\",\"msg_cd\":\"EGW00201\",\"msg1\":\"초당 거래건수를 초과하였습니다.\"}");
                    return Ok("{\"rt_cd\":\"0\",\"msg1\":\"정상\",\"output\":{\"ODNO\":\"0001\",\"KRX_FWDG_ORD_ORGNO\":\"00950\",\"ORD_TMD\":\"090000\"}}");
                }
                return Ok("{}");
            }

            private static HttpResponseMessage Ok(string body) =>
                new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body) };

            protected override HttpResponseMessage Send(HttpRequestMessage request, CancellationToken ct)
                => Respond(request);

            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
                => Task.FromResult(Respond(request));
        }

        private static KisRestClient Client(OrderHandler h) =>
            new KisRestClient("ak123456", "sk", "demo", null, new HttpClient(h));

        [Fact]
        public void OrderCash_retries_on_rate_limit_then_succeeds()
        {
            var h = new OrderHandler { RateLimitFirst = 1 };
            var res = Client(h).OrderCash("12345678", "01", "005930", true, 10, 0m, KisConstants.OrdDvsnMarket);
            Assert.True(res.Ok);
            Assert.Equal("0001", res.OrderNo);
            Assert.Equal(2, h.OrderCalls);     // 1회 한도초과 + 1회 성공
        }

        [Fact]
        public void OrderCash_retries_on_transport_error_then_succeeds()
        {
            var h = new OrderHandler { FailFirstHttp = 1 };
            var res = Client(h).OrderCash("12345678", "01", "005930", true, 10, 0m, KisConstants.OrdDvsnMarket);
            Assert.True(res.Ok);
            Assert.Equal(2, h.OrderCalls);
        }

        [Fact]
        public void OrderCash_returns_failure_without_throwing_when_exhausted()
        {
            // 계속 전송오류여도 예외를 던지지 않고 Ok=false(TRANSPORT)를 반환해 알고리즘이 살아있게 한다.
            var h = new OrderHandler { FailFirstHttp = 99 };
            var res = Client(h).OrderCash("12345678", "01", "005930", true, 10, 0m, KisConstants.OrdDvsnMarket);
            Assert.False(res.Ok);
            Assert.Equal("TRANSPORT", res.Code);
            Assert.Equal(4, h.OrderCalls);     // OrderMaxAttempts 만큼 시도
        }
    }
}
