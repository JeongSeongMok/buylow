/*
 * KIS 실시간 WebSocket 클라이언트 (라이브 어댑터용).
 *
 * 두 가지를 구독한다:
 *   1) 실시간 체결가(H0STCNT0) — 종목별. LEAN 데이터피드(IDataQueueHandler)로 흘려보낸다.
 *   2) 체결통보(H0STCNI0 실전 / H0STCNI9 모의) — 내 주문의 체결을 받아 브로커리지 OrderEvent로.
 *
 * KIS 프레임 규격:
 *   - 제어/응답(구독 ack, PINGPONG)은 '{' 로 시작하는 JSON.
 *   - 실시간 데이터는 `암호화여부|TR_ID|건수|본문` 형식이며 본문 필드는 '^' 구분.
 *   - 체결통보 본문은 AES-CBC(base64) 암호문 → 구독 ack의 key/iv로 복호화.
 *
 * LEAN의 WebSocketClientWrapper(자동 재연결 포함)를 사용한다. 체결통보 구독은 HTS ID(tr_key)가
 * 필요하므로 없으면 생략하고 경고만 남긴다(주문 체결 자동확인 불가 — docs/LIVE_KIS.md 한계 참고).
 */

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using QuantConnect.Brokerages;
using QuantConnect.Logging;

namespace MyTrading.Kis
{
    /// <summary>체결통보 1건(내 주문의 체결).</summary>
    public class KisFill
    {
        public string OrderNo;     // ODER_NO
        public string Ticker;      // STCK_SHRN_ISCD
        public bool Buy;           // SELN_BYOV_CLS: 02 매수 / 01 매도
        public int FillQty;        // CNTG_QTY
        public decimal FillPrice;  // CNTG_UNPR
        public string FillTime;    // STCK_CNTG_HOUR (HHMMSS)
        public bool IsFilled;      // CNTG_YN == "2"
    }

    public class KisWebSocketClient : IDisposable
    {
        private readonly string _approvalKey;
        private readonly string _env;
        private readonly string _htsId;
        private readonly WebSocketClientWrapper _ws = new WebSocketClientWrapper();
        private readonly HashSet<string> _subscribed = new HashSet<string>();
        private readonly object _lock = new object();

        private string _aesKey;
        private string _aesIv;

        /// <summary>실시간 체결가 콜백: (ticker, price, volume, hhmmss).</summary>
        public event Action<string, decimal, decimal, string> TradeReceived;
        /// <summary>체결통보 콜백.</summary>
        public event Action<KisFill> FillReceived;

        public bool IsConnected => _ws.IsOpen;

        public KisWebSocketClient(string approvalKey, string env, string htsId)
        {
            _approvalKey = approvalKey;
            _env = env;
            _htsId = htsId;
            _ws.Initialize(KisConstants.WsUrl(env));
            _ws.Message += OnMessage;
            _ws.Open += (_, __) => OnOpen();
            _ws.Error += (_, e) => Log.Error($"KisWebSocket error: {e.Message}");
        }

        public void Connect()
        {
            if (!_ws.IsOpen) _ws.Connect();
        }

        private void OnOpen()
        {
            Log.Trace("KisWebSocket: connected");
            // 재연결 시 기존 구독 복원 + 체결통보 재구독.
            lock (_lock)
            {
                foreach (var ticker in _subscribed)
                    Send(SubscribeMessage(KisConstants.WsTrPrice, ticker, true));
            }
            SubscribeFills();
        }

        /// <summary>체결통보 구독. HTS ID 없으면 생략(자동 체결확인 불가).</summary>
        public void SubscribeFills()
        {
            if (string.IsNullOrEmpty(_htsId))
            {
                Log.Trace("KisWebSocket: HTS ID 미설정 — 체결통보 구독 생략(주문 체결 자동확인 불가).");
                return;
            }
            var tr = KisConstants.IsDemo(_env) ? KisConstants.WsTrFillDemo : KisConstants.WsTrFillReal;
            Send(SubscribeMessage(tr, _htsId, true));
        }

        public void SubscribePrice(string ticker)
        {
            lock (_lock)
            {
                if (!_subscribed.Add(ticker)) return;
            }
            if (_ws.IsOpen) Send(SubscribeMessage(KisConstants.WsTrPrice, ticker, true));
        }

        public void UnsubscribePrice(string ticker)
        {
            lock (_lock)
            {
                if (!_subscribed.Remove(ticker)) return;
            }
            if (_ws.IsOpen) Send(SubscribeMessage(KisConstants.WsTrPrice, ticker, false));
        }

        private string SubscribeMessage(string trId, string trKey, bool subscribe)
        {
            return new JObject
            {
                ["header"] = new JObject
                {
                    ["approval_key"] = _approvalKey,
                    ["custtype"] = "P",
                    ["tr_type"] = subscribe ? "1" : "2",
                    ["content-type"] = "utf-8",
                },
                ["body"] = new JObject
                {
                    ["input"] = new JObject { ["tr_id"] = trId, ["tr_key"] = trKey },
                },
            }.ToString(Newtonsoft.Json.Formatting.None);
        }

        private void Send(string msg)
        {
            try { _ws.Send(msg); }
            catch (Exception e) { Log.Error($"KisWebSocket send 실패: {e.Message}"); }
        }

        private void OnMessage(object sender, WebSocketMessage e)
        {
            var data = (e.Data as WebSocketClientWrapper.TextMessage)?.Message;
            if (string.IsNullOrEmpty(data)) return;
            try
            {
                if (data[0] == '{') { HandleControl(data); return; }
                HandleRealtime(data);
            }
            catch (Exception ex) { Log.Error($"KisWebSocket 파싱 오류: {ex.Message}"); }
        }

        // 구독 ack / PINGPONG
        private void HandleControl(string json)
        {
            var obj = JObject.Parse(json);
            var trId = obj["header"]?.Value<string>("tr_id");
            if (trId == "PINGPONG") { Send(json); return; }  // 그대로 에코
            var output = obj["body"]?["output"] as JObject;
            if (output != null)
            {
                var key = output.Value<string>("key");
                var iv = output.Value<string>("iv");
                if (!string.IsNullOrEmpty(key) && !string.IsNullOrEmpty(iv))
                {
                    _aesKey = key; _aesIv = iv;  // 체결통보 복호화용
                    Log.Trace("KisWebSocket: 체결통보 암호화 키 수신");
                }
            }
        }

        // 실시간 데이터: 암호화여부|TR_ID|건수|본문
        private void HandleRealtime(string frame)
        {
            var parts = frame.Split(new[] { '|' }, 4);
            if (parts.Length < 4) return;
            var encrypted = parts[0] == "1";
            var trId = parts[1];
            var body = parts[3];

            if (trId == KisConstants.WsTrPrice)
            {
                ParseTrade(body);
            }
            else if (trId == KisConstants.WsTrFillReal || trId == KisConstants.WsTrFillDemo)
            {
                if (encrypted) body = Decrypt(body);
                ParseFill(body);
            }
        }

        private void ParseTrade(string body)
        {
            var trade = ParseTradeBody(body);
            if (trade != null) TradeReceived?.Invoke(trade.Item1, trade.Item2, trade.Item3, trade.Item4);
        }

        private void ParseFill(string body)
        {
            var fill = ParseFillBody(body);
            if (fill != null) FillReceived?.Invoke(fill);
        }

        /// <summary>실시간 체결가(H0STCNT0) 본문 파싱 — (ticker, price, volume, hhmmss). 순수·테스트 가능.</summary>
        public static Tuple<string, decimal, decimal, string> ParseTradeBody(string body)
        {
            var f = (body ?? "").Split('^');
            if (f.Length < 13) return null;
            // 0 MKSC_SHRN_ISCD, 1 STCK_CNTG_HOUR, 2 STCK_PRPR, 12 CNTG_VOL
            if (!decimal.TryParse(f[2], NumberStyles.Any, CultureInfo.InvariantCulture, out var price)) return null;
            decimal.TryParse(f[12], NumberStyles.Any, CultureInfo.InvariantCulture, out var vol);
            return Tuple.Create(f[0], price, vol, f[1]);
        }

        /// <summary>체결통보(H0STCNI0/9) 복호화 본문 파싱 — KisFill. 순수·테스트 가능.</summary>
        public static KisFill ParseFillBody(string body)
        {
            var f = (body ?? "").Split('^');
            if (f.Length < 14) return null;
            // 2 ODER_NO, 4 SELN_BYOV_CLS, 8 STCK_SHRN_ISCD, 9 CNTG_QTY, 10 CNTG_UNPR, 11 STCK_CNTG_HOUR, 13 CNTG_YN
            decimal.TryParse(f[10], NumberStyles.Any, CultureInfo.InvariantCulture, out var fillPrice);
            int.TryParse(f[9], out var fillQty);
            return new KisFill
            {
                OrderNo = f[2],
                Buy = f[4] == "02",
                Ticker = f[8],
                FillQty = fillQty,
                FillPrice = fillPrice,
                FillTime = f[11],
                IsFilled = f[13] == "2",
            };
        }

        private string Decrypt(string cipherBase64)
        {
            if (string.IsNullOrEmpty(_aesKey) || string.IsNullOrEmpty(_aesIv))
                throw new InvalidOperationException("체결통보 복호화 키 미수신");
            using (var aes = Aes.Create())
            {
                aes.Mode = CipherMode.CBC;
                aes.Padding = PaddingMode.PKCS7;
                aes.Key = Encoding.UTF8.GetBytes(_aesKey);
                aes.IV = Encoding.UTF8.GetBytes(_aesIv);
                using (var dec = aes.CreateDecryptor())
                {
                    var cipher = Convert.FromBase64String(cipherBase64);
                    var plain = dec.TransformFinalBlock(cipher, 0, cipher.Length);
                    return Encoding.UTF8.GetString(plain);
                }
            }
        }

        public void Dispose()
        {
            try { _ws.Close(); } catch { /* ignore */ }
        }
    }
}
