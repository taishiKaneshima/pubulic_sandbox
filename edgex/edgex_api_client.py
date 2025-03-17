import os
import time
import json
import requests
import base64
import asyncio
import websockets
from web3 import Web3
from starkware.crypto.signature.signature import sign, private_key_to_ec_point_on_stark_curve
import traceback
from websockets.exceptions import InvalidStatusCode, WebSocketException
import re
from collections import deque

# 定数: K_MODULUS（公式実装の値）
K_MODULUS = int("0800000000000010ffffffffffffffffb781126dcae7b2321e66a241adc64d2f", 16)

class EdgeXAPIClient:
    def __init__(self, private_key_hex: str = None, account_id: str = None, save_memory=False, max_memory=100):
        """
        EdgeX API クライアント
        - save_memory: True なら WebSocket データを保存（quote-event, snapshot は保存しない）
        - max_memory: 各イベント・チャンネルごとの最大保存数（FIFOキュー形式）
        """
        # シークレット情報が渡されなければ secrets/secret.json から読み込む
        if private_key_hex is None or account_id is None:
            secrets_path = os.path.join(os.path.dirname(__file__), "..", "secrets", "secret.json")
            with open(secrets_path, "r") as f:
                secrets = json.load(f)
            if private_key_hex is None:
                private_key_hex = secrets["PRIVATE_KEY_HEX"]
            if account_id is None:
                account_id = secrets["ACCOUNT_ID"]
        # 0x プレフィックスがあれば除去
        self.private_key_hex = private_key_hex[2:] if private_key_hex.startswith("0x") else private_key_hex
        self.account_id = account_id

        # APIのベースURL
        self.base_url = "https://pro.edgex.exchange"
        self.ws_url = "wss://quote.edgex.exchange"

        self.ping_interval = 30  # サーバーの仕様に応じて変更可能

        self.save_memory = save_memory
        self.max_memory = max_memory

        # メモリ（チャンネル & イベントごとにデータを保存）
        self.memory = {
            "public": {
                "kline": deque(maxlen=self.max_memory),
                "depth": deque(maxlen=self.max_memory),
                "trades": deque(maxlen=self.max_memory)
            },
            "private": {
                "ACCOUNT_UPDATE": deque(maxlen=self.max_memory),
                "DEPOSIT_UPDATE": deque(maxlen=self.max_memory),
                "WITHDRAW_UPDATE": deque(maxlen=self.max_memory),
                "TRANSFER_IN_UPDATE": deque(maxlen=self.max_memory),
                "TRANSFER_OUT_UPDATE": deque(maxlen=self.max_memory),
                "ORDER_UPDATE": deque(maxlen=self.max_memory),
                "FORCE_WITHDRAW_UPDATE": deque(maxlen=self.max_memory),
                "FORCE_TRADE_UPDATE": deque(maxlen=self.max_memory),
                "FUNDING_SETTLEMENT": deque(maxlen=self.max_memory),
                "ORDER_FILL_FEE_INCOME": deque(maxlen=self.max_memory),
                "START_LIQUIDATING": deque(maxlen=self.max_memory),
                "FINISH_LIQUIDATING": deque(maxlen=self.max_memory)
            }
        }

        # ✅ コールバック関数の登録（イベント or チャンネルごと）
        self.event_callbacks = {}
        self.channel_callbacks = {}

    def register_event_callback(self, event, callback):
        """
        特定のイベントに対するコールバック関数を登録

        登録できるイベント名（Private WebSocketイベント）:
        - "ACCOUNT_UPDATE"           👤 アカウント更新
        - "DEPOSIT_UPDATE"           💰 入金更新
        - "WITHDRAW_UPDATE"          🏦 出金更新
        - "TRANSFER_IN_UPDATE"       🔄 資金移動（入金）
        - "TRANSFER_OUT_UPDATE"      🔄 資金移動（出金）
        - "ORDER_UPDATE"             📑 注文更新
        - "FORCE_WITHDRAW_UPDATE"    ⚠️ 強制出金更新
        - "FORCE_TRADE_UPDATE"       ⚠️ 強制取引更新
        - "FUNDING_SETTLEMENT"       💹 資金決済更新
        - "ORDER_FILL_FEE_INCOME"    💲 注文成立手数料収益
        - "START_LIQUIDATING"        ⚠️ 清算開始
        - "FINISH_LIQUIDATING"       ✅ 清算完了
        """
        self.event_callbacks[event] = callback

    def register_channel_callback(self, channel, callback):
        """
        特定のチャンネルに対するコールバック関数を登録

        登録できるチャンネル名（Public WebSocketチャンネル）:
        - "kline"   📈 K-Line（ローソク足データ）
        - "depth"   📊 板情報（オーダーブック）
        - "trades"  💰 最新取引データ
        - "quote"   💬 Quote（特別扱い: メモリに保存しない）
        """
        self.channel_callbacks[channel] = callback

    def generate_signature_headers(self, http_method: str, request_path: str, query_params: dict) -> dict:
        """
        指定された HTTP メソッド、リクエストパス、クエリパラメータから署名付きヘッダーを生成します。
        """
        # タイムスタンプ（ミリ秒）
        timestamp = str(int(time.time() * 1000))
        # クエリパラメータはアルファベット順に連結
        sorted_query = "&".join(f"{k}={query_params[k]}" for k in sorted(query_params))
        # 署名対象の文字列
        message = f"{timestamp}{http_method}{request_path}{sorted_query}"
        print("Message for signing:", message)

        # Keccak-256 ハッシュ計算
        msg_hash_bytes = Web3.keccak(text=message)
        msg_hash_int = int.from_bytes(msg_hash_bytes, byteorder="big")
        # 公式実装に合わせ、ハッシュ値を K_MODULUS で剰余
        msg_hash_int = msg_hash_int % K_MODULUS
        print("Reduced message hash (int):", msg_hash_int)

        # 署名生成
        private_key_int = int(self.private_key_hex, 16)
        r, s = sign(msg_hash_int, private_key_int)
        print("Signature components:")
        print(" r =", hex(r))
        print(" s =", hex(s))

        # 公開鍵の導出 (EC点： (x, y))
        public_key = private_key_to_ec_point_on_stark_curve(private_key_int)
        public_key_y = public_key[1]
        print("Public key Y coordinate:", hex(public_key_y))

        # 最終署名: r || s || publicKeyYCoordinate（各32バイト、16進64桁で連結）
        signature_hex = f"{r:064x}{s:064x}{public_key_y:064x}"
        print("Final Signature (hex):", signature_hex)
        print("Signature Length:", len(signature_hex))

        headers = {
            "X-edgeX-Api-Signature": signature_hex,
            "X-edgeX-Api-Timestamp": timestamp
        }
        return headers

    # RESTful APIでの通信
    def send_api_request(self, http_method: str, endpoint: str, query_params: dict,
                            data: dict = None, retries: int = 3, base_wait: float = 1.0,
                            max_wait: float = 5.0):
        """
        BASE_URL に対して、指定されたエンドポイント、HTTPメソッド、クエリパラメータでリクエストを送信します。
        リトライ時は線形に待機時間を増加させ、上限を設けます。
        """
        url = self.base_url + endpoint
        attempt = 0
        while attempt <= retries:
            try:
                headers = self.generate_signature_headers(http_method, endpoint, query_params)
                print(f"Attempt {attempt + 1}: Sending request to {url}")
                if http_method.upper() == "GET":
                    response = requests.get(url, headers=headers, params=query_params)
                elif http_method.upper() == "POST":
                    response = requests.post(url, headers=headers, params=query_params, json=data)
                else:
                    raise ValueError(f"Unsupported HTTP method: {http_method}")
                return response
            except Exception as e:
                attempt += 1
                if attempt > retries:
                    raise e
                wait_time = min(base_wait * attempt, max_wait)
                print(f"Request failed: {e}. Retrying in {wait_time} seconds (attempt {attempt}/{retries})...")
                time.sleep(wait_time)

    def get_account_position_transaction_page(self, account_id: str = None,
                                                filter_type_list: str = "SETTLE_FUNDING_FEE",
                                                size: str = "10"):
        """
        /api/v1/private/account/getPositionTransactionPage エンドポイントへ GET リクエストを送信します。
        各パラメータは個別に指定可能で、指定がなければデフォルト値を利用します。
        """
        endpoint = "/api/v1/private/account/getPositionTransactionPage"
        # account_id が指定されていなければインスタンスの account_id を使用
        if account_id is None:
            account_id = self.account_id
        query_params = {
            "accountId": account_id,
            "filterTypeList": filter_type_list,
            "size": size
        }
        return self.send_api_request("GET", endpoint, query_params)

    # WebSocketでの通信
    async def connect_public_websocket(self, channels=None):
        """ WebSocket (パブリック) """

        endpoint = "/api/v1/public/ws"
        websocket_url = self.ws_url + endpoint
        try:
            async with websockets.connect(websocket_url) as websocket:
                print("Connected to EdgeX Public WebSocket.")
                # ここで購読リクエストを送信
                if channels:
                    await self.subscribe_channels(websocket, channels)

                while True:
                    message = await websocket.recv()
                    print("Received:", message)
                    await self.handle_message(websocket, message)
        except Exception as e:
            print(f"Failed to connect or error during communication: {e}")

    async def subscribe_channels(self, websocket, channels):
        """
        WebSocketで指定されたチャンネルを購読（サブスクライブ）

        channels (list[dict]): 購読するチャンネルのリスト。
            各チャンネルは辞書形式で指定し、以下のキーを持つ:
            - "type": str  (固定で "subscribe")
            - "channel": str  (購読するチャンネル名)
            - "symbol": str (オプション。特定の通貨ペアや市場を指定)

        例:
            channels = [
                {"type": "subscribe", "channel": "ticker.all"},  # すべてのティックデータ
                // 複数のチャンネルを購読可
            ]
        指定できるチャンネルは以下の公式ドキュメントを参照
        https://edgex-1.gitbook.io/edgeX-documentation/api/websocket-api
        """
        for channel in channels:
            await websocket.send(json.dumps(channel))
            print(f"📡 サブスクライブ: {channel}")

    async def connect_private_websocket_web(self):
        """ WebSocket (ブラウザ向けのBase64方式) """

        endpoint = "/api/v1/private/ws"
        param = "accountId=" + self.account_id
        websocket_url = self.ws_url + endpoint + "?" + param

        headers = self.generate_signature_headers("GET", endpoint + param, {})

        # Base64エンコード
        base64_auth = base64.b64encode(json.dumps(headers).encode()).decode()
        # URLエンコードしてASCII文字列に変換
        headers_json = json.dumps(headers)
        safe_base64_auth = base64.urlsafe_b64encode(headers_json.encode()).decode().rstrip("=")

        try:
            async with websockets.connect(websocket_url, subprotocols=[safe_base64_auth]) as websocket:
                print("✅ Connected to EdgeX Private WebSocket (Browser Auth).")
                while True:
                    message = await websocket.recv()
                    print("🔹 Received:", message)
                    await self.handle_message(websocket, message)
        except Exception as e:
            error_details = traceback.format_exc()
            print("❌ エラー詳細:\n", error_details)  # 文字列として出力可能

    async def connect_private_websocket_app(self):
        """ WebSocket (App/API用) """

        endpoint = "/api/v1/private/ws"
        param = "accountId=" + self.account_id
        websocket_url = self.ws_url + endpoint + "?" + param

        headers = self.generate_signature_headers("GET", endpoint + param, {})

        try:
            async with websockets.connect(websocket_url, extra_headers=headers) as websocket:
                print("✅ Connected to EdgeX Private WebSocket.")
                while True:
                    message = await websocket.recv()
                    print("🔹 Received:", message)
                    await self.handle_message(websocket, message)

        except InvalidStatusCode as e:
            print(f"❌ HTTP Error {e.status_code}: Server rejected WebSocket connection")
            print("🔍 Response Headers:", e.headers)

        except WebSocketException as e:
            print("❌ WebSocket Error:", str(e))

        except Exception as e:
            error_details = traceback.format_exc()
            print("❌ エラー詳細:\n", error_details)

    async def handle_message(self, websocket, message):
        """ サーバーからのメッセージを処理 """
        print("🔹 Received:", message)

        try:
            msg_json = json.loads(message)
            msg_type = msg_json.get("type", "")

            if msg_type == "ping":
                # サーバーからの PING に対して PONG を返す
                await self.send_pong(websocket, msg_json.get("time", ""))
            else:
                # Ping 以外のメッセージを処理（例: 取引データ）
                await self.process_data(msg_json)

        except json.JSONDecodeError:
            print("⚠️ 受信メッセージのJSONデコードエラー")

    async def send_ping(self, websocket):
        """ 定期的に PING を送信（クライアントからのレイテンシ測定用）"""
        while True:
            await asyncio.sleep(self.ping_interval)
            ping_message = json.dumps({"type": "ping", "time": str(int(asyncio.get_event_loop().time() * 1000))})
            await websocket.send(ping_message)
            print("📤 Sent PING:", ping_message)

    async def send_pong(self, websocket, timestamp):
        """ サーバーからの PING に応答する PONG を送信 """
        pong_message = json.dumps({"type": "pong", "time": timestamp})
        await websocket.send(pong_message)
        print("📤 Sent PONG:", pong_message)

    async def process_data(self, data):
        """ WebSocket で受信したデータを処理し、登録された関数をトリガー """

        message_type = data.get("type", "")

        if message_type in ['connected', 'subscribed']:
            print(f"👤 接続処理: {data}")

        # 🔹 Private チャンネル処理
        elif message_type == "trade-event":
            event_type = data.get("content", {}).get("event", "")
            event_data = data.get("content", {}).get("data", None)

            if event_type == "Snapshot":
                print(f"📸 スナップショット（保存しない）: {data}")
                return  # スナップショットは保存しない

            print(f"📢 {event_type}: {data}")

            # ✅ コールバック関数の実行（もし登録されていれば）
            if event_type in self.event_callbacks:
                await self.event_callbacks[event_type](event_data)

            # ✅ メモリ保存（quote-event は除外）
            if self.save_memory:
                self.store_data("private", event_type, event_data)

        # 🔹 Quote チャンネル（特別扱い: メモリ保存しない）
        elif message_type == 'quote-event':
            print(f"📢 Quote: {data}")

            # ✅ コールバック関数の実行（もし登録されていれば）
            if 'quote' in self.channel_callbacks:
                await self.channel_callbacks['quote'](data)

        # 🔹 Public チャンネル（kline, depth, trades）
        elif message_type == "payload":
            channel_name = data.get("channel", "")
            event_data = data.get("content", {}).get("data", None)

            # 正規表現で分類
            category = self.classify_channel(channel_name)

            print(f"📢 {category}: {data}")

            # ✅ コールバック関数の実行（もし登録されていれば）
            if category in self.channel_callbacks:
                await self.channel_callbacks[category](event_data)

            # ✅ メモリ保存（quote-event は除外）
            if self.save_memory and category in self.memory["public"]:
                self.store_data("public", category, event_data)

        else:
            print(f"🔍 未知のメッセージタイプ: {message_type}")

    def classify_channel(self, channel_name):
        """ 正規表現を使ってチャンネルを分類 """
        if re.search(r'kline', channel_name, re.IGNORECASE):
            return "kline"
        elif re.search(r'depth', channel_name, re.IGNORECASE):
            return "depth"
        elif re.search(r'trades?', channel_name, re.IGNORECASE):  # trade または trades
            return "trades"
        else:
            return "unknown"

    def store_data(self, category, event, data):
        """ メモリにデータを保存（キュー形式で最大保存数を超えたら古いものを削除） """
        if data is not None:
            self.memory[category][event].appendleft(data)  # 新しいデータをリストの先頭（index=0）に追加
            print(f"💾 データ保存: {event} ({len(self.memory[category][event])}/{self.max_memory})")