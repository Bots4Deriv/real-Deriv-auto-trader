import asyncio
import json
import statistics
import websockets
import time
import requests

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------
# GLOBALS
# -----------------------
DERIV_APP_ID = "1089"
SYMBOL = "R_25"

ticks = []
price = 0
signal = "NEUTRAL"
momentum = 0
volatility = 0

auto_trader_running = False
api_token = ""
trade_history = []  # max 20 trades
stake_amount = 0.35
take_profit = 1.0
stop_loss = 10.0
martingale_factor = 2.1
cumulative_profit = 0.0

# -----------------------
# INDICATORS
# -----------------------
def calc_momentum():
    if len(ticks) < 10:
        return 0
    return ticks[-1] - ticks[-10]

def calc_volatility():
    if len(ticks) < 20:
        return 0
    return statistics.stdev(ticks[-20:])

def analyze_signal():
    global signal, momentum, volatility
    if len(ticks) < 20:
        signal = "NEUTRAL"
        return
    momentum = calc_momentum()
    volatility = calc_volatility()
    if abs(volatility) >= 0.4:
        signal = "BUY" if momentum > 0 else "SELL"
    else:
        signal = "NEUTRAL"

# -----------------------
# DERIV STREAM
# -----------------------
async def tick_stream():
    global price, ticks
    url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            if "tick" in data:
                p = float(data["tick"]["quote"])
                price = p
                ticks.append(p)
                if len(ticks) > 200:
                    ticks.pop(0)
                analyze_signal()

# -----------------------
# DERIV TRADE FUNCTIONS
# -----------------------
def place_real_trade(direction, stake, token):
    """Place a real Deriv contract"""
    url = "https://api.deriv.com/buy"  # placeholder
    payload = {
        "price": stake,
        "symbol": SYMBOL,
        "contract_type": direction,
        "duration": 3,
        "duration_unit": "t",
        "basis": "stake"
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        # Uncomment below when real Deriv API endpoint is used
        # response = requests.post(url, json=payload, headers=headers)
        # data = response.json()
        # For now, simulate a contract
        data = {"contract_id": int(time.time()), "status": "ok"}
        return data
    except Exception as e:
        return {"error": str(e)}

def poll_contract(contract_id, token):
    """Poll contract result (placeholder)"""
    # In real implementation, use:
    # GET /proposal_open_contract?contract_id=...
    # return result WIN/LOSS
    # Here, simulate random outcome
    import random
    return "WIN" if random.random() > 0.5 else "LOSS"

# -----------------------
# AUTO TRADER
# -----------------------
async def auto_trader():
    global auto_trader_running, trade_history, stake_amount, api_token, cumulative_profit
    martingale_stake = stake_amount
    while auto_trader_running:
        # Stop if TP or SL reached
        if cumulative_profit >= take_profit or cumulative_profit <= -stop_loss:
            auto_trader_running = False
            break
        if signal in ["BUY", "SELL"]:
            direction = signal
            trade = {
                "timestamp": time.strftime("%H:%M:%S"),
                "direction": direction,
                "stake": round(martingale_stake,2),
                "price": round(price,3),
                "result": "PENDING"
            }
            trade_history.append(trade)
            if len(trade_history) > 20:
                trade_history.pop(0)

            # Place real trade
            contract = place_real_trade(direction, martingale_stake, api_token)
            contract_id = contract.get("contract_id", None)
            await asyncio.sleep(3)  # wait 3 ticks
            trade_result = poll_contract(contract_id, api_token)
            trade["result"] = trade_result

            # Update cumulative profit
            if trade_result == "WIN":
                cumulative_profit += martingale_stake
                martingale_stake = stake_amount
            else:
                cumulative_profit -= martingale_stake
                martingale_stake *= martingale_factor

            # Wait for opposite breakout after loss
            if trade_result == "LOSS":
                while signal == direction:
                    await asyncio.sleep(0.5)
        else:
            await asyncio.sleep(0.5)

# -----------------------
# WEB INTERFACE
# -----------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    trade_rows = ""
    for t in reversed(trade_history):
        trade_rows += f"<tr><td>{t['timestamp']}</td><td>{t['direction']}</td><td>{t['stake']}</td><td>{t['price']}</td><td>{t['result']}</td></tr>"
    return f"""
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
    body{{background:black;color:white;text-align:center;font-family:Arial}}
    .circle{{width:280px;height:280px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;margin:auto;margin-top:40px;font-size:28px;}}
    .buy{{background:green;}}
    .sell{{background:red;}}
    .neutral{{background:gray;}}
    table{{width:90%;margin:auto;border-collapse:collapse;margin-top:20px}}
    td,th{{border:1px solid white;padding:5px}}
    input{{padding:5px;margin:5px}}
    button{{padding:10px;margin:5px;font-size:16px}}
    </style>
    </head>
    <body>
    <div class="circle {signal.lower()}">
        {price:.3f}<br>{signal}
    </div>

    <h3>Auto Trader Controls</h3>
    <form method="post" action="/start">
        Token: <input name="token" type="password" required><br>
        Stake: <input name="stake" type="number" step="0.01" value="{stake_amount}" required><br>
        Take Profit: <input name="tp" type="number" step="0.01" value="{take_profit}" required><br>
        Stop Loss: <input name="sl" type="number" step="0.01" value="{stop_loss}" required><br>
        <button type="submit">Start Auto-Trader</button>
    </form>

    <form method="post" action="/stop">
        <button type="submit">Stop Auto-Trader</button>
    </form>

    <h3>Trade History (last {len(trade_history)} trades)</h3>
    <table>
        <tr><th>Time</th><th>Dir</th><th>Stake</th><th>Price</th><th>Result</th></tr>
        {trade_rows}
    </table>

    <p>Cumulative Profit: {round(cumulative_profit,2)}</p>
    <script>setTimeout(()=>location.reload(),1000)</script>
    </body>
    </html>
    """

# -----------------------
# START / STOP ENDPOINTS
# -----------------------
@app.post("/start")
async def start_auto(token: str = Form(...), stake: float = Form(...), tp: float = Form(...), sl: float = Form(...)):
    global auto_trader_running, api_token, stake_amount, take_profit, stop_loss, cumulative_profit
    api_token = token
    stake_amount = stake
    take_profit = tp
    stop_loss = sl
    cumulative_profit = 0.0
    if not auto_trader_running:
        auto_trader_running = True
        asyncio.create_task(auto_trader())
    return HTMLResponse("<script>window.location='/';</script>")

@app.post("/stop")
async def stop_auto():
    global auto_trader_running
    auto_trader_running = False
    return HTMLResponse("<script>window.location='/';</script>")

# -----------------------
# START STREAM
# -----------------------
@app.on_event("startup")
async def startup():
    asyncio.create_task(tick_stream())
