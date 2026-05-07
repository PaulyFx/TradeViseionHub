import os, sqlite3, re, io, asyncio
import yfinance as yf
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from PIL import Image

# --- CONFIG ---
# BIZTONSÁGOS MEGOLDÁS: A rendszer a felhőszerver titkos változóiból olvassa ki a kulcsot!
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("⚠️ FIGYELEM: A GEMINI_API_KEY nincs beállítva a környezeti változók között!")

MODEL_NAME = 'models/gemini-3.1-flash-lite-preview'
client = genai.Client(api_key=GEMINI_API_KEY)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('trades.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS signals 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  symbol TEXT, type TEXT, entry REAL, sl REAL, tp REAL, 
                  reasoning TEXT, status TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- COMMUNITY STORAGE ---
community_messages = []

# --- PRICE MONITOR ---
def format_symbol_for_yf(symbol):
    sym = symbol.upper().replace(' ', '')
    if '/' in sym:
        base, quote = sym.split('/')
        if base in ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'DOGE', 'BNB']: return f"{base}-{quote}"
        else: return f"{base}{quote}=X"
    return sym

async def price_monitor():
    while True:
        try:
            conn = sqlite3.connect('trades.db', check_same_thread=False)
            c = conn.cursor()
            c.execute("SELECT id, symbol, type, entry, sl, tp FROM signals WHERE status = 'PENDING'")
            for trade in c.fetchall():
                trade_id, symbol, trade_type, entry, sl, tp = trade
                yf_symbol = format_symbol_for_yf(symbol)
                ticker = yf.Ticker(yf_symbol)
                hist = ticker.history(period="1d", interval="1m")
                if not hist.empty:
                    current_price = hist['Close'].iloc[-1]
                    new_status = None
                    if trade_type == 'BUY':
                        if current_price >= tp: new_status = 'WIN'
                        elif current_price <= sl: new_status = 'LOSS'
                    else:
                        if current_price <= tp: new_status = 'WIN'
                        elif current_price >= sl: new_status = 'LOSS'
                    if new_status:
                        c.execute("UPDATE signals SET status = ? WHERE id = ?", (new_status, trade_id))
            conn.commit()
            conn.close()
        except: pass
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(price_monitor())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- ENDPOINTS ---
@app.get("/stats")
def get_stats():
    conn = sqlite3.connect('trades.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT status, COUNT(*) FROM signals GROUP BY status")
    stats = {"WIN": 0, "LOSS": 0, "PENDING": 0}
    for row in c.fetchall():
        if row[0] in stats: stats[row[0]] = row[1]
    conn.close()
    wr = round((stats["WIN"]/(stats["WIN"]+stats["LOSS"])*100),1) if (stats["WIN"]+stats["LOSS"]) > 0 else 0
    return {"success": True, "stats": stats, "win_rate": wr}

@app.post("/analyze")
async def analyze(files: list[UploadFile] = File(...)):
    try:
        images = [Image.open(io.BytesIO(await f.read())) for f in files]
        prompt = "You are an Elite Institutional Analyst. Use emojis. You MUST separate Part 1 and Part 2 with '|||'. PART 1: Summary with SYMBOL, SIGNAL, ENTRY, SL, TP. PART 2: Detailed technical reasoning."
        response = client.models.generate_content(model=MODEL_NAME, contents=[prompt] + images)
        res_text = response.text
        if "|||" in res_text:
            summary, reasoning = res_text.split("|||", 1)
        else:
            summary, reasoning = res_text, "Check chart for details."
        
        # SQL mentés egyszerűsítve
        match_sym = re.search(r"SYMBOL:\s*([\w/]+)", summary)
        sym = match_sym.group(1) if match_sym else "ASSET"
        
        conn = sqlite3.connect('trades.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("INSERT INTO signals (symbol, type, entry, sl, tp, reasoning, status) VALUES (?,?,?,?,?,?,?)",
                  (sym, "SELL" if "SELL" in summary.upper() else "BUY", 0.0, 0.0, 0.0, reasoning.strip(), "PENDING"))
        db_id = c.lastrowid
        conn.commit()
        conn.close()
        
        return {"summary": summary.strip(), "reasoning": reasoning.strip(), "db_id": db_id}
    except Exception as e:
        return {"summary": f"Error: {str(e)}", "reasoning": ""}

@app.post("/chat")
async def chat(message: str = Form(None)):
    if not message: return {"reply": "No message received."}
    try:
        prompt = f"You are a professional trader. Answer in English. Question: {message}"
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return {"reply": response.text}
    except Exception as e:
        return {"reply": f"AI Error: {str(e)}"}

@app.post("/community/send")
async def send_comm(username: str = Form(...), message: str = Form(...)):
    community_messages.append({"username": username, "text": message})
    if len(community_messages) > 50: community_messages.pop(0)
    return {"success": True}

@app.get("/community/messages")
async def get_comm():
    return {"messages": community_messages}
