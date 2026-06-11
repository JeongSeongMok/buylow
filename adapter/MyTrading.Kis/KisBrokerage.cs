/*
 * KisBrokerage — KIS 한국투자증권 LEAN 라이브 브로커리지 + 실시간 데이터큐.
 *
 * 역할: LEAN 라이브 엔진이 전략(①선별 + ②타이밍, 백테스트와 동일 코드)이 만든 주문을 이 클래스를 통해
 * KIS로 실제 전송하고, 체결통보/시세를 받아 LEAN에 되먹인다.
 *
 * ★ 무장(arming) 게이트는 제거됨: enabled면 실전(real)·모의(demo) 모두 주문을 바로 전송한다.
 *   유일한 선택적 방벽은 max_order_amount(원>0)로, 1건 주문금액이 한도를 넘으면 거부한다(0=비활성).
 *   (자세한 정책은 docs/LIVE_KIS.md.)
 *
 * 한 클래스가 IBrokerage와 IDataQueueHandler를 모두 구현 — KIS는 동일 토큰/세션 위에서 주문과 시세가
 * 함께 돌아가므로 묶는 게 자연스럽다(zerodha 등 LEAN 다른 어댑터와 동일 패턴).
 */

using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using QuantConnect;
using QuantConnect.Brokerages;
using QuantConnect.Configuration;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Logging;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Packets;
using QuantConnect.Securities;
using QuantConnect.Util;

namespace MyTrading.Kis
{
    public class KisBrokerage : Brokerage, IDataQueueHandler
    {
        static KisBrokerage()
        {
            // C# 런타임에도 krx 시장을 등록(시장코드↔id). market/krx.py(백테스트 데이터 주입)와 동일 id.
            try { Market.Add(KisConstants.KrxMarket, KisConstants.KrxMarketId); }
            catch { /* 이미 등록됨 */ }
        }

        private readonly IAlgorithm _algorithm;
        private readonly KisRestClient _rest;
        private readonly KisSymbolMapper _symbolMapper = new KisSymbolMapper();
        private readonly IDataAggregator _aggregator;
        private readonly string _cano;
        private readonly string _acntPrdtCd;
        private readonly string _env;
        private readonly string _htsId;
        private readonly decimal _maxOrderAmount;

        private KisWebSocketClient _ws;
        private bool _connected;
        // 부분체결 누적 추적(brokerId=ODNO → 체결수량 합). 풀필/부분 판정용.
        private readonly ConcurrentDictionary<string, int> _filledQty = new ConcurrentDictionary<string, int>();

        // 연결 판정은 주문 경로(REST) 기준. WS는 실시간 시세/체결통보용으로 Connect()에서 비동기로
        // 열리며(연결까지 수십~수백 ms) 실패해도 경고만 낸다(주문/잔고는 REST라 가능). LEAN의
        // BrokerageSetupHandler는 Connect() 직후 IsConnected를 검사하는데, 여기에 _ws.IsConnected를
        // 묶으면 WS가 아직 안 열려 false → "Unable to connect to brokerage"로 라이브 초기화가 죽는다.
        // REST 토큰 발급 성공(=_connected)만으로 판정한다.
        public override bool IsConnected => _connected;

        public KisBrokerage(IAlgorithm algorithm, string appKey, string appSecret, string accountNo,
                            string env, string htsId, decimal maxOrderAmount,
                            string tokenCachePath)
            : base("KisBrokerage")
        {
            _algorithm = algorithm;
            _env = string.IsNullOrEmpty(env) ? "demo" : env;
            _htsId = htsId;
            _maxOrderAmount = maxOrderAmount;
            AccountBaseCurrency = KisConstants.KrwCurrency;

            // 계좌번호 "12345678-01" → CANO=12345678, ACNT_PRDT_CD=01
            var parts = (accountNo ?? "").Split('-');
            _cano = parts.Length > 0 ? parts[0].Trim() : "";
            _acntPrdtCd = parts.Length > 1 ? parts[1].Trim() : "01";

            _rest = new KisRestClient(appKey, appSecret, _env, tokenCachePath);
            _aggregator = Composer.Instance.GetExportedValueByTypeName<IDataAggregator>(
                Config.Get("data-aggregator", "QuantConnect.Lean.Engine.DataFeeds.AggregationManager"));
        }

        // ── 연결 ───────────────────────────────────────────────────────────
        public override void Connect()
        {
            if (_connected) return;
            _rest.AccessToken();  // 토큰 선발급(실패 시 즉시 예외)
            try
            {
                var approval = _rest.ApprovalKey();
                _ws = new KisWebSocketClient(approval, _env, _htsId);
                _ws.TradeReceived += OnTrade;
                _ws.FillReceived += OnFill;
                _ws.Connect();
            }
            catch (Exception e)
            {
                // 실시간 연결 실패해도 주문/잔고(REST)는 가능하므로 경고만 — 데이터피드는 빈 상태.
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "WsConnectFailed",
                    $"KIS 실시간 연결 실패(주문/잔고는 정상): {e.Message}"));
            }
            _connected = true;
            Log.Trace($"KisBrokerage.Connect(): env={_env} 계좌={_cano}-{_acntPrdtCd}");
        }

        public override void Disconnect()
        {
            _ws?.Dispose();
            _connected = false;
        }

        // ── 주문 ───────────────────────────────────────────────────────────
        public override bool PlaceOrder(Order order)
        {
            var ticker = _symbolMapper.GetBrokerageSymbol(order.Symbol);
            var buy = order.Direction == OrderDirection.Buy;
            var isMarket = order.Type == OrderType.Market
                           || order.Type == OrderType.MarketOnOpen || order.Type == OrderType.MarketOnClose;
            var price = order.Type == OrderType.Limit && order is LimitOrder lo ? lo.LimitPrice
                        : (order.Price != 0 ? order.Price : _algorithm.Securities[order.Symbol].Price);

            // ── 주문금액 한도 검사 (선택적 안전장치, 한도 0이면 무조건 통과) ──
            if (!LimitCheck(order, price, out var reject))
            {
                OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero, reject) { Status = OrderStatus.Invalid });
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "OrderBlocked", reject));
                return false;
            }

            try
            {
                var ordDvsn = isMarket ? KisConstants.OrdDvsnMarket : KisConstants.OrdDvsnLimit;
                var res = _rest.OrderCash(_cano, _acntPrdtCd, ticker, buy, (int)order.AbsoluteQuantity, price, ordDvsn);
                if (!res.Ok || string.IsNullOrEmpty(res.OrderNo))
                {
                    var msg = $"KIS 주문거부 {res.Code} {res.Message}";
                    OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero, msg) { Status = OrderStatus.Invalid });
                    OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Warning, "OrderRejected", msg));
                    return false;
                }
                // ODNO를 brokerId로 — 체결통보가 이 번호로 들어온다.
                order.BrokerId.Add(res.OrderNo);
                if (!string.IsNullOrEmpty(res.OrgNo)) order.BrokerId.Add("ORG:" + res.OrgNo);
                OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero) { Status = OrderStatus.Submitted });
                Log.Trace($"KisBrokerage.PlaceOrder(): {(buy ? "매수" : "매도")} {ticker} x{order.AbsoluteQuantity} ODNO={res.OrderNo}");
                return true;
            }
            catch (Exception e)
            {
                OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero, e.Message) { Status = OrderStatus.Invalid });
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Error, "OrderError", e.Message));
                return false;
            }
        }

        /// <summary>주문금액 한도 검사. 통과 시 true. 한도(>0) 초과 시에만 거부(0=비활성). 무장 개념 없음.</summary>
        private bool LimitCheck(Order order, decimal price, out string reason)
        {
            if (_maxOrderAmount > 0)
            {
                var amount = order.AbsoluteQuantity * (price > 0 ? price : 0m);
                if (amount > _maxOrderAmount)
                {
                    reason = $"주문금액 {amount:N0}원 > 한도 {_maxOrderAmount:N0}원 — 차단.";
                    return false;
                }
            }
            reason = null;
            return true;
        }

        public override bool UpdateOrder(Order order)
        {
            var (orgNo, orderNo) = ExtractBrokerIds(order);
            if (string.IsNullOrEmpty(orderNo)) return false;
            try
            {
                var price = order is LimitOrder lo ? lo.LimitPrice : order.Price;
                var res = _rest.ReviseCancel(_cano, _acntPrdtCd, orgNo, orderNo, false, (int)order.AbsoluteQuantity, price, false);
                if (res.Ok)
                    OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero) { Status = OrderStatus.UpdateSubmitted });
                return res.Ok;
            }
            catch (Exception e)
            {
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Error, "UpdateError", e.Message));
                return false;
            }
        }

        public override bool CancelOrder(Order order)
        {
            var (orgNo, orderNo) = ExtractBrokerIds(order);
            if (string.IsNullOrEmpty(orderNo)) return false;
            try
            {
                var res = _rest.ReviseCancel(_cano, _acntPrdtCd, orgNo, orderNo, true, (int)order.AbsoluteQuantity, 0m, true);
                if (res.Ok)
                    OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero) { Status = OrderStatus.Canceled });
                return res.Ok;
            }
            catch (Exception e)
            {
                OnMessage(new BrokerageMessageEvent(BrokerageMessageType.Error, "CancelError", e.Message));
                return false;
            }
        }

        private static (string orgNo, string orderNo) ExtractBrokerIds(Order order)
        {
            string orgNo = null, orderNo = null;
            foreach (var id in order.BrokerId)
            {
                if (id.StartsWith("ORG:", StringComparison.Ordinal)) orgNo = id.Substring(4);
                else orderNo = id;
            }
            return (orgNo, orderNo);
        }

        // ── 잔고/예수금/주문 ──────────────────────────────────────────────
        public override List<Order> GetOpenOrders()
        {
            // 라이브 시작 시 기존 미체결 동기화는 미구현(첫 cut). 새 세션은 빈 상태로 시작한다.
            return new List<Order>();
        }

        public override List<Holding> GetAccountHoldings()
        {
            var balance = _rest.InquireBalance(_cano, _acntPrdtCd);
            return balance.Holdings.Select(h => new Holding
            {
                Symbol = _symbolMapper.GetLeanSymbol(h.Ticker, SecurityType.Equity, KisConstants.KrxMarket),
                Quantity = h.Quantity,
                AveragePrice = h.AveragePrice,
                MarketPrice = h.CurrentPrice,
                MarketValue = h.EvalAmount,
                UnrealizedPnL = h.ProfitLoss,
                CurrencySymbol = "₩",
            }).ToList();
        }

        public override List<CashAmount> GetCashBalance()
        {
            var balance = _rest.InquireBalance(_cano, _acntPrdtCd);
            // D+2 예수금(결제반영)을 가용현금으로. 없으면 총예수금.
            var cash = balance.D2Deposit > 0 ? balance.D2Deposit : balance.Deposit;
            return new List<CashAmount> { new CashAmount(cash, KisConstants.KrwCurrency) };
        }

        // ── 체결통보 → OrderEvent ─────────────────────────────────────────
        private void OnFill(KisFill fill)
        {
            if (!fill.IsFilled || fill.FillQty <= 0) return;
            var orders = _algorithm.Transactions.GetOrdersByBrokerageId(fill.OrderNo);
            if (orders == null || orders.Count == 0)
            {
                Log.Trace($"KisBrokerage.OnFill(): 매칭 주문 없음 ODNO={fill.OrderNo}");
                return;
            }
            foreach (var order in orders)
            {
                var cumulative = _filledQty.AddOrUpdate(fill.OrderNo, fill.FillQty, (_, prev) => prev + fill.FillQty);
                var status = cumulative >= order.AbsoluteQuantity ? OrderStatus.Filled : OrderStatus.PartiallyFilled;
                var signed = fill.Buy ? fill.FillQty : -fill.FillQty;
                OnOrderEvent(new OrderEvent(order, DateTime.UtcNow, OrderFee.Zero)
                {
                    Status = status,
                    FillQuantity = signed,
                    FillPrice = fill.FillPrice,
                    FillPriceCurrency = KisConstants.KrwCurrency,
                });
                if (status == OrderStatus.Filled) _filledQty.TryRemove(fill.OrderNo, out _);
            }
        }

        // ── 실시간 시세 → 데이터피드 ──────────────────────────────────────
        private void OnTrade(string ticker, decimal price, decimal volume, string hhmmss)
        {
            var symbol = _symbolMapper.GetLeanSymbol(ticker, SecurityType.Equity, KisConstants.KrxMarket);
            // KRX는 Asia/Seoul. 라이브 틱 시각은 거래소 타임존으로(UTC+9 근사).
            var time = DateTime.UtcNow.AddHours(9);
            var tick = new Tick(time, symbol, "", "KRX", volume, price) { TickType = TickType.Trade };
            _aggregator.Update(tick);
        }

        // ── IDataQueueHandler ──────────────────────────────────────────────
        public IEnumerator<BaseData> Subscribe(SubscriptionDataConfig dataConfig, EventHandler newDataAvailableHandler)
        {
            if (!CanSubscribe(dataConfig.Symbol) || dataConfig.TickType == TickType.OpenInterest)
                return null;
            var enumerator = _aggregator.Add(dataConfig, newDataAvailableHandler);
            _ws?.SubscribePrice(_symbolMapper.GetBrokerageSymbol(dataConfig.Symbol));
            return enumerator;
        }

        public void Unsubscribe(SubscriptionDataConfig dataConfig)
        {
            if (!CanSubscribe(dataConfig.Symbol)) return;
            _ws?.UnsubscribePrice(_symbolMapper.GetBrokerageSymbol(dataConfig.Symbol));
            _aggregator.Remove(dataConfig);
        }

        public void SetJob(LiveNodePacket job) { /* 별도 처리 없음 — 생성자에서 모두 주입 */ }

        private static bool CanSubscribe(Symbol symbol)
        {
            return symbol != null
                && !symbol.Value.Contains("universe", StringComparison.OrdinalIgnoreCase)
                && symbol.SecurityType == SecurityType.Equity
                && symbol.ID.Market == KisConstants.KrxMarket;
        }

        public override void Dispose()
        {
            _ws?.Dispose();
            _aggregator?.Dispose();
        }
    }
}
