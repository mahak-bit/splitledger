"""Mount every router under a single versioned prefix."""

from fastapi import APIRouter

from splitledger.api import auth, expenses, groups, settlements

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(groups.router)
api_router.include_router(expenses.router)
api_router.include_router(settlements.router)
