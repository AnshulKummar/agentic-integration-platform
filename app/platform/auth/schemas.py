"""Request/response bodies for the auth area. Passwords/tokens are never echoed back
except the freshly issued token pair in ``TokenResponse``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.platform.principal import Role

# Generous transport limits; real policy lives in passwords.check_password_policy.
_PASSWORD = Field(min_length=1, max_length=1024)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignupRequest(_Body):
    org_name: str = Field(min_length=1, max_length=200)
    org_slug: str = Field(min_length=3, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    password: str = _PASSWORD
    display_name: str = Field(default="", max_length=200)


class LoginRequest(_Body):
    email: str = Field(min_length=1, max_length=320)
    password: str = _PASSWORD
    tenant_slug: str | None = Field(default=None, max_length=64)


class RefreshRequest(_Body):
    refresh_token: str = Field(min_length=1, max_length=512)


class LogoutRequest(_Body):
    refresh_token: str = Field(min_length=1, max_length=512)


class PasswordChangeRequest(_Body):
    current_password: str = _PASSWORD
    new_password: str = _PASSWORD


class TenantView(BaseModel):
    id: str
    slug: str
    name: str


class UserView(BaseModel):
    id: str
    email: str
    display_name: str


class SessionView(BaseModel):
    user: UserView
    tenant: TenantView
    roles: list[Role]
    password_change_required: bool


class TokenResponse(SessionView):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int


class MemberCreate(_Body):
    email: str = Field(min_length=3, max_length=320)
    roles: list[Role] = Field(min_length=1)
    #: Required and policy-checked for every request. Only used when the email has
    #: no account yet; an existing user keeps their own password (an org admin must
    #: never be able to reset the password of a user shared with another org).
    initial_password: str = _PASSWORD
    display_name: str = Field(default="", max_length=200)


class MemberUpdate(_Body):
    roles: list[Role] = Field(min_length=1)


class MemberView(BaseModel):
    user_id: str
    email: str
    display_name: str
    roles: list[Role]
    is_active: bool
