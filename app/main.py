from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import engine
from app.models.models import Base
from app.routers import farms, alerts, users, satellite
import time

app = FastAPI(
    title="FarmiGrow AI API",
    description="Backend API for FarmiGrow AI — device-based farm sync + Sentinel satellite scans",
    version="2.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(farms.router)
app.include_router(alerts.router)
app.include_router(users.router)
app.include_router(satellite.router)

@app.on_event("startup")
async def startup():
    # Retry DB connection on startup
    max_retries = 5
    for i in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            print("✅ Database connected and tables created!")
            break
        except Exception as e:
            print(f"⚠️ DB connection attempt {i+1}/{max_retries} failed: {e}")
            if i < max_retries - 1:
                time.sleep(3)
            else:
                print("❌ Could not connect to database after retries")

@app.get("/")
def root():
    return {
        "app": "FarmiGrow AI API",
        "version": "2.1.0",
        "status": "running"
    }

@app.get("/health")
def health():
    return {"status": "healthy"}
