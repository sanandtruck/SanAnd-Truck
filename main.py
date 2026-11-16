import os
import io
import traceback
import pandas as pd
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text

app = FastAPI(title="SanAnd Truck Logistics Backend - Debug Mode")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
try:
    engine = create_engine(DATABASE_URL) if DATABASE_URL else None
except Exception as e:
    print(f"Errore connessione DB: {e}")
    engine = None

@app.get("/")
def read_root():
    return {"status": "online", "message": "SanAnd Truck Backend attivo 24/7!"}

@app.post("/api/upload-orders")
async def upload_orders(file: UploadFile = File(...)):
    if not engine:
        raise HTTPException(status_code=500, detail="Database non configurato (DATABASE_URL mancante)")
    
    try:
        contents = await file.read()
        filename = file.filename.lower()
        
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents))
        elif filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            raise HTTPException(status_code=400, detail="Formato file non supportato. Usa CSV o Excel (.xlsx).")
        
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        imported_count = 0
        errors = []
        
        with engine.begin() as conn:
            for idx, row in df.iterrows():
                try:
                    # Controllo flessibile per InPost o HRP
                    if 'codice pacco' in df.columns or 'destinatario - indirizzo' in df.columns:
                        tracking = str(row.get('codice pacco') or f"INPOST-{idx}")
                        customer = str(row.get('mittente - denominazione') or "InPost")
                        address = str(row.get('destinazione - indirizzo') or '')
                        phone = str(row.get('destinazione - telefono') or '')
                    else:
                        tracking = str(row.get('barcode') or row.get('tracking') or f"TRK-{idx}")
                        customer = str(row.get('den_destinatario') or row.get('cliente') or "Cliente")
                        addr = str(row.get('ind_destinatario') or row.get('address line 1') or '')
                        city = str(row.get('citta_destinatario') or row.get('city') or '')
                        cap = str(row.get('cap_destinatario') or row.get('zip') or '')
                        address = ", ".join([p for p in [addr, city, cap] if p and p != 'nan'])
                        phone = str(row.get('cel_destinatario') or row.get('tel_destinatario') or '')

                    if not address or address == 'nan':
                        continue
                        
                    # Geocodifica
                    lat, lon = 45.4642, 9.1900 
                    try:
                        geo_url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(address)}&format=json&limit=1"
                        headers = {'User-Agent': 'SanAndTruckLogistics/1.0'}
                        resp = requests.get(geo_url, headers=headers, timeout=3)
                        if resp.status_code == 200 and resp.json():
                            lat = float(resp.json()[0]['lat'])
                            lon = float(resp.json()[0]['lon'])
                    except Exception:
                        pass
                    
                    sql = text("""
                        INSERT INTO orders (tracking_code, customer_name, customer_phone, delivery_address, delivery_coordinates, status)
                        VALUES (:tracking, :customer, :phone, :address, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 'PENDING')
                        ON CONFLICT (tracking_code) DO UPDATE 
                        SET customer_name = EXCLUDED.customer_name,
                            customer_phone = EXCLUDED.customer_phone,
                            delivery_address = EXCLUDED.delivery_address;
                    """)
                    conn.execute(sql, {
                        "tracking": tracking,
                        "customer": customer,
                        "phone": phone,
                        "address": address,
                        "lat": lat,
                        "lon": lon
                    })
                    imported_count += 1
                except Exception as row_ex:
                    errors.append(f"Riga {idx}: {str(row_ex)}")
                    
        return {
            "success": True, 
            "imported_orders": imported_count, 
            "errors": errors[:10] # Mostra i primi 10 errori se ce ne sono
        }
        
    except Exception as e:
        error_trace = traceback.format_exc()
        print(error_trace)
        raise HTTPException(status_code=500, detail=f"Errore critico: {str(e)}")

@app.get("/api/orders")
def get_orders():
    if not engine:
        return []
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id, tracking_code, customer_name, delivery_address, status FROM orders ORDER BY created_at DESC LIMIT 100"))
            orders = [dict(row._mapping) for row in result]
            return orders
    except Exception as e:
        return {"error": str(e)}
