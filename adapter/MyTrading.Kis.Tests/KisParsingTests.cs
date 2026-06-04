/*
 * KIS 어댑터 순수 로직 테스트 — 웹소켓 프레임 파싱(인덱스 산식), 심볼 매핑, 환경 분기.
 * 이 부분이 e2e 없이 회귀를 잡을 수 있는 핵심이라 우선 커버한다.
 */

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
}
