from edgex.edgex_api_client import EdgeXAPIClient

def main():
    client = EdgeXAPIClient()  # secrets/secret.json からデフォルト値がロードされる
    response = client.get_account_position_transaction_page(
        account_id=client.account_id,
        filter_type_list="SETTLE_FUNDING_FEE",
        size="20"  # 例として size を 20 に変更
    )
    print("Status Code:", response.status_code)
    try:
        print("Response:", response.json())
    except Exception as e:
        print("Response (Raw):", response.text)

if __name__ == "__main__":
    main()
