/*
 * KisBrokerageFactory — LEAN Composer가 라이브 KIS 브로커리지를 생성하는 팩토리.
 *
 * config.json의 live-mode-brokerage="KisBrokerage"일 때 BrokerageSetupHandler가 이 팩토리를 찾아
 * BrokerageData(아래 키들, LeanRunner가 live config에 주입)로 KisBrokerage를 만든다.
 *
 * BrokerageData 키(=LeanRunner _build_live_config가 채움):
 *   kis-app-key / kis-app-secret / kis-account-no / kis-env(real|demo) /
 *   kis-hts-id(체결통보용, 선택) / kis-max-order-amount(원, 선택 한도) /
 *   kis-token-cache(토큰 디스크캐시 경로, 선택)
 */

using System.Collections.Generic;
using QuantConnect.Brokerages;
using QuantConnect.Configuration;
using QuantConnect.Interfaces;
using QuantConnect.Packets;
using QuantConnect.Securities;
using QuantConnect.Util;

namespace MyTrading.Kis
{
    public class KisBrokerageFactory : BrokerageFactory
    {
        public KisBrokerageFactory() : base(typeof(KisBrokerage)) { }

        public override Dictionary<string, string> BrokerageData => new Dictionary<string, string>
        {
            { "kis-app-key", Config.Get("kis-app-key") },
            { "kis-app-secret", Config.Get("kis-app-secret") },
            { "kis-account-no", Config.Get("kis-account-no") },
            { "kis-env", Config.Get("kis-env", "demo") },
            { "kis-hts-id", Config.Get("kis-hts-id") },
            { "kis-max-order-amount", Config.Get("kis-max-order-amount", "0") },
            { "kis-token-cache", Config.Get("kis-token-cache") },
        };

        public override IBrokerageModel GetBrokerageModel(IOrderProvider orderProvider) => new KisBrokerageModel();

        public override IBrokerage CreateBrokerage(LiveNodePacket job, IAlgorithm algorithm)
        {
            var data = job.BrokerageData;
            string Read(string k, string dflt = "") => data.TryGetValue(k, out var v) && !string.IsNullOrEmpty(v) ? v : dflt;

            decimal.TryParse(Read("kis-max-order-amount", "0"), out var maxAmt);

            var brokerage = new KisBrokerage(
                algorithm,
                Read("kis-app-key"),
                Read("kis-app-secret"),
                Read("kis-account-no"),
                Read("kis-env", "demo"),
                Read("kis-hts-id"),
                maxAmt,
                Read("kis-token-cache"));

            // 라이브 데이터큐 핸들러로도 동일 인스턴스를 Composer에 등록(시세/주문 동일 세션).
            Composer.Instance.AddPart<IDataQueueHandler>(brokerage);
            return brokerage;
        }

        public override void Dispose() { /* NOP */ }
    }
}
