import os
import asyncio
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

from core.execution.broker_interface import DhanBroker, Order, OrderType

async def main():
    client_id = os.environ.get("DHAN_CLIENT_ID")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN")

    if not client_id or not access_token:
        print("❌ Error: DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN must be set in your .env file.")
        return

    print(f"✅ Connecting to Dhan with Client ID: {client_id}")
    broker = DhanBroker(client_id=client_id, access_token=access_token)

    # We will place a very low LIMIT order for a highly liquid stock (e.g. IDEA or YESBANK)
    # so it does NOT actually execute, but validates the API.
    print("📈 Creating dummy limit order for YESBANK at ₹1.00...")
    
    order = Order(
        asset="YESBANK",
        side="buy",
        quantity=1.0,
        order_type=OrderType.LIMIT,
        limit_price=1.00,  # Far below market price
    )

    try:
        submitted_order = await broker.submit_order(order)
        print(f"✅ Order successfully submitted to Dhan!")
        print(f"Broker Order ID: {submitted_order.broker_order_id}")
        print(f"Status: {submitted_order.status}")
        
        # Now let's fetch open orders to verify
        print(f"\n🔍 Fetching order status for {submitted_order.broker_order_id}...")
        order_status = await broker.get_order_status(submitted_order.broker_order_id)
        print(f"- {order_status.asset} | {order_status.side} | Qty: {order_status.quantity} | Status: {order_status.status.name}")
            
    except Exception as e:
        print(f"❌ Failed to submit order: {e}")

if __name__ == "__main__":
    asyncio.run(main())
