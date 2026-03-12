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
allow_origins=[""],
allow_methods=[""],
allow_headers=["*"],
)

DERIV_APP_ID = "1089"
SYMBOL = "R_25"

ticks = []
price = 0
signal = "NEUTRAL"

auto_trader_running = False

demo_token = ""
real_token = ""

demo_losses = 0
use_real_trade = False

trade_history = []

stake_amount = 0.35
martingale_factor = 2.1

cumulative_profit = 0
MAX_STAKE = 30

#-----------------------

INDICATORS

#-----------------------

def calc_momentum():
if len(ticks) < 10:
return 0
return ticks[-1] - ticks[-10]

def calc_volatility():
if len(ticks) < 20:
return 0
return statistics.stdev(ticks[-20:])

def calc_micro_trend():

if len(ticks) < 6:
    return "FLAT"

avg_short = sum(ticks[-3:]) / 3
avg_long = sum(ticks[-6:]) / 6

if avg_short > avg_long:
    return "UP"
elif avg_short < avg_long:
    return "DOWN"
else:
    return "FLAT"

def analyze_signal():

global signal

if len(ticks) < 20:
    signal = "NEUTRAL"
    return

momentum = calc_momentum()
volatility = calc_volatility()
trend = calc_micro_trend()

# volatility filter
if volatility < 0.25:
    signal = "NEUTRAL"
    return

# BUY signal
if momentum > 0 and trend == "UP":
    signal = "BUY"

# SELL signal
elif momentum < 0 and trend == "DOWN":
    signal = "SELL"

else:
    signal = "NEUTRAL"

#-----------------------

TICK STREAM

#-----------------------

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

#-----------------------

TRADE EXECUTION

#-----------------------

async def execute_trade(direction, stake, token):

url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

async with websockets.connect(url) as ws:

    await ws.send(json.dumps({
        "authorize": token
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

#-----------------------

AUTO TRADER

#-----------------------

async def auto_trader():

global auto_trader_running
global cumulative_profit
global demo_losses
global use_real_trade

martingale_stake = stake_amount

while auto_trader_running:

    if signal not in ["BUY","SELL"]:
        await asyncio.sleep(1)
        continue

    direction = signal

    # DEMO TRADING
    if not use_real_trade:

        result = await execute_trade(direction, stake_amount, demo_token)

        trade_history.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "account": "DEMO",
            "direction": direction,
            "stake": stake_amount,
            "price": round(price,3),
            "result": result
        })

        if len(trade_history) > 20:
            trade_history.pop(0)

        if result == "LOSS":
            demo_losses += 1
        else:
            demo_losses = 0

        if demo_losses >= 3:
            use_real_trade = True
            demo_losses = 0

    # REAL TRADING
    else:

        result = await execute_trade(direction, martingale_stake, real_token)

        trade_history.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "account": "REAL",
            "direction": direction,
            "stake": martingale_stake,
            "price": round(price,3),
            "result": result
        })

        if len(trade_history) > 20:
            trade_history.pop(0)

        if result == "WIN":

            cumulative_profit += martingale_stake
            martingale_stake = stake_amount

        else:

            cumulative_profit -= martingale_stake

            martingale_stake = min(
                round(martingale_stake * martingale_factor,2),
                MAX_STAKE
            )

        use_real_trade = False

    await asyncio.sleep(5)

#-----------------------

STATUS

#-----------------------

@app.get("/status")
async def status():

return {
    "price": price,
    "signal": signal,
    "trade_history": trade_history,
    "cumulative_profit": cumulative_profit
}

#-----------------------

UI

#-----------------------

@app.get("/", response_class=HTMLResponse)
async def home():

rows = ""

for t in reversed(trade_history):

    rows += f"<tr><td>{t['timestamp']}</td><td>{t['account']}</td><td>{t['direction']}</td><td>{t['stake']}</td><td>{t['price']}</td><td>{t['result']}</td></tr>"

return f"""

<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>

body{{background:black;color:white;text-align:center;font-family:Arial}}

.circle{{width:260px;height:260px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;margin:auto;margin-top:40px;font-size:28px}}

.buy{{background:green}}
.sell{{background:red}}
.neutral{{background:gray}}

table{{width:95%;margin:auto;border-collapse:collapse;margin-top:20px}}

td,th{{border:1px solid white;padding:5px}}

input{{padding:6px;margin:5px}}

button{{padding:10px;margin:6px;font-size:16px}}

</style></head><body><div class="circle {signal.lower()}" id="circle">{price:.3f}<br>{signal}

</div><h3>Auto Trader Controls</h3><form id="startForm">Demo Token:<br>
<input name="demo" type="password"><br>

Real Token:<br>
<input name="real" type="password"><br>

Stake:<br>
<input name="stake" value="{stake_amount}" step="0.01"><br>

<button type="submit">Start Auto-Trader</button>

</form><button onclick="stopBot()">Stop Auto-Trader</button>

<h3>Trade History</h3><table id="table"><tr>
<th>Time</th>
<th>Account</th>
<th>Dir</th>
<th>Stake</th>
<th>Price</th>
<th>Result</th>
</tr>{rows}

</table><p>Cumulative Profit: <span id="profit">{round(cumulative_profit,2)}</span></p><script>

document.getElementById("startForm").onsubmit = async (e)=>{

e.preventDefault()

const f = new FormData(e.target)

await fetch("/start",{method:"POST",body:f})

}

async function stopBot(){
await fetch("/stop",{method:"POST"})
}

async function update(){
const r = await fetch("/status")
const d = await r.json()

document.getElementById("circle").innerHTML = d.price.toFixed(3)+"<br>"+d.signal
document.getElementById("circle").className = "circle "+d.signal.toLowerCase()

let rows = "<tr><th>Time</th><th>Account</th><th>Dir</th><th>Stake</th><th>Price</th><th>Result</th></tr>"

for(let t of d.trade_history.slice().reverse()){

rows += `<tr>
<td>${t.timestamp}</td>
<td>${t.account}</td>
<td>${t.direction}</td>
<td>${t.stake}</td>
<td>${t.price}</td>
<td>${t.result}</td>
</tr>`

}

document.getElementById("table").innerHTML = rows
document.getElementById("profit").innerText = d.cumulative_profit.toFixed(2)

}

setInterval(update,2000)

</script></body></html>
#"""-----------------------

START

#-----------------------

@app.post("/start")
async def start(demo: str = Form(...), real: str = Form(...), stake: float = Form(...)):

global auto_trader_running
global demo_token
global real_token
global stake_amount
global cumulative_profit

demo_token = demo
real_token = real
stake_amount = round(stake,2)
cumulative_profit = 0

if not auto_trader_running:

    auto_trader_running = True
    asyncio.create_task(auto_trader())

return {"status":"started"}

#-----------------------

STOP

#-----------------------

@app.post("/stop")
async def stop():

global auto_trader_running

auto_trader_running = False

return {"status":"stopped"}

#-----------------------

STARTUP

#-----------------------

@app.on_event("startup")
async def startup():

asyncio.create_task(tick_stream())
