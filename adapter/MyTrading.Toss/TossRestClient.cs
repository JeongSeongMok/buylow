/*
 * 토스(Toss) REST 클라이언트 (C#, 라이브 어댑터용).
 *
 * 왜 C#에 또 있나: 파이썬 brokers/toss.py는 오케스트레이터(조회)용이고, 이건 LEAN 라이브
 * 프로세스(.NET) 안에서 주문/잔고/시세를 직접 호출하기 위한 것이다. 토큰은 OAuth2 client-credentials
 * 로 발급하고 프로세스 수명 동안 메모리 + 디스크 캐시(파이썬 클라이언트와 같은 .toss_token.json 재사용)로
 * 재발급을 피한다(Toss는 client당 유효 토큰 1개 — 재발급 시 직전 토큰 무효화되므로 공유 캐시가 중요).
 *
 * KIS와 달리: 모의투자 없음(실전 단일), 계좌는 X-Tossinvest-Account 헤더(accountSeq), 응답은 {"result":...}.
 */

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using QuantConnect.Logging;

namespace MyTrading.Toss
{
    /// <summary>토스 보유종목 1건 (getHoldings items, KR만).</summary>
    public class TossHolding
    {
        public string Ticker;
        public string Name;
        public decimal Quantity;
        public decimal AveragePrice;
        public decimal CurrentPrice;
        public decimal EvalAmount;
        public decimal ProfitLoss;
    }

    /// <summary>잔고 결과(보유 + 매수가능).</summary>
    public class TossBalance
    {
        public List<TossHolding> Holdings = new List<TossHolding>();
        public decimal BuyingPower;   // cashBuyingPower(KRW)
        public decimal TotalEval;     // marketValue.amount.krw
    }

    /// <summary>주문 생성/정정/취소 응답.</summary>
    public class TossOrderResult
    {
        public bool Ok;
        public string OrderId;
        public string Message;
        public string Code;
    }

    /// <summary>주문 상세(폴링으로 체결 추적).</summary>
    public class TossOrderStatus
    {
        public bool Found;
        public string Status;       // PENDING/PARTIAL_FILLED/FILLED/CANCELED/REJECTED/EXPIRED ...
        public bool Buy;
        public string Symbol;
        public decimal FilledQty;
        public decimal AvgPrice;
        public decimal Commission;
        public decimal Tax;

        /// <summary>더 이상 변화 없는 종료 상태인지(폴링 중단·추적 제거 판정).</summary>
        public bool IsTerminal()
        {
            switch ((Status ?? "").ToUpperInvariant())
            {
                case "FILLED":
                case "CANCELED":
                case "CANCELLED":
                case "REJECTED":
                case "EXPIRED":
                case "REPLACED":
                    return true;
                default:
                    return false;
            }
        }
    }

    /// <summary>현재가 1건(getPrices).</summary>
    public class TossPrice
    {
        public string Symbol;
        public decimal LastPrice;
    }

    /// <summary>토스 REST 오류.</summary>
    public class TossException : Exception
    {
        public TossException(string message) : base(message) { }
    }

    public class TossRestClient
    {
        private readonly string _clientId;
        private readonly string _clientSecret;
        private readonly string _baseUrl;
        private readonly string _tokenCachePath;
        private readonly HttpClient _http;
        private readonly object _tokenLock = new object();

        private string _token;
        private DateTime _tokenExpiry = DateTime.MinValue;

        private readonly object _accountLock = new object();
        private long _accountSeq = 0;
        private string _accountNo;

        // 주문 페이싱 — 같은 클라이언트의 주문 전송을 최소 간격으로 직렬화해 초당 한도(429)를 넘지 않게.
        private readonly object _orderGate = new object();
        private DateTime _lastOrderAt = DateTime.MinValue;
        private static readonly TimeSpan OrderMinInterval = TimeSpan.FromMilliseconds(250); // ≤4건/초
        private const int OrderMaxAttempts = 4;

        public TossRestClient(string clientId, string clientSecret, string tokenCachePath = null, HttpClient http = null)
        {
            if (string.IsNullOrEmpty(clientId) || string.IsNullOrEmpty(clientSecret))
                throw new TossException("토스 client_id/client_secret이 필요합니다 (대시보드 설정).");
            _clientId = clientId;
            _clientSecret = clientSecret;
            _baseUrl = TossConstants.RestUrl;
            _tokenCachePath = tokenCachePath;
            _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(15) };
        }

        // ── 토큰 ───────────────────────────────────────────────────────────
        public string AccessToken()
        {
            lock (_tokenLock)
            {
                if (_token != null && _tokenExpiry > DateTime.UtcNow.AddMinutes(1))
                    return _token;
                if (TryLoadCachedToken())
                    return _token;
                return IssueToken();
            }
        }

        private string CacheId() => _clientId.Length >= 12 ? _clientId.Substring(0, 12) : _clientId;

        private bool TryLoadCachedToken()
        {
            if (string.IsNullOrEmpty(_tokenCachePath) || !File.Exists(_tokenCachePath)) return false;
            try
            {
                var blob = JObject.Parse(File.ReadAllText(_tokenCachePath));
                var rec = blob[CacheId()] as JObject;
                if (rec == null) return false;
                var exp = rec.Value<double?>("expires_at") ?? 0;
                var expiry = DateTimeOffset.FromUnixTimeSeconds((long)exp).UtcDateTime;
                if (expiry > DateTime.UtcNow.AddMinutes(1))
                {
                    _token = rec.Value<string>("access_token");
                    _tokenExpiry = expiry;
                    return !string.IsNullOrEmpty(_token);
                }
            }
            catch (Exception e) { Log.Trace($"TossRestClient: token cache read failed: {e.Message}"); }
            return false;
        }

        private void SaveCachedToken()
        {
            if (string.IsNullOrEmpty(_tokenCachePath)) return;
            try
            {
                JObject blob;
                try { blob = File.Exists(_tokenCachePath) ? JObject.Parse(File.ReadAllText(_tokenCachePath)) : new JObject(); }
                catch { blob = new JObject(); }
                blob[CacheId()] = new JObject
                {
                    ["access_token"] = _token,
                    ["expires_at"] = new DateTimeOffset(_tokenExpiry).ToUnixTimeSeconds(),
                };
                File.WriteAllText(_tokenCachePath, blob.ToString(Formatting.None));
            }
            catch (Exception e) { Log.Trace($"TossRestClient: token cache write failed: {e.Message}"); }
        }

        private string IssueToken()
        {
            using (var req = new HttpRequestMessage(HttpMethod.Post, _baseUrl + TossConstants.PathToken))
            {
                req.Content = new FormUrlEncodedContent(new Dictionary<string, string>
                {
                    ["grant_type"] = "client_credentials",
                    ["client_id"] = _clientId,
                    ["client_secret"] = _clientSecret,
                });
                var resp = _http.Send(req);
                var text = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                if (!resp.IsSuccessStatusCode)
                    throw new TossException($"토큰 발급 실패: HTTP {(int)resp.StatusCode} {Trunc(text)}");
                var data = JObject.Parse(text);
                var token = data.Value<string>("access_token");
                if (string.IsNullOrEmpty(token))
                    throw new TossException($"토큰 응답에 access_token 없음: {Trunc(text)}");
                var expiresIn = data.Value<double?>("expires_in") ?? 86400;
                _token = token;
                _tokenExpiry = DateTime.UtcNow.AddSeconds(expiresIn - 600); // 10분 여유
                SaveCachedToken();
                return token;
            }
        }

        // ── HTTP 헬퍼 ──────────────────────────────────────────────────────
        private static string Trunc(string s) => string.IsNullOrEmpty(s) ? "" : (s.Length > 200 ? s.Substring(0, 200) : s);

        private void AddAuth(HttpRequestMessage req, bool account)
        {
            req.Headers.TryAddWithoutValidation("authorization", "Bearer " + AccessToken());
            if (account)
                req.Headers.TryAddWithoutValidation("X-Tossinvest-Account", AccountSeq().ToString(CultureInfo.InvariantCulture));
        }

        /// <summary>GET 후 BFF 봉투의 result(JToken)를 반환.</summary>
        private JToken GetResult(string path, IDictionary<string, string> query, bool account)
        {
            var sb = new StringBuilder(_baseUrl + path);
            if (query != null && query.Count > 0)
            {
                sb.Append('?');
                foreach (var kv in query)
                    sb.Append(Uri.EscapeDataString(kv.Key)).Append('=').Append(Uri.EscapeDataString(kv.Value)).Append('&');
            }
            using (var req = new HttpRequestMessage(HttpMethod.Get, sb.ToString().TrimEnd('&', '?')))
            {
                AddAuth(req, account);
                var resp = _http.Send(req);
                var text = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                if (!resp.IsSuccessStatusCode)
                    throw new TossException($"{path} HTTP {(int)resp.StatusCode} {Trunc(text)}");
                var data = JObject.Parse(text);
                return data["result"] ?? data;
            }
        }

        /// <summary>POST(JSON 본문) — (성공여부, result, 오류메시지/코드).</summary>
        private (bool ok, JToken result, string message, string code) PostJson(string path, string json, bool account)
        {
            using (var req = new HttpRequestMessage(HttpMethod.Post, _baseUrl + path))
            {
                req.Content = new StringContent(json ?? "{}", Encoding.UTF8, "application/json");
                AddAuth(req, account);
                var resp = _http.Send(req);
                var text = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                JObject data = null;
                try { data = string.IsNullOrEmpty(text) ? null : JObject.Parse(text); } catch { }
                if (resp.IsSuccessStatusCode)
                    return (true, data?["result"] ?? data, null, null);
                // 오류 본문에서 메시지/코드 추출(BFF 봉투 형태가 다양할 수 있어 방어적으로).
                var err = data?["error"] as JObject;
                var msg = err?.Value<string>("message") ?? data?.Value<string>("message") ?? Trunc(text);
                var code = err?.Value<string>("code") ?? data?.Value<string>("code") ?? ((int)resp.StatusCode).ToString();
                return (false, null, msg, code);
            }
        }

        private static decimal Dec(JToken t) =>
            decimal.TryParse((t?.ToString() ?? "").Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : 0m;

        // ── 계좌 ───────────────────────────────────────────────────────────
        public long AccountSeq()
        {
            if (_accountSeq != 0) return _accountSeq;
            lock (_accountLock)
            {
                if (_accountSeq != 0) return _accountSeq;
                var result = GetResult(TossConstants.PathAccounts, null, false) as JArray;
                if (result == null || result.Count == 0)
                    throw new TossException("토스 계좌가 없습니다(getAccounts 빈 응답).");
                JObject chosen = null;
                foreach (var a in result)
                    if (a.Value<string>("accountType") == "BROKERAGE") { chosen = (JObject)a; break; }
                chosen = chosen ?? (JObject)result[0];
                _accountSeq = chosen.Value<long>("accountSeq");
                _accountNo = chosen.Value<string>("accountNo");
                return _accountSeq;
            }
        }

        public string AccountNo() { AccountSeq(); return _accountNo ?? ""; }

        // ── 잔고/매수가능 ──────────────────────────────────────────────────
        public decimal GetBuyingPower(string currency = "KRW")
        {
            var result = GetResult(TossConstants.PathBuyingPower,
                new Dictionary<string, string> { ["currency"] = currency }, true) as JObject;
            return Dec(result?["cashBuyingPower"]);
        }

        public TossBalance GetBalance()
        {
            var result = GetResult(TossConstants.PathHoldings, null, true) as JObject;
            var balance = new TossBalance();
            foreach (var item in (result?["items"] as JArray) ?? new JArray())
            {
                if (item.Value<string>("marketCountry") != "KR") continue;  // 국내주식만
                var qty = Dec(item["quantity"]);
                if (qty <= 0) continue;
                var mv = item["marketValue"] as JObject;
                var pl = item["profitLoss"] as JObject;
                balance.Holdings.Add(new TossHolding
                {
                    Ticker = item.Value<string>("symbol"),
                    Name = item.Value<string>("name"),
                    Quantity = qty,
                    AveragePrice = Dec(item["averagePurchasePrice"]),
                    CurrentPrice = Dec(item["lastPrice"]),
                    EvalAmount = Dec(mv?["amount"]),
                    ProfitLoss = Dec(pl?["amount"]),
                });
            }
            var ovAmount = (result?["marketValue"] as JObject)?["amount"] as JObject;
            balance.TotalEval = Dec(ovAmount?["krw"]);
            balance.BuyingPower = GetBuyingPower("KRW");
            return balance;
        }

        // ── 장 운영 ─────────────────────────────────────────────────────────
        public bool IsMarketOpenDay(DateTime date)
        {
            var iso = date.ToString("yyyy-MM-dd");
            var result = GetResult(TossConstants.PathMarketCalendarKr,
                new Dictionary<string, string> { ["date"] = iso }, false) as JObject;
            var today = result?["today"] as JObject;
            if (today == null || today.Value<string>("date") != iso) return false;
            return (today["integrated"] as JObject)?["regularMarket"] != null;
        }

        // ── 현재가(시세 폴링) ────────────────────────────────────────────────
        public List<TossPrice> GetPrices(IEnumerable<string> symbols)
        {
            var joined = string.Join(",", symbols);
            var prices = new List<TossPrice>();
            if (string.IsNullOrEmpty(joined)) return prices;
            var result = GetResult(TossConstants.PathPrices,
                new Dictionary<string, string> { ["symbols"] = joined }, false) as JArray;
            foreach (var p in result ?? new JArray())
                prices.Add(new TossPrice { Symbol = p.Value<string>("symbol"), LastPrice = Dec(p["lastPrice"]) });
            return prices;
        }

        // ── 주문 ───────────────────────────────────────────────────────────
        /// <summary>주문 생성. buy=true 매수. isMarket=true 시장가(price 무시). clientOrderId는 멱등성 키.</summary>
        public TossOrderResult CreateOrder(string symbol, bool buy, int qty, decimal price, bool isMarket, string clientOrderId)
        {
            var body = new JObject
            {
                ["symbol"] = symbol,
                ["side"] = buy ? "BUY" : "SELL",
                ["orderType"] = isMarket ? "MARKET" : "LIMIT",
                ["quantity"] = qty.ToString(CultureInfo.InvariantCulture),
            };
            if (!string.IsNullOrEmpty(clientOrderId)) body["clientOrderId"] = clientOrderId;
            if (!isMarket) body["price"] = ((long)Math.Round(price)).ToString(CultureInfo.InvariantCulture);
            return SendOrder(HttpMethod.Post, TossConstants.PathOrders, body.ToString());
        }

        /// <summary>주문 정정(가격/수량). KR은 quantity 필수.</summary>
        public TossOrderResult ModifyOrder(string orderId, int qty, decimal price, bool isMarket)
        {
            var body = new JObject
            {
                ["orderType"] = isMarket ? "MARKET" : "LIMIT",
                ["quantity"] = qty.ToString(CultureInfo.InvariantCulture),
            };
            if (!isMarket) body["price"] = ((long)Math.Round(price)).ToString(CultureInfo.InvariantCulture);
            return SendOrder(HttpMethod.Post, TossConstants.PathOrderModify(orderId), body.ToString());
        }

        /// <summary>주문 취소.</summary>
        public TossOrderResult CancelOrder(string orderId)
        {
            return SendOrder(HttpMethod.Post, TossConstants.PathOrderCancel(orderId), "{}");
        }

        /// <summary>주문 전송 공통 — 페이싱 + 429/일시오류 백오프 재시도. 실패해도 예외 없이 Ok=false 반환
        /// (전송오류 1건이 라이브 전체를 RuntimeError로 종료시키지 않게 — KIS 어댑터와 동일 정책).</summary>
        private TossOrderResult SendOrder(HttpMethod method, string path, string body)
        {
            lock (_orderGate)
            {
                var since = DateTime.UtcNow - _lastOrderAt;
                if (since < OrderMinInterval)
                    System.Threading.Thread.Sleep(OrderMinInterval - since);
                try
                {
                    TossOrderResult last = null;
                    for (var attempt = 1; attempt <= OrderMaxAttempts; attempt++)
                    {
                        try
                        {
                            var (ok, result, msg, code) = PostJson(path, body, true);
                            last = new TossOrderResult
                            {
                                Ok = ok,
                                OrderId = (result as JObject)?.Value<string>("orderId"),
                                Message = msg,
                                Code = code,
                            };
                            if (ok) return last;
                            // 레이트리밋(429)만 백오프 후 재시도; 그 외 거부(잔고부족 등)는 즉시 반환.
                            if (code != "429" || attempt == OrderMaxAttempts) return last;
                        }
                        catch (Exception e)
                        {
                            Log.Trace($"TossRestClient.SendOrder: 전송 오류({attempt}/{OrderMaxAttempts}): {e.Message}");
                            last = new TossOrderResult { Ok = false, Code = "TRANSPORT", Message = e.Message };
                            if (attempt == OrderMaxAttempts) return last;
                        }
                        System.Threading.Thread.Sleep(300 * attempt); // 0.3→0.6→0.9s 백오프
                    }
                    return last ?? new TossOrderResult { Ok = false, Message = "주문 전송 실패" };
                }
                finally { _lastOrderAt = DateTime.UtcNow; }
            }
        }

        /// <summary>주문 상세 — 체결 폴링용. 미존재/오류면 Found=false.</summary>
        public TossOrderStatus GetOrder(string orderId)
        {
            try
            {
                var o = GetResult(TossConstants.PathOrder(orderId), null, true) as JObject;
                if (o == null) return new TossOrderStatus { Found = false };
                var exec = o["execution"] as JObject;
                return new TossOrderStatus
                {
                    Found = true,
                    Status = o.Value<string>("status"),
                    Buy = o.Value<string>("side") == "BUY",
                    Symbol = o.Value<string>("symbol"),
                    FilledQty = Dec(exec?["filledQuantity"]),
                    AvgPrice = Dec(exec?["averageFilledPrice"]),
                    Commission = Dec(exec?["commission"]),
                    Tax = Dec(exec?["tax"]),
                };
            }
            catch (Exception e)
            {
                Log.Trace($"TossRestClient.GetOrder({orderId}) 실패: {e.Message}");
                return new TossOrderStatus { Found = false };
            }
        }
    }
}
