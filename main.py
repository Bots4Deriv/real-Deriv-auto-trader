import asyncio
import json
import statistics
import websockets
import time

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DERIV_APP_ID = "1089"
SYMBOL = "R_25"

ticks = []
price = 0
signal = "NEUTRAL"

auto_trader_running = False
api_token = ""

trade_history = []

stake_amount = 0.35
take_profit = 5
stop_loss = 10
martingale_factor = 2.1

cumulative_profit = 0

MAX_STAKE = 30

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
    global signal

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
# TICK STREAM
# -----------------------

async def tick_stream():

    global price

    url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

    async with websockets.connect(url) as ws:

        await ws.send(json.dumps({
            "ticks": SYMBOL,
            "subscribe": 1
        }))

        while True:

            msg = await ws.recv()
            data = json.loads(msg)

            if "tick" in data:

                price = float(data["tick"]["quote"])

                ticks.append(price)

                if len(ticks) > 200:
                    ticks.pop(0)

                analyze_signal()

# -----------------------
# REAL TRADE
# -----------------------

async def execute_trade(direction, stake):

    url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

    async with websockets.connect(url) as ws:

        await ws.send(json.dumps({
            "authorize": api_token
        }))

        await ws.recv()

        contract_type = "CALL" if direction == "BUY" else "PUT"

        proposal = {
            "proposal": 1,
            "amount": round(stake,2),
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": 3,
            "duration_unit": "t",
            "symbol": SYMBOL
        }

        await ws.send(json.dumps(proposal))

        proposal_response = json.loads(await ws.recv())

        if "error" in proposal_response:
            return "LOSS"

        proposal_id = proposal_response["proposal"]["id"]

        await ws.send(json.dumps({
            "buy": proposal_id,
            "price": round(stake,2)
        }))

        buy = json.loads(await ws.recv())

        if "error" in buy:
            return "LOSS"

        contract_id = buy["buy"]["contract_id"]

        while True:

            await ws.send(json.dumps({
                "proposal_open_contract": 1,
                "contract_id": contract_id
            }))

            result = json.loads(await ws.recv())

            contract = result["proposal_open_contract"]

            if contract["is_sold"]:

                profit = contract["profit"]

                if profit > 0:
                    return "WIN"
                else:
                    return "LOSS"

            await asyncio.sleep(1)

# -----------------------
# AUTO TRADER
# -----------------------

async def auto_trader():

    global auto_trader_running
    global cumulative_profit

    martingale_stake = stake_amount

    while auto_trader_running:

        if cumulative_profit >= take_profit:
            auto_trader_running = False
            break

        if cumulative_profit <= -stop_loss:
            auto_trader_running = False
            break

        if signal in ["BUY","SELL"]:

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

            result = await execute_trade(direction, martingale_stake)

            trade["result"] = result

            if result == "WIN":

                cumulative_profit += martingale_stake
                martingale_stake = stake_amount

            else:

                cumulative_profit -= martingale_stake

                martingale_stake = min(
                    round(martingale_stake * martingale_factor,2),
                    MAX_STAKE
                )

            await asyncio.sleep(5)

        else:

            await asyncio.sleep(1)

# -----------------------
# STATUS
# -----------------------

@app.get("/status")
async def status():

    return {
        "price": price,
        "signal": signal,
        "trade_history": trade_history,
        "cumulative_profit": cumulative_profit
    }

# -----------------------
# UI
# -----------------------

@app.get("/", response_class=HTMLResponse)
async def home():

    rows = ""

    for t in reversed(trade_history):

        rows += f"<tr><td>{t['timestamp']}</td><td>{t['direction']}</td><td>{t['stake']}</td><td>{t['price']}</td><td>{t['result']}</td></tr>"

    return f"""
<html>

<head>

<meta name="viewport" content="width=device-width, initial-scale=1">

<style>

body{{background:black;color:white;text-align:center;font-family:Arial}}

.circle{{width:260px;height:260px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;margin:auto;margin-top:40px;font-size:28px}}

.buy{{background:green}}
.sell{{background:red}}
.neutral{{background:gray}}

table{{width:90%;margin:auto;border-collapse:collapse;margin-top:20px}}

td,th{{border:1px solid white;padding:5px}}

input{{padding:6px;margin:5px}}

button{{padding:10px;margin:6px;font-size:16px}}

</style>

</head>

<body>

<div class="circle {signal.lower()}" id="circle">

{price:.3f}<br>{signal}

</div>

<h3>Auto Trader Controls</h3>

<form id="startForm">

Token:<br>
<input name="token" type="password"><br>

Stake:<br>
<input name="stake" value="{stake_amount}" step="0.01"><br>

Take Profit:<br>
<input name="tp" value="{take_profit}"><br>

Stop Loss:<br>
<input name="sl" value="{stop_loss}"><br>

<button type="submit">Start Auto-Trader</button>

</form>

<button onclick="stopBot()">Stop Auto-Trader</button>

<h3>Trade History</h3>

<table id="table">

<tr>
<th>Time</th>
<th>Dir</th>
<th>Stake</th>
<th>Price</th>
<th>Result</th>
</tr>

{rows}

</table>

<p>Cumulative Profit: <span id="profit">{round(cumulative_profit,2)}</span></p>

<script>

document.getElementById("startForm").onsubmit = async (e)=>{{

e.preventDefault()

const f = new FormData(e.target)

await fetch("/start",{{method:"POST",body:f}})

}}

async function stopBot(){{
await fetch("/stop",{{method:"POST"}})
}}

async function update(){{
const r = await fetch("/status")
const d = await r.json()

document.getElementById("circle").innerHTML = d.price.toFixed(3)+"<br>"+d.signal
document.getElementById("circle").className = "circle "+d.signal.toLowerCase()

let rows = "<tr><th>Time</th><th>Dir</th><th>Stake</th><th>Price</th><th>Result</th></tr>"

for(let t of d.trade_history.slice().reverse()){{

rows += `<tr>
<td>${{t.timestamp}}</td>
<td>${{t.direction}}</td>
<td>${{t.stake}}</td>
<td>${{t.price}}</td>
<td>${{t.result}}</td>
</tr>`

}}

document.getElementById("table").innerHTML = rows
document.getElementById("profit").innerText = d.cumulative_profit.toFixed(2)

}}

setInterval(update,2000)

</script>

</body>

</html>
"""

# -----------------------
# START
# -----------------------

@app.post("/start")
async def start(token: str = Form(...), stake: float = Form(...), tp: float = Form(...), sl: float = Form(...)):

    global auto_trader_running
    global api_token
    global stake_amount
    global take_profit
    global stop_loss
    global cumulative_profit

    api_token = token
    stake_amount = round(stake,2)
    take_profit = tp
    stop_loss = sl
    cumulative_profit = 0

    if not auto_trader_running:

        auto_trader_running = True

        asyncio.create_task(auto_trader())

    return {"status":"started"}

# -----------------------
# STOP
# -----------------------

@app.post("/stop")
async def stop():

    global auto_trader_running

    auto_trader_running = False

    return {"status":"stopped"}

# -----------------------
# STARTUP
# -----------------------

@app.on_event("startup")
async def startup():

    asyncio.create_task(tick_stream())
