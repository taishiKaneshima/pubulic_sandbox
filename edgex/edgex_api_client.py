import os
import time
import json
import requests
from web3 import Web3
from starkware.crypto.signature.signature import sign, private_key_to_ec_point_on_stark_curve

# 定数: K_MODULUS（公式実装の値）
K_MODULUS = int("0800000000000010ffffffffffffffffb781126dcae7b2321e66a241adc64d2f", 16)

class EdgeXAPIClient:
    def __init__(self, private_key_hex: str = None, account_id: str = None):
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
        # API のベースURLは固定
        self.base_url = "https://pro.edgex.exchange"

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

