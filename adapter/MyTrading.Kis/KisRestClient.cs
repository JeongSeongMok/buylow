/*
 * KIS REST 클라이언트 (C#, 라이브 어댑터용).
 *
 * 왜 C#에 또 있나: 파이썬 brokers/kis.py는 ETL/오케스트레이터(데이터)용이고, 이건 LEAN 라이브
 * 프로세스(.NET) 안에서 주문/잔고/시세를 직접 호출하기 위한 것이다. 토큰은 24h 유효라 프로세스
 * 수명 동안 메모리 캐시 + 디스크 캐시(파이썬 클라이언트와 같은 .kis_token.json 재사용)로 재발급을 피한다.
 *
 * 모든 시세/금액은 KRW 정수 도메인. 실전/모의는 env로 분기(TR 접두 T/V + URL).
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

namespace MyTrading.Kis
{
    /// <summary>KIS 보유종목 1건 (잔고조회 output1 정규화).</summary>
    public class KisHolding
    {
        public string Ticker;          // pdno
        public string Name;            // prdt_name
        public decimal Quantity;       // hldg_qty
        public decimal AveragePrice;   // pchs_avg_pric
        public decimal CurrentPrice;   // prpr
        public decimal EvalAmount;     // evlu_amt
        public decimal ProfitLoss;     // evlu_pfls_amt
    }

    /// <summary>잔고조회 결과 (보유 + 예수금).</summary>
    public class KisBalance
    {
        public List<KisHolding> Holdings = new List<KisHolding>();
        public decimal Deposit;         // dnca_tot_amt (예수금총액)
        public decimal D2Deposit;       // prvs_rcdl_excc_amt (D+2 예수금)
        public decimal TotalEval;       // tot_evlu_amt
        public decimal NetAsset;        // nass_amt
    }

    /// <summary>주문 응답 (현금/정정취소).</summary>
    public class KisOrderResult
    {
        public bool Ok;
        public string OrderNo;          // ODNO
        public string OrgNo;            // KRX_FWDG_ORD_ORGNO
        public string OrderTime;        // ORD_TMD
        public string Message;          // msg1 (오류 시)
        public string Code;             // msg_cd
    }

    /// <summary>KIS REST 오류.</summary>
    public class KisException : Exception
    {
        public KisException(string message) : base(message) { }
    }

    public class KisRestClient
    {
        private readonly string _appKey;
        private readonly string _appSecret;
        private readonly string _env;        // "real" | "demo"
        private readonly string _baseUrl;
        private readonly string _tokenCachePath;
        private readonly HttpClient _http;
        private readonly object _tokenLock = new object();

        private string _token;
        private DateTime _tokenExpiry = DateTime.MinValue;

        // 잔고조회 단기 캐시 — LEAN이 init에서 GetCashBalance()+GetAccountHoldings()를 연달아 호출하며
        // inquire-balance를 2번 때려 KIS 초당 한도(rt_cd=1 "초당 거래건수 초과")를 넘기던 문제를 막는다.
        // 한 응답에 예수금+보유가 모두 들어있어 둘이 한 호출을 공유한다(TTL은 짧게 — 잔고 신선도 유지).
        private readonly object _balanceLock = new object();
        private KisBalance _balanceCache;
        private DateTime _balanceCacheAt = DateTime.MinValue;
        private static readonly TimeSpan BalanceCacheTtl = TimeSpan.FromSeconds(2);

        public string Env => _env;
        public bool IsDemo => KisConstants.IsDemo(_env);

        public KisRestClient(string appKey, string appSecret, string env, string tokenCachePath = null, HttpClient http = null)
        {
            if (string.IsNullOrEmpty(appKey) || string.IsNullOrEmpty(appSecret))
                throw new KisException("KIS app_key/app_secret이 필요합니다 (대시보드 설정).");
            _appKey = appKey;
            _appSecret = appSecret;
            _env = string.IsNullOrEmpty(env) ? "demo" : env;   // 안전 기본: 모의
            _baseUrl = KisConstants.RestUrl(_env);
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

        private string CacheId() => $"{_env}:{(_appKey.Length >= 8 ? _appKey.Substring(0, 8) : _appKey)}";

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
            catch (Exception e) { Log.Trace($"KisRestClient: token cache read failed: {e.Message}"); }
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
            catch (Exception e) { Log.Trace($"KisRestClient: token cache write failed: {e.Message}"); }
        }

        private string IssueToken()
        {
            var body = new JObject
            {
                ["grant_type"] = "client_credentials",
                ["appkey"] = _appKey,
                ["appsecret"] = _appSecret,
            };
            var resp = PostJson(KisConstants.PathToken, body.ToString(), null, null);
            var data = JObject.Parse(resp);
            var token = data.Value<string>("access_token");
            if (string.IsNullOrEmpty(token))
                throw new KisException($"토큰 응답에 access_token 없음: {resp}");
            var expiresIn = data.Value<double?>("expires_in") ?? 86400;
            _token = token;
            _tokenExpiry = DateTime.UtcNow.AddSeconds(expiresIn - 600); // 10분 여유
            SaveCachedToken();
            return token;
        }

        /// <summary>웹소켓 접속키(approval_key) 발급 — 실시간 구독에 필요.</summary>
        public string ApprovalKey()
        {
            var body = new JObject
            {
                ["grant_type"] = "client_credentials",
                ["appkey"] = _appKey,
                ["secretkey"] = _appSecret,
            };
            var resp = PostJson(KisConstants.PathApprovalKey, body.ToString(), null, null);
            var key = JObject.Parse(resp).Value<string>("approval_key");
            if (string.IsNullOrEmpty(key))
                throw new KisException($"approval_key 발급 실패: {resp}");
            return key;
        }

        // ── HTTP 헬퍼 ──────────────────────────────────────────────────────
        private Dictionary<string, string> AuthHeaders(string trId)
        {
            return new Dictionary<string, string>
            {
                ["authorization"] = "Bearer " + AccessToken(),
                ["appkey"] = _appKey,
                ["appsecret"] = _appSecret,
                ["tr_id"] = trId,
                ["custtype"] = "P",
            };
        }

        private string PostJson(string path, string json, string trId, Dictionary<string, string> extraHeaders)
        {
            using (var req = new HttpRequestMessage(HttpMethod.Post, _baseUrl + path))
            {
                req.Content = new StringContent(json, Encoding.UTF8, "application/json");
                if (!string.IsNullOrEmpty(trId))
                    foreach (var kv in AuthHeaders(trId)) req.Headers.TryAddWithoutValidation(kv.Key, kv.Value);
                if (extraHeaders != null)
                    foreach (var kv in extraHeaders) req.Headers.TryAddWithoutValidation(kv.Key, kv.Value);
                var resp = _http.Send(req);
                var text = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                if (!resp.IsSuccessStatusCode && string.IsNullOrEmpty(text))
                    throw new KisException($"{path} HTTP {(int)resp.StatusCode}");
                return text;
            }
        }

        private string Get(string path, string trId, IDictionary<string, string> query)
        {
            var sb = new StringBuilder(_baseUrl + path + "?");
            foreach (var kv in query) sb.Append(Uri.EscapeDataString(kv.Key)).Append('=').Append(Uri.EscapeDataString(kv.Value)).Append('&');
            using (var req = new HttpRequestMessage(HttpMethod.Get, sb.ToString().TrimEnd('&')))
            {
                foreach (var kv in AuthHeaders(trId)) req.Headers.TryAddWithoutValidation(kv.Key, kv.Value);
                var resp = _http.Send(req);
                var text = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                if (!resp.IsSuccessStatusCode && string.IsNullOrEmpty(text))
                    throw new KisException($"{path} HTTP {(int)resp.StatusCode}");
                return text;
            }
        }

        private static decimal Dec(JToken t) =>
            decimal.TryParse((t?.ToString() ?? "").Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out var v) ? v : 0m;

        // ── 주문 ───────────────────────────────────────────────────────────
        /// <summary>주식주문(현금). buy=true 매수. ordDvsn: 00 지정가 / 01 시장가. price는 지정가에만 사용.</summary>
        public KisOrderResult OrderCash(string cano, string acntPrdtCd, string ticker, bool buy, int qty, decimal price, string ordDvsn)
        {
            var trId = buy
                ? (IsDemo ? KisConstants.TrOrderBuyDemo : KisConstants.TrOrderBuyReal)
                : (IsDemo ? KisConstants.TrOrderSellDemo : KisConstants.TrOrderSellReal);
            var body = new JObject
            {
                ["CANO"] = cano,
                ["ACNT_PRDT_CD"] = acntPrdtCd,
                ["PDNO"] = ticker,
                ["ORD_DVSN"] = ordDvsn,
                ["ORD_QTY"] = qty.ToString(CultureInfo.InvariantCulture),
                // 시장가는 단가 0; 지정가는 정수 KRW.
                ["ORD_UNPR"] = (ordDvsn == KisConstants.OrdDvsnMarket ? 0 : (long)Math.Round(price)).ToString(CultureInfo.InvariantCulture),
                ["EXCG_ID_DVSN_CD"] = "KRX",
            };
            return ParseOrder(PostJson(KisConstants.PathOrderCash, body.ToString(), trId, null));
        }

        /// <summary>주식주문(정정취소). cancel=true 취소, false 정정. orgNo/orderNo는 원주문 식별.</summary>
        public KisOrderResult ReviseCancel(string cano, string acntPrdtCd, string orgNo, string orderNo,
                                           bool cancel, int qty, decimal price, bool all)
        {
            var trId = IsDemo ? KisConstants.TrReviseCancelDemo : KisConstants.TrReviseCancelReal;
            var body = new JObject
            {
                ["CANO"] = cano,
                ["ACNT_PRDT_CD"] = acntPrdtCd,
                ["KRX_FWDG_ORD_ORGNO"] = orgNo ?? "",
                ["ORGN_ODNO"] = orderNo,
                ["ORD_DVSN"] = KisConstants.OrdDvsnLimit,
                ["RVSE_CNCL_DVSN_CD"] = cancel ? KisConstants.CancelCode : KisConstants.RviseCode,
                ["ORD_QTY"] = qty.ToString(CultureInfo.InvariantCulture),
                ["ORD_UNPR"] = ((long)Math.Round(price)).ToString(CultureInfo.InvariantCulture),
                ["QTY_ALL_ORD_YN"] = all ? "Y" : "N",
            };
            return ParseOrder(PostJson(KisConstants.PathOrderRvseCncl, body.ToString(), trId, null));
        }

        private static KisOrderResult ParseOrder(string resp)
        {
            var data = JObject.Parse(resp);
            var rtCd = data.Value<string>("rt_cd");
            var result = new KisOrderResult
            {
                Ok = rtCd == "0",
                Message = data.Value<string>("msg1"),
                Code = data.Value<string>("msg_cd"),
            };
            var output = data["output"] as JObject;
            if (output != null)
            {
                result.OrderNo = output.Value<string>("ODNO");
                result.OrgNo = output.Value<string>("KRX_FWDG_ORD_ORGNO");
                result.OrderTime = output.Value<string>("ORD_TMD");
            }
            return result;
        }

        // ── 잔고/예수금 ────────────────────────────────────────────────────
        /// <summary>잔고조회(예수금+보유). 2초 캐시 + 초당한도(rt_cd=1) 시 백오프 재시도.
        /// lock으로 동시 호출을 직렬화해 같은 클라이언트가 inquire-balance를 중복 발사하지 않게 한다.</summary>
        public KisBalance InquireBalance(string cano, string acntPrdtCd)
        {
            lock (_balanceLock)
            {
                if (_balanceCache != null && DateTime.UtcNow - _balanceCacheAt < BalanceCacheTtl)
                    return _balanceCache;
                _balanceCache = FetchBalanceWithRetry(cano, acntPrdtCd);
                _balanceCacheAt = DateTime.UtcNow;
                return _balanceCache;
            }
        }

        private static bool IsRateLimit(string msg) =>
            !string.IsNullOrEmpty(msg) && (msg.Contains("초당") || msg.Contains("거래건수"));

        private KisBalance FetchBalanceWithRetry(string cano, string acntPrdtCd)
        {
            const int maxAttempts = 3;
            for (var attempt = 1; ; attempt++)
            {
                try { return FetchBalance(cano, acntPrdtCd); }
                catch (KisException e) when (attempt < maxAttempts && IsRateLimit(e.Message))
                {
                    Log.Trace($"KisRestClient.InquireBalance: 초당 한도 — {300 * attempt}ms 후 재시도({attempt}/{maxAttempts - 1})");
                    System.Threading.Thread.Sleep(300 * attempt);  // 0.3s → 0.6s 백오프
                }
            }
        }

        private KisBalance FetchBalance(string cano, string acntPrdtCd)
        {
            var trId = IsDemo ? KisConstants.TrBalanceDemo : KisConstants.TrBalanceReal;
            var balance = new KisBalance();
            // 페이지네이션(연속조회) — 보유종목이 많으면 CTX 키로 이어 받는다.
            string fk = "", nk = "";
            for (var page = 0; page < 20; page++)
            {
                var query = new Dictionary<string, string>
                {
                    ["CANO"] = cano,
                    ["ACNT_PRDT_CD"] = acntPrdtCd,
                    ["AFHR_FLPR_YN"] = "N",
                    ["OFL_YN"] = "",
                    ["INQR_DVSN"] = "02",
                    ["UNPR_DVSN"] = "01",
                    ["FUND_STTL_ICLD_YN"] = "N",
                    ["FNCG_AMT_AUTO_RDPT_YN"] = "N",
                    ["PRCS_DVSN"] = "00",
                    ["CTX_AREA_FK100"] = fk,
                    ["CTX_AREA_NK100"] = nk,
                };
                var data = JObject.Parse(Get(KisConstants.PathInquireBalance, trId, query));
                if (data.Value<string>("rt_cd") != "0")
                    throw new KisException($"inquire-balance rt_cd={data.Value<string>("rt_cd")} {data.Value<string>("msg1")}");
                foreach (var row in (data["output1"] as JArray) ?? new JArray())
                {
                    var qty = Dec(row["hldg_qty"]);
                    if (qty <= 0) continue;  // 0주(과거 보유) 스킵
                    balance.Holdings.Add(new KisHolding
                    {
                        Ticker = row.Value<string>("pdno"),
                        Name = row.Value<string>("prdt_name"),
                        Quantity = qty,
                        AveragePrice = Dec(row["pchs_avg_pric"]),
                        CurrentPrice = Dec(row["prpr"]),
                        EvalAmount = Dec(row["evlu_amt"]),
                        ProfitLoss = Dec(row["evlu_pfls_amt"]),
                    });
                }
                var out2 = (data["output2"] as JArray);
                if (out2 != null && out2.Count > 0)
                {
                    var o2 = out2[0];
                    balance.Deposit = Dec(o2["dnca_tot_amt"]);
                    balance.D2Deposit = Dec(o2["prvs_rcdl_excc_amt"]);
                    balance.TotalEval = Dec(o2["tot_evlu_amt"]);
                    balance.NetAsset = Dec(o2["nass_amt"]);
                }
                // 연속조회: tr_cont가 'F'/'M'이면 다음 페이지. 헤더 접근이 번거로워 CTX 키 유무로 판단.
                nk = data.Value<string>("ctx_area_nk100")?.Trim() ?? "";
                fk = data.Value<string>("ctx_area_fk100")?.Trim() ?? "";
                if (string.IsNullOrEmpty(nk)) break;
            }
            return balance;
        }

        /// <summary>매수가능 최대 수량 (현금 기준).</summary>
        public int InquirePsblQty(string cano, string acntPrdtCd, string ticker, decimal price)
        {
            var trId = IsDemo ? KisConstants.TrPsblOrderDemo : KisConstants.TrPsblOrderReal;
            var query = new Dictionary<string, string>
            {
                ["CANO"] = cano,
                ["ACNT_PRDT_CD"] = acntPrdtCd,
                ["PDNO"] = ticker,
                ["ORD_UNPR"] = ((long)Math.Round(price)).ToString(CultureInfo.InvariantCulture),
                ["ORD_DVSN"] = KisConstants.OrdDvsnLimit,
                ["CMA_EVLU_AMT_ICLD_YN"] = "N",
                ["OVRS_ICLD_YN"] = "N",
            };
            var data = JObject.Parse(Get(KisConstants.PathInquirePsblOrder, trId, query));
            var output = data["output"] as JObject;
            return output == null ? 0 : (int)Dec(output["max_buy_qty"]);
        }

        /// <summary>국내휴장일조회 — 해당일 개장(거래)일 여부. (일 1회 캐시 권장)</summary>
        public bool IsMarketOpenDay(DateTime date)
        {
            var query = new Dictionary<string, string>
            {
                ["BASS_DT"] = date.ToString("yyyyMMdd"),
                ["CTX_AREA_NK"] = "",
                ["CTX_AREA_FK"] = "",
            };
            var data = JObject.Parse(Get(KisConstants.PathChkHoliday, KisConstants.TrChkHoliday, query));
            foreach (var row in (data["output"] as JArray) ?? new JArray())
            {
                if (row.Value<string>("bass_dt") == date.ToString("yyyyMMdd"))
                    return row.Value<string>("opnd_yn") == "Y";
            }
            return false;
        }
    }
}
