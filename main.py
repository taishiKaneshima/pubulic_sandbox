from edgex.edgex_api_client import EdgeXAPIClient
import asyncio
import websockets

def main():
    client = EdgeXAPIClient()  # secrets/secret.json からデフォルト値がロードされる

    # # Restful APIのテスト

    # public_API
    # ticket = client.get_ticket_summary()
    # print(ticket)

    # private_API
    # response = client.get_account_position_transaction_page(
    #     account_id=client.account_id,
    #     filter_type_list="SETTLE_FUNDING_FEE",
    #     size="20"  # 例として size を 20 に変更
    # )
    # print("Status Code:", response.status_code)
    # try:
    #     print("Response:", response.json())
    # except Exception as e:
    #     print("Response (Raw):", response.text)

    # Postメソッドのテスト
    # cancelRes = client.cancel_all_order(client.account_id)
    # print(cancelRes)
    # orderRes = client.create_order({"accountId": client.account_id})
    # print(orderRes)

    ## WebSocket接続のテスト

    # パブリックWebSocket接続
    # channels = [
    # {"type": "subscribe", "channel": "ticker.all"},  # すべてのティックデータ
    #     # 複数のチャンネルを購読可
    # ]
    # asyncio.run(client.connect_public_websocket(channels))

    # アプリ向けのWebSocket接続
    # asyncio.run(client.connect_private_websocket_app())

    # ブラウザ向けのWebSocket接続
    # asyncio.run(client.connect_private_websocket_web())


    # イベントフックの登録
    # async def handle_order_update(data):
    #     print(f"📑 注文更新処理: {data}")
    # client.register_event_callback("ORDER_UPDATE", handle_order_update)

    # メモリデータの取得・表示
    # print("\n📌 PRIVATE DATA")
    # for event, data_queue in client.memory["private"].items():
    #     print(f"🔸 {event} ({len(data_queue)}件): {list(data_queue)[:3]}...")  # 最新3件のみ表示

if __name__ == "__main__":
    main()
