import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import appointments, auth, chat, patients

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

app = FastAPI(
    title="BAPC — Brownsville Adult & Pediatric Clinic",
    version="0.1.0",
    description="Appointment scheduling system for Brownsville Adult and Pediatric Clinic",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(appointments.router)
app.include_router(chat.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
