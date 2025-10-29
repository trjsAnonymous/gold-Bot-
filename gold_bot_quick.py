"""
gold_bot_quick.py
- Dual mode: uses MetaTrader5 if available & initialized; otherwise runs a simulation.
- Implements client's exact strategy:
  Buy @ base_price (lot 0.01) TP = +tp_points
  If TP not hit and price drops to base_price - gap => place Sell (double lot) TP = -tp_points
  Alternate and double lot each step until any TP hits -> then close all and reset.
- Logs events to trade_log.csv
"""

import time, csv, os, math, random

# Try importing MT5
USE_MT5 = False
try:
    import MetaTrader5 as mt5
    USE_MT5 = True
except Exception:
    USE_MT5 = False

# ---------------- CONFIG ----------------
CONFIG = {
    "mode": "auto",           # "auto" (use MT5 if available) or "sim" (force simulation)
    "symbol": "XAUUSD",       # change to broker's symbol if needed
    "base_price": None,       # if None -> use current market price as base when starting
    "base_lot": 0.01,
    "gap": 3.0,               # price gap between buy and sell points
    "tp_points": 5.0,         # TP distance in price units (points)
    "max_steps": 6,           # safety cap on doubling steps
    "tick_sleep": 1.0,        # loop sleep seconds
    "logfile": "trade_log.csv",
    "mt5_terminal_path": None # optional path to terminal64.exe e.g. r"C:\Program Files\MetaTrader 5\terminal64.exe"
}
# ----------------------------------------

# ---------------- Utilities ----------------
def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(CONFIG["logfile"], "a+", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([ts, msg])

# ---------------- MT5 helper (minimal) ----------------
mt5_ok = False
def mt5_init():
    global mt5_ok
    if not USE_MT5:
        return False
    try:
        if CONFIG["mt5_terminal_path"]:
            res = mt5.initialize(CONFIG["mt5_terminal_path"])
        else:
            res = mt5.initialize()
        if not res:
            log(f"MT5 initialize failed: {mt5.last_error()}")
            mt5_ok = False
            return False
        # try login status: returns dict if connected to a terminal
        info = mt5.terminal_info()
        if info is None:
            log("MT5 terminal info not available")
            mt5_ok = False
            return False
        mt5_ok = True
        log("MT5 initialized successfully (local terminal).")
        # ensure symbol selected in MarketWatch
        if not mt5.symbol_select(CONFIG["symbol"], True):
            log(f"Warning: symbol {CONFIG['symbol']} not found or not selected in MarketWatch.")
        return True
    except Exception as e:
        log(f"MT5 init exception: {e}")
        mt5_ok = False
        return False

def mt5_get_price():
    t = mt5.symbol_info_tick(CONFIG["symbol"])
    if t is None:
        return None
    # return mid price for logic
    return (t.ask + t.bid) / 2.0

def mt5_place_market(order_type, lot, tp_price):
    # order_type: mt5.ORDER_TYPE_BUY or ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(CONFIG["symbol"])
    price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": CONFIG["symbol"],
        "volume": lot,
        "type": order_type,
        "price": price,
        "tp": tp_price,
        "sl": 0.0,
        "deviation": 20,
        "magic": 123456,
        "comment": "GoldBotAuto",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    res = mt5.order_send(req)
    return res

def mt5_close_all_positions():
    positions = mt5.positions_get(symbol=CONFIG["symbol"])
    if positions is None:
        return
    for p in positions:
        # close each position
        vol = p.volume
        if p.type == 0:  # buy -> close with sell
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": CONFIG["symbol"],
                "volume": vol,
                "type": mt5.ORDER_TYPE_SELL,
                "price": mt5.symbol_info_tick(CONFIG["symbol"]).bid,
                "deviation": 20,
                "magic": 123456,
                "comment": "CloseBuy"
            }
            mt5.order_send(req)
        else:
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": CONFIG["symbol"],
                "volume": vol,
                "type": mt5.ORDER_TYPE_BUY,
                "price": mt5.symbol_info_tick(CONFIG["symbol"]).ask,
                "deviation": 20,
                "magic": 123456,
                "comment": "CloseSell"
            }
            mt5.order_send(req)

# ---------------- Simulation helpers ----------------
def simulate_next_price(current):
    # small random walk, biased small step
    step = random.uniform(-2.0, 2.0)
    return round(current + step, 2)

# ---------------- Core Strategy Engine ----------------
class StrategyEngine:
    def __init__(self, base_price=None):
        self.base_price = base_price
        self.reset()

    def reset(self):
        self.current_step = 0
        self.current_lot = CONFIG["base_lot"]
        self.pending_side = "buy"  # start with buy
        self.active_positions = []  # list of dicts {'side','entry','lot','tp'}
        self.pending_orders = []    # simulation only representation

    def start_cycle(self, start_price):
        # start a new cycle using start_price as base
        self.reset()
        self.base_price = start_price
        self.place_initial_buy()

    def place_initial_buy(self):
        entry = self.base_price if self.base_price is not None else get_market_price()
        tp = round(entry + CONFIG["tp_points"], 2)
        self._place_trade_sim("buy", entry, self.current_lot, tp)
        # prepare pending sell stop at base_price - gap
        sell_price = round(self.base_price - CONFIG["gap"], 2)
        sell_lot = round(self.current_lot * 2, 2)
        sell_tp = round(sell_price - CONFIG["tp_points"], 2)
        self.pending_orders.append({"type":"sell_stop","price":sell_price,"lot":sell_lot,"tp":sell_tp,"step":1})
        self.current_step = 1
        self.pending_side = "sell"
        log(f"Initial BUY placed @ {entry} lot {self.current_lot} TP {tp} ; pending SELL_STOP @{sell_price} lot {sell_lot}")

    def _place_trade_sim(self, side, entry, lot, tp):
        # simulation record (in MT5 mode actual orders will be placed separately)
        self.active_positions.append({"side":side,"entry":entry,"lot":lot,"tp":tp})
        log(f"TRADE => {side.upper()} entry={entry} lot={lot} tp={tp}")

    def _place_trade_mt5(self, side, price, lot, tp):
        if not mt5_ok:
            log("MT5 not ready")
            return None
        order_type = mt5.ORDER_TYPE_BUY if side=="buy" else mt5.ORDER_TYPE_SELL
        res = mt5_place_market(order_type, lot, tp)
        log(f"MT5 order send result: {res}")
        return res

    def check_events(self, current_price, use_mt5=False):
        # 1) check TP hit for any active position
        for pos in list(self.active_positions):
            if pos["side"]=="buy" and current_price >= pos["tp"]:
                log(f"TP HIT BUY at {current_price} target {pos['tp']} -> CLOSE ALL")
                if use_mt5:
                    mt5_close_all_positions()
                self.close_all_and_reset()
                return "tp_hit"
            if pos["side"]=="sell" and current_price <= pos["tp"]:
                log(f"TP HIT SELL at {current_price} target {pos['tp']} -> CLOSE ALL")
                if use_mt5:
                    mt5_close_all_positions()
                self.close_all_and_reset()
                return "tp_hit"

        # 2) check pending fills (simulation)
        for p in list(self.pending_orders):
            if p["type"]=="sell_stop" and current_price <= p["price"]:
                # fill sell stop
                self._place_trade_sim("sell", p["price"], p["lot"], p["tp"]) if not use_mt5 else self._place_trade_mt5("sell", p["price"], p["lot"], p["tp"])
                self.pending_orders.remove(p)
                # place next buy_stop at base_price
                next_lot = round(CONFIG["base_lot"] * (2 ** (p["step"])), 2)
                next_price = round(self.base_price, 2)
                next_tp = round(next_price + CONFIG["tp_points"], 2)
                if self.current_step + 1 <= CONFIG["max_steps"]:
                    self.pending_orders.append({"type":"buy_stop","price":next_price,"lot":next_lot,"tp":next_tp,"step":p["step"]+1})
                    log(f"Placed pending BUY_STOP @{next_price} lot {next_lot} TP {next_tp}")
                self.current_step += 1
                return "filled"
            if p["type"]=="buy_stop" and current_price >= p["price"]:
                self._place_trade_sim("buy", p["price"], p["lot"], p["tp"]) if not use_mt5 else self._place_trade_mt5("buy", p["price"], p["lot"], p["tp"])
                self.pending_orders.remove(p)
                next_lot = round(CONFIG["base_lot"] * (2 ** (p["step"])), 2)
                next_price = round(self.base_price - CONFIG["gap"], 2)
                next_tp = round(next_price - CONFIG["tp_points"], 2)
                if self.current_step + 1 <= CONFIG["max_steps"]:
                    self.pending_orders.append({"type":"sell_stop","price":next_price,"lot":next_lot,"tp":next_tp,"step":p["step"]+1})
                    log(f"Placed pending SELL_STOP @{next_price} lot {next_lot} TP {next_tp}")
                self.current_step += 1
                return "filled"

        # 3) check max step
        if self.current_step > CONFIG["max_steps"]:
            log("Max steps reached -> closing all for safety")
            if use_mt5:
                mt5_close_all_positions()
            self.close_all_and_reset()
            return "stopped"

        return None

    def close_all_and_reset(self):
        # simulation: log and clear
        for pos in self.active_positions:
            log(f"Closing position {pos}")
        self.active_positions = []
        self.pending_orders = []
        self.reset()

# ---------------- Controller that runs engine ----------------
def get_market_price():
    if mt5_ok:
        p = mt5_get_price()
        return p
    else:
        # fallback dummy
        return None

def run_bot_loop(force_sim=False):
    use_mt5_mode = False
    if CONFIG["mode"]=="auto" and mt5_init():
        use_mt5_mode = True
    if force_sim:
        use_mt5_mode = False

    engine = None
    # decide starting base price
    if use_mt5_mode:
        cur = get_market_price()
        if cur is None:
            log("Could not fetch market price from MT5. Switching to simulation.")
            use_mt5_mode = False
        else:
            start_price = round(cur, 2) if CONFIG["base_price"] is None else CONFIG["base_price"]
    else:
        # simulation start price: if base_price provided use that, else pick a typical number for demo
        start_price = CONFIG["base_price"] if CONFIG["base_price"] is not None else 3300.0

    engine = StrategyEngine(start_price)
    log(f"Starting cycle with base price = {start_price} | MT5 mode = {use_mt5_mode}")

    # if using MT5 mode and we want real order placement for initial buy, do it:
    if use_mt5_mode:
        # place initial as market buy at market price with TP
        engine.active_positions = []  # keep record in sim engine too
        entry_price = get_market_price()
        if entry_price is None:
            log("Failed to read price from MT5 at start")
        else:
            tp = round(entry_price + CONFIG["tp_points"], 2)
            res = engine._place_trade_mt5("buy", entry_price, engine.current_lot, tp)
            engine.pending_orders = [{"type":"sell_stop","price":round(entry_price - CONFIG["gap"],2),
                                      "lot":round(engine.current_lot*2,2),"tp":round(entry_price - CONFIG["gap"] - CONFIG["tp_points"],2),"step":1}]
            engine.current_step = 1
            engine.pending_side = "sell"
            engine.current_lot *= 2
            log("Placed initial market buy via MT5 and pending sell_stop configured.")
    else:
        # simulation already placed initial in start_cycle
        pass

    # main loop
    sim_price = start_price
    while True:
        if use_mt5_mode:
            cur_price = get_market_price()
            if cur_price is None:
                log("Tick read failed from MT5, retrying...")
                time.sleep(CONFIG["tick_sleep"])
                continue
        else:
            # simulation random walk
            sim_price = simulate_next_price(sim_price)
            cur_price = sim_price

        log(f"Tick price = {cur_price}")

        ev = engine.check_events(cur_price, use_mt5=use_mt5_mode)
        if ev == "tp_hit":
            log("Cycle finished with TP hit. Ready for next manual start.")
            break
        elif ev == "stopped":
            log("Cycle stopped due to safety limits.")
            break
        elif ev == "filled":
            log("A pending order filled and next pending placed.")
            # continue
        # else nothing happened

        time.sleep(CONFIG["tick_sleep"])

# ---------------- Entry ----------------
if __name__ == "__main__":
    # ensure log file has header
    if not os.path.exists(CONFIG["logfile"]):
        with open(CONFIG["logfile"], "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp","event"])
    # If mt5 not installed or you want to force simulation -> pass force_sim=True
    force_sim = False
    # Quick CLI: pass "sim" argument to force simulation
    import sys
    if len(sys.argv) > 1 and sys.argv[1].lower() == "sim":
        force_sim = True

    log("=== Starting Gold Bot Quick ===")
    run_bot_loop(force_sim=force_sim)
    log("=== Bot ended ===")
