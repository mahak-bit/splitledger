"""Registration, token issuance, and the caller's own profile."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from splitledger.deps import AppSettings, CurrentUser, DbSession
from splitledger.schemas import TokenResponse, UserCreate, UserRead
from splitledger.security import create_access_token
from splitledger.services import users as user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(payload: UserCreate, session: DbSession) -> UserRead:
    user = await user_service.register(session, payload)
    return UserRead.model_validate(user)


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Exchange credentials for an access token",
)
async def issue_token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSession,
    settings: AppSettings,
) -> TokenResponse:
    """OAuth2 password flow. ``username`` is the registered email address."""
    user = await user_service.authenticate(session, form.username, form.password)
    token = create_access_token(
        user.id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        ttl_minutes=settings.access_token_ttl_minutes,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.get("/me", response_model=UserRead, summary="Who am I")
async def read_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
