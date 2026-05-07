import os, sqlite3, re, io
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from PIL import Image

# --- CONFIG ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    print("⚠️ FIGYELEM: Nincs beállítva a GEMINI_API_KEY!")

MODEL_NAME = 'models/gemini-3.1-flash-lite-preview'
client = genai.Client(api_key=GEMINI_API_KEY)

# --- DATABASE SETUP ---
def init_db():
    try:
        conn = sqlite3.connect('trades.db', check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS signals 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      symbol TEXT, type TEXT, entry REAL, sl REAL, tp REAL, 
                      reasoning TEXT, status TEXT)''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f">>> DB Error: {e}", flush=True)

init_db()

def extract_price(text, label):
    match = re.search(rf"{label}[:\s]*([\d,.]+)", text, re.IGNORECASE)
    if not match:
        match = re.search(rf"[\u2600-\u27BF].*?[:\s]*([\d,.]+)", text)
    if match:
        try: return float(match.group(1).replace(',', ''))
        except: return None
    return None

# --- WEB SERVER SETUP (FastAPI) ---
app = FastAPI(title="TradeVision Web API")

# Engedélyezzük a CORS-t, hogy a GitHub Pages weblapod tudjon kommunikálni ezzel a szerverrel
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Később ide beírjuk a GitHub Pages linkedet a "*" helyett!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    return {"status": "TradeVision v3.9 API ACTIVE"}

# --- A ROBOT AGYA (Az új végpont) ---
@app.post("/analyze")
async def analyze_charts(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Nem küldtél képet!")

    images = []
    for file in files:
        contents = await file.read()
        images.append(Image.open(io.BytesIO(contents)))

    print(f"📸 Új elemzés kérés érkezett ({len(images)} kép)")
    
    try:
        # MTF Konfiguráció (sokkal egyszerűbb, mint Telegramon!)
        mtf_context = ""
        if len(images) > 1:
            mtf_context = f"I have provided {len(images)} charts of the same asset. Perform a Multi-Timeframe analysis. Use HTF for trend and LTF for entries. "

        prompt = (
            f"You are an Elite Institutional Analyst. {mtf_context}Use emojis for clarity. "
            "You MUST separate Part 1 and Part 2 with '|||'.\n\n"
            "PART 1 (Output exactly in this style):\n"
            "🏷️ SYMBOL: [Asset]\n"
            "🚦 SIGNAL: [BUY/SELL/NEUTRAL]\n"
            "🎯 ENTRY: [Price]\n"
            "🛑 STOP LOSS: [Price]\n"
            "💰 TAKE PROFIT: [Price]\n"
            "⚡ CONFIDENCE: [X%]\n"
            "🧩 PATTERNS: [Specific patterns found]\n"
            "|||\n"
            "PART 2:\n"
            "[Detailed technical analysation using Mutliple Strategy.]"
        )
        
        # Hívás a Gemini-nek
        content_payload = [prompt] + images
        response = client.models.generate_content(model=MODEL_NAME, contents=content_payload)
        res_text = response.text
        
        if "|||" in res_text:
            summary, reasoning = res_text.split("|||", 1)
        else:
            summary, reasoning = res_text, "Nincs részletes elemzés."

        # Adatbázisba mentés
        entry_p = extract_price(summary, "ENTRY")
        sl_p = extract_price(summary, "STOP LOSS")
        tp_p = extract_price(summary, "TAKE PROFIT")
        sym = "ASSET"
        match_sym = re.search(r"SYMBOL:\s*([\w/]+)", summary)
        if match_sym: sym = match_sym.group(1)

        conn = sqlite3.connect('trades.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("INSERT INTO signals (symbol, type, entry, sl, tp, reasoning, status) VALUES (?,?,?,?,?,?,?)",
                  (sym, "SELL" if "SELL" in summary.upper() else "BUY", entry_p, sl_p, tp_p, reasoning.strip(), "PENDING"))
        db_id = c.lastrowid
        conn.commit()
        conn.close()

        # Visszaküldjük az adatokat a weblapnak JSON formátumban!
        return {
            "success": True,
            "db_id": db_id,
            "summary": summary.strip(),
            "reasoning": reasoning.strip()
        }

    except Exception as e:
        print(f"❌ HIBA: {e}")
        raise HTTPException(status_code=500, detail="Hiba történt az elemzés során. Próbáld újra!")

# Szerver indítása: uvicorn api:app --reload
