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
trade_in_progress = False  # Lock to prevent multiple trades

api_token = ""  # Single token for all trades

trade_history = []
cumulative_profit = 0
max_trades = 0  # Set by trader (0 = unlimited)
trade_count = 0

stake_amount = 0.35

#-----------------------
# INDICATORS
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
# TICK STREAM
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
# TRADE EXECUTION (60 seconds)
#-----------------------

async def execute_trade(direction, stake, token):
    global trade_in_progress
    
    url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "authorize": token
        }))

        await ws.recv()

        contract_type = "CALL" if direction == "BUY" else "PUT"

        proposal = {
            "proposal": 1,
            "amount": round(stake, 2),
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": 60,          # Changed to 60 seconds
            "duration_unit": "s",    # Changed from "t" (ticks) to "s" (seconds)
            "symbol": SYMBOL
        }

        await ws.send(json.dumps(proposal))

        proposal_response = json.loads(await ws.recv())

        if "error" in proposal_response:
            print(f"Proposal error: {proposal_response['error']['message']}")
            trade_in_progress = False
            return None, 0

        proposal_id = proposal_response["proposal"]["id"]

        await ws.send(json.dumps({
            "buy": proposal_id,
            "price": round(stake, 2)
        }))

        buy = json.loads(await ws.recv())

        if "error" in buy:
            print(f"Buy error: {buy['error']['message']}")
            trade_in_progress = False
            return None, 0

        contract_id = buy["buy"]["contract_id"]

        # Wait for contract to close (60 seconds + buffer)
        while True:
            await ws.send(json.dumps({
                "proposal_open_contract": 1,
                "contract_id": contract_id
            }))

            result = json.loads(await ws.recv())

            if "error" in result:
                print(f"Contract error: {result['error']['message']}")
                trade_in_progress = False
                return None, 0

            contract = result["proposal_open_contract"]

            if contract["is_sold"]:
                profit = float(contract["profit"])
                exit_price = float(contract["exit_tick"]) if "exit_tick" in contract else price
                
                trade_in_progress = False
                
                if profit > 0:
                    return "WIN", profit
                else:
                    return "LOSS", profit

            await asyncio.sleep(1)

#-----------------------
# AUTO TRADER
#-----------------------

async def auto_trader():
    global auto_trader_running
    global trade_in_progress
    global trade_count
    global cumulative_profit

    while auto_trader_running:
        # Check if max trades reached
        if max_trades > 0 and trade_count >= max_trades:
            print(f"Max trades ({max_trades}) reached. Stopping trader.")
            auto_trader_running = False
            break

        # Wait for signal
        if signal not in ["BUY", "SELL"]:
            await asyncio.sleep(1)
            continue

        # Wait if trade in progress
        if trade_in_progress:
            await asyncio.sleep(1)
            continue

        # Start new trade
        trade_in_progress = True
        direction = signal
        
        print(f"Placing {direction} trade at {price:.3f}")

        result, profit = await execute_trade(direction, stake_amount, api_token)

        if result is None:
            # Error occurred, already logged
            await asyncio.sleep(2)
            continue

        trade_count += 1
        cumulative_profit += profit

        trade_history.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "direction": direction,
            "stake": stake_amount,
            "entry_price": round(price, 3),
            "result": result,
            "profit": round(profit, 2),
            "trade_number": trade_count
        })

        if len(trade_history) > 50:
            trade_history.pop(0)

        print(f"Trade #{trade_count}: {result} | Profit: ${profit:.2f} | Total: ${cumulative_profit:.2f}")

        # Wait before next trade to avoid over-trading
        await asyncio.sleep(5)

#-----------------------
# STATUS
#-----------------------

@app.get("/status")
async def status():
    return {
        "price": price,
        "signal": signal,
        "trade_history": trade_history,
        "cumulative_profit": round(cumulative_profit, 2),
        "trade_count": trade_count,
        "max_trades": max_trades,
        "auto_trader_running": auto_trader_running,
        "trade_in_progress": trade_in_progress
    }

#-----------------------
# UI
#-----------------------

@app.get("/", response_class=HTMLResponse)
async def home():
    rows = ""

    for t in reversed(trade_history):
        color = "green" if t['result'] == "WIN" else "red"
        rows += f"<tr style='color:{color}'><td>{t['trade_number']}</td><td>{t['timestamp']}</td><td>{t['direction']}</td><td>${t['stake']}</td><td>{t['entry_price']}</td><td>{t['result']}</td><td>${t['profit']}</td></tr>"

    status_color = "green" if auto_trader_running else "red"
    status_text = "RUNNING" if auto_trader_running else "STOPPED"

    return f"""
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{{background:#0a0a0a;color:#fff;text-align:center;font-family:'Segoe UI',Arial,sans-serif;margin:0;padding:20px}}
.circle{{width:200px;height:200px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;margin:20px auto;font-size:24px;font-weight:bold;box-shadow:0 0 30px rgba(0,255,0,0.3)}}
.buy{{background:linear-gradient(135deg,#00b894,#00cec9);box-shadow:0 0 30px rgba(0,184,148,0.6)}}
.sell{{background:linear-gradient(135deg,#d63031,#e17055);box-shadow:0 0 30px rgba(214,48,49,0.6)}}
.neutral{{background:linear-gradient(135deg,#636e72,#b2bec3)}}
table{{width:100%;max-width:800px;margin:20px auto;border-collapse:collapse;background:#1a1a1a;border-radius:10px;overflow:hidden}}
td,th{{border:1px solid #333;padding:12px;text-align:center}}
th{{background:#2d3436;color:#74b9ff;font-weight:600}}
tr:hover{{background:#2d3436}}
input{{padding:10px;margin:5px;border-radius:5px;border:1px solid #333;background:#1a1a1a;color:#fff;width:200px}}
button{{padding:12px 24px;margin:10px;font-size:16px;border:none;border-radius:5px;cursor:pointer;transition:all 0.3s}}
.start-btn{{background:#00b894;color:white}}
.start-btn:hover{{background:#00a885;transform:scale(1.05)}}
.stop-btn{{background:#d63031;color:white}}
.stop-btn:hover{{background:#c0392b;transform:scale(1.05)}}
.status{{display:inline-block;padding:8px 16px;border-radius:20px;font-weight:bold;background:{status_color};color:white;margin:10px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:15px;max-width:600px;margin:20px auto}}
.stat-box{{background:#1a1a1a;padding:15px;border-radius:10px;border:1px solid #333}}
.stat-label{{color:#74b9ff;font-size:12px;text-transform:uppercase}}
.stat-value{{font-size:24px;font-weight:bold;margin-top:5px}}
</style>
</head>
<body>
<h1>🚀 Deriv Trading Bot</h1>
<div class="status">{status_text}</div>

<div class="circle {signal.lower()}" id="circle">
    <div>${{price:.3f}}</div>
    <div style="font-size:16px;margin-top:10px">{signal}</div>
</div>

<div class="stats">
    <div class="stat-box">
        <div class="stat-label">Trade Count</div>
        <div class="stat-value" id="trade_count">{trade_count}</div>
    </div>
    <div class="stat-box">
        <div class="stat-label">Total Profit</div>
        <div class="stat-value" id="profit" style="color:{'#00b894' if cumulative_profit >= 0 else '#d63031'}">${{round(cumulative_profit,2)}}</div>
    </div>
    <div class="stat-box">
        <div class="stat-label">Max Trades</div>
        <div class="stat-value" id="max_trades">{max_trades if max_trades > 0 else '∞'}</div>
    </div>
</div>

<h3>Trading Controls</h3>
<form id="startForm">
    <input name="token" type="password" placeholder="API Token" required><br>
    <input name="stake" type="number" step="0.01" value="{stake_amount}" placeholder="Stake Amount"><br>
    <input name="max_trades" type="number" min="0" value="0" placeholder="Max Trades (0=unlimited)"><br>
    <button type="submit" class="start-btn">▶ Start Trading</button>
</form>

<button class="stop-btn" onclick="stopBot()">⏹ Stop Trading</button>

<h3>Trade History</h3>
<table id="table">
<tr>
    <th>#</th>
    <th>Time</th>
    <th>Dir</th>
    <th>Stake</th>
    <th>Price</th>
    <th>Result</th>
    <th>P/L</th>
</tr>{rows}
</table>

<script>
document.getElementById("startForm").onsubmit = async (e)=>{{
    e.preventDefault()
    const f = new FormData(e.target)
    await fetch("/start",{{method:"POST",body:f}})
    alert("Trading started!")
}}

async function stopBot(){{
    await fetch("/stop",{{method:"POST"}})
    alert("Trading stopped!")
}}

async function update(){{
    const r = await fetch("/status")
    const d = await r.json()
    
    document.getElementById("circle").innerHTML = `<div>${{d.price.toFixed(3)}}</div><div style="font-size:16px;margin-top:10px">${{d.signal}}</div>`
    document.getElementById("circle").className = "circle " + d.signal.toLowerCase()
    document.getElementById("trade_count").innerText = d.trade_count
    document.getElementById("profit").innerText = "$" + d.cumulative_profit.toFixed(2)
    document.getElementById("profit").style.color = d.cumulative_profit >= 0 ? '#00b894' : '#d63031'
    
    let rows = "<tr><th>#</th><th>Time</th><th>Dir</th><th>Stake</th><th>Price</th><th>Result</th><th>P/L</th></tr>"
    for(let t of d.trade_history.slice().reverse()){{
        const color = t.result === "WIN" ? "green" : "red"
        rows += `<tr style="color:${{color}}"><td>${{t.trade_number}}</td><td>${{t.timestamp}}</td><td>${{t.direction}}</td><td>$${{t.stake}}</td><td>${{t.entry_price}}</td><td>${{t.result}}</td><td>$${{t.profit}}</td></tr>`
    }}
    document.getElementById("table").innerHTML = rows
}}

setInterval(update,2000)
</script>
</body>
</html>
"""

#-----------------------
# START
#-----------------------

@app.post("/start")
async def start(token: str = Form(...), stake: float = Form(...), max_trades: int = Form(0)):
    global auto_trader_running
    global api_token
    global stake_amount
    global cumulative_profit
    global trade_count
    global trade_in_progress
    global max_trades

    api_token = token
    stake_amount = round(stake, 2)
    max_trades = max(0, max_trades)  # Ensure non-negative
    
    # Reset stats only if starting fresh (not resuming)
    if not auto_trader_running:
        cumulative_profit = 0
        trade_count = 0
        trade_in_progress = False
        trade_history.clear()
        
        auto_trader_running = True
        asyncio.create_task(auto_trader())

    return {"status": "started", "max_trades": max_trades if max_trades > 0 else "unlimited"}

#-----------------------
# STOP
#-----------------------

@app.post("/stop")
async def stop():
    global auto_trader_running
    auto_trader_running = False
    return {"status": "stopped", "final_profit": round(cumulative_profit, 2), "total_trades": trade_count}

#-----------------------
# STARTUP
#-----------------------

@app.on_event("startup")
async def startup():
    asyncio.create_task(tick_stream())
