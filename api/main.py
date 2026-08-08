import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers.admin import router as admin_router
from api.routers.auth import router as auth_router
from api.routers.leaderboard import router as leaderboard_router
from api.routers.photos import router as photos_router
from api.routers.profile import router as profile_router
from api.routers.tournaments import router as tournaments_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Catio Bot Telegram Mini App API",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# CORS configuration for Telegram Web App
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers under /api/v1 prefix
app.include_router(auth_router, prefix="/api/v1")
app.include_router(profile_router, prefix="/api/v1")
app.include_router(leaderboard_router, prefix="/api/v1")
app.include_router(tournaments_router, prefix="/api/v1")
app.include_router(photos_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health_check():
    return {"status": "ok", "service": "catio-api"}
