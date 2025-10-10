import MetaTrader5 as mt5
import time

# ---------------- CONFIGURATION ----------------
MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
SYMBOL = "XAUUSD"  # Gold symbol
FETCH_INTERVAL = 1  # seconds between price fetch

# ---------------- INITIALIZE MT5 ----------------
if not mt5.initialize(path=MT5_PATH):
    print(f"❌ MT5 Initialize failed: {mt5.last_error()}")
    quit()
else:
    print("✅ MT5 Connected Successfully!")

# ---------------- CHECK SYMBOL ----------------
if not mt5.symbol_select(SYMBOL, True):
    print(f"❌ Failed to select symbol {SYMBOL}")
    mt5.shutdown()
    quit()

print(f"📡 Fetching Live Prices for {SYMBOL}...\n")

# ---------------- LIVE PRICE LOOP ----------------
try:
    while True:
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick:
            # Display live prices
            print(f"[LIVE] Bid: {tick.bid:.2f} | Ask: {tick.ask:.2f}")
            
            # -------- Example Strategy Logic --------
            # Yeh example sirf demonstration ke liye hai
            # Tere client ki Gold strategy yahan implement hogi
            base_price = 3300
            if tick.ask >= base_price:
                print("⚡ Signal: BUY trigger detected at price", tick.ask)
            elif tick.bid <= 3297:
                print("⚡ Signal: SELL trigger detected at price", tick.bid)

        else:
            print("⚠️ Tick data not received.")
        time.sleep(FETCH_INTERVAL)

except KeyboardInterrupt:
    print("\n🛑 Bot stopped manually.")
finally:
    mt5.shutdown()
    print("🔌 MT5 shutdown completed.")
