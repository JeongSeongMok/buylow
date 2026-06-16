/*
 * TossBrokerageFactory — LEAN Composer가 라이브 토스 브로커리지를 생성하는 팩토리.
 *
 * config.json의 live-mode-brokerage="TossBrokerage"일 때 BrokerageSetupHandler가 이 팩토리를 찾아
 * BrokerageData(아래 키들, LeanRunner가 live config에 주입)로 TossBrokerage를 만든다.
 *
 * BrokerageData 키(=LeanRunner build_toss_live_config가 채움):
 *   toss-client-id / toss-client-secret /
 *   toss-max-order-amount(원, 선택 한도) / toss-token-cache(토큰 디스크캐시 경로, 선택)
 * (KIS와 달리 계좌번호·HTS ID·env 키가 없다 — accountSeq 자동해석, 폴링 체결, 실전 단일.)
 */

using System.Collections.Generic;
using QuantConnect.Brokerages;
using QuantConnect.Configuration;
using QuantConnect.Interfaces;
using QuantConnect.Packets;
using QuantConnect.Securities;
using QuantConnect.Util;

namespace MyTrading.Toss
{
    public class TossBrokerageFactory : BrokerageFactory
    {
        public TossBrokerageFactory() : base(typeof(TossBrokerage)) { }

        public override Dictionary<string, string> BrokerageData => new Dictionary<string, string>
        {
            { "toss-client-id", Config.Get("toss-client-id") },
            { "toss-client-secret", Config.Get("toss-client-secret") },
            { "toss-max-order-amount", Config.Get("toss-max-order-amount", "0") },
            { "toss-token-cache", Config.Get("toss-token-cache") },
        };

        public override IBrokerageModel GetBrokerageModel(IOrderProvider orderProvider) => new TossBrokerageModel();

        public override IBrokerage CreateBrokerage(LiveNodePacket job, IAlgorithm algorithm)
        {
            var data = job.BrokerageData;
            string Read(string k, string dflt = "") => data.TryGetValue(k, out var v) && !string.IsNullOrEmpty(v) ? v : dflt;

            decimal.TryParse(Read("toss-max-order-amount", "0"), out var maxAmt);

            var brokerage = new TossBrokerage(
                algorithm,
                Read("toss-client-id"),
                Read("toss-client-secret"),
                maxAmt,
                Read("toss-token-cache"));

            // 라이브 데이터큐 핸들러로도 동일 인스턴스를 Composer에 등록(시세/주문 동일 세션).
            Composer.Instance.AddPart<IDataQueueHandler>(brokerage);
            return brokerage;
        }

        public override void Dispose() { /* NOP */ }
    }
}
