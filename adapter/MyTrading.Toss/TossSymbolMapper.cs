/*
 * LEAN Symbol ↔ 토스 종목코드 매핑.
 *
 * KRX 종목은 6자리 숫자 코드(예: 005930)이며 LEAN Symbol.Value와 동일하게 쓴다(market="krx").
 * KIS 어댑터(KisSymbolMapper)와 동일 — 어댑터 계약상 ISymbolMapper로 분리해 둔다.
 */

using System;
using QuantConnect;
using QuantConnect.Brokerages;

namespace MyTrading.Toss
{
    public class TossSymbolMapper : ISymbolMapper
    {
        /// <summary>LEAN Symbol → 토스 6자리 코드.</summary>
        public string GetBrokerageSymbol(Symbol symbol)
        {
            if (symbol == null || string.IsNullOrEmpty(symbol.Value))
                throw new ArgumentException("유효한 Symbol이 아닙니다.", nameof(symbol));
            return symbol.Value;
        }

        /// <summary>토스 코드 → LEAN Symbol (market=krx, equity).</summary>
        public Symbol GetLeanSymbol(string brokerageSymbol, SecurityType securityType, string market,
                                    DateTime expirationDate = default, decimal strike = 0, OptionRight optionRight = 0)
        {
            if (string.IsNullOrEmpty(brokerageSymbol))
                throw new ArgumentException("토스 종목코드가 비었습니다.", nameof(brokerageSymbol));
            return Symbol.Create(brokerageSymbol, securityType,
                string.IsNullOrEmpty(market) ? TossConstants.KrxMarket : market);
        }
    }
}
