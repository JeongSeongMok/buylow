/*
 * KIS 브로커리지 모델 + 한국식 수수료 모델.
 *
 * - 기본 시장을 krx로(주식). 한국 개인 현금계좌 가정(AccountType.Cash, 레버리지 1).
 * - 수수료/세금은 market/krx.py의 korean_fee와 같은 규칙(매수: 위탁수수료, 매도: +증권거래세).
 *   백테스트(파이썬 KoreanFeeModel)와 라이브(이 모델)가 동일 비용을 쓰게 해 결과 괴리를 줄인다.
 */

using System;
using System.Collections.Generic;
using QuantConnect;
using QuantConnect.Brokerages;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Securities;
using QuantConnect.Util;

namespace MyTrading.Kis
{
    /// <summary>한국 거래비용: 매수=위탁수수료, 매도=위탁수수료+증권거래세(농특세 포함 근사). KRW.</summary>
    public class KoreanFeeModel : FeeModel
    {
        // market/krx.py 기본값과 동일.
        private const decimal CommissionRate = 0.00015m; // 0.015%
        private const decimal SellTaxRate = 0.0018m;     // 0.18% (매도만)

        public override OrderFee GetOrderFee(OrderFeeParameters parameters)
        {
            var order = parameters.Order;
            var price = order.Price != 0 ? order.Price : parameters.Security.Price;
            var value = order.AbsoluteQuantity * price;
            var fee = value * CommissionRate;
            if (order.Direction == OrderDirection.Sell)
                fee += value * SellTaxRate;
            return new OrderFee(new CashAmount(Math.Round(fee), KisConstants.KrwCurrency));
        }
    }

    public class KisBrokerageModel : DefaultBrokerageModel
    {
        private static readonly IReadOnlyDictionary<SecurityType, string> KrxMarkets =
            new Dictionary<SecurityType, string> { { SecurityType.Equity, KisConstants.KrxMarket } }.ToReadOnlyDictionary();

        public KisBrokerageModel() : base(AccountType.Cash) { }

        public override IReadOnlyDictionary<SecurityType, string> DefaultMarkets => KrxMarkets;

        public override IFeeModel GetFeeModel(Security security) => new KoreanFeeModel();

        /// <summary>현금계좌 + 한국주식은 레버리지 1.</summary>
        public override decimal GetLeverage(Security security) => 1m;

        /// <summary>지정가/시장가만 허용(트레일링/스탑 등 미지원은 LEAN 측 모델이 변환·처리).</summary>
        public override bool CanSubmitOrder(Security security, Order order, out BrokerageMessageEvent message)
        {
            if (security.Type != SecurityType.Equity)
            {
                message = new BrokerageMessageEvent(BrokerageMessageType.Warning, "NotSupported",
                    "KIS 어댑터는 국내주식(Equity)만 지원합니다.");
                return false;
            }
            if (order.Type != OrderType.Market && order.Type != OrderType.Limit
                && order.Type != OrderType.MarketOnOpen && order.Type != OrderType.MarketOnClose)
            {
                message = new BrokerageMessageEvent(BrokerageMessageType.Warning, "NotSupported",
                    $"KIS 어댑터가 지원하지 않는 주문유형: {order.Type}");
                return false;
            }
            message = null;
            return true;
        }
    }
}
