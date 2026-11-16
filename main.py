import os
import io
import pandas as pd
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text

app = FastAPI(title="SanAnd Truck Logistics Backend")

# Configurazione CORS per permettere l'accesso da web app e smartphone
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connessione al database PostgreSQL/PostGIS su Supabase
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL) if DATABASE_URL else None

@app.get("/")
def read_root():
    return {"status": "online", "message": "SanAnd Truck Backend attivo 24/7!"}

@app.post("/api/upload-orders")
async def upload_orders(file: UploadFile = File(...)):
    """
    Legge file Excel (.xlsx) o CSV, riconosce le colonne,
    effettua la geocodifica GPS e salva gli ordini su Supabase.
    """
    if not engine:
        raise HTTPException(status_code=500, detail="Database non configurato (DATABASE_URL mancante)")
    
    contents = await file.read()
    filename = file.filename.lower()
    
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents))
        elif filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            raise HTTPException(status_code=400, detail="Formato file non supportato. Usa CSV o Excel (.xlsx).")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Errore nella lettura del file: {str(e)}")
    
    # Normalizzazione intestazioni colonne
    df.columns = [str(c).strip().lower() for c in df.columns]
    
    imported_count = 0
    errors = []
    
    with engine.begin() as conn:
        for idx, row in df.iterrows():
            tracking = str(row.get('tracking') or row.get('codice') or row.get('tracking_code') or f"TRK-{idx}")
            customer = str(row.get('cliente') or row.get('customer') or row.get('nome') or "Cliente Sconosciuto")
            address = str(row.get('indirizzo') or row.get('address') or row.get('destinazione') or "")
            phone = str(row.get('telefono') or row.get('phone') or "")
            
            if not address or address == 'nan':
                continue
                
            # Geocodifica automatica via Nominatim (OpenStreetMap)
            lat, lon = 45.4642, 9.1900 # Default coordinate (Milano)
            try:
                geo_url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(address)}&format=json&limit=1"
                headers = {'User-Agent': 'SanAndTruckLogistics/1.0'}
                response = requests.get(geo_url, headers=headers, timeout=5)
                if response.status_code == 200 and response.json():
                    data = response.json()[0]
                    lat = float(data['lat'])
                    lon = float(data['lon'])
            except Exception:
                pass
            
            # Salvataggio su database PostGIS
            try:
                sql = text("""
                    INSERT INTO orders (tracking_code, customer_name, customer_phone, delivery_address, delivery_coordinates, status)
                    VALUES (:tracking, :customer, :phone, :address, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 'PENDING')
                    ON CONFLICT (tracking_code) DO NOTHING;
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
            except Exception as ex:
                errors.append(f"Riga {idx}: {str(ex)}")
                
    return {
        "success": True, 
        "imported_orders": imported_count, 
        "errors": errors
    }

@app.get("/api/orders")
def get_orders():
    if not engine:
        return []
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id, tracking_code, customer_name, delivery_address, status FROM orders ORDER BY created_at DESC LIMIT 50"))
            orders = [dict(row._mapping) for row in result]
            return orders
    except Exception as e:
        return {"error": str(e)}
