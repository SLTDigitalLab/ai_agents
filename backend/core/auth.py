"""
Azure AD JWT token validation for FastAPI.
Fetches public keys from Azure and validates incoming bearer tokens.
"""

import os
import logging
from typing import Optional
from datetime import datetime

import requests
from fastapi import HTTPException, status
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from functools import lru_cache

logger = logging.getLogger(__name__)

# Load from .env
AZURE_TENANT_ID = os.getenv("MS_TENANT_ID")
AZURE_CLIENT_ID = os.getenv("MS_CLIENT_ID")
JWKS_URL = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/discovery/v2.0/keys"
ISSUER = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/v2.0"

# auto_error=False so a missing Authorization header does not 403/401
# before get_current_user can honour AZURE_AUTH_ENABLED=false.
security = HTTPBearer(auto_error=False)


def _auth_enabled() -> bool:
    return os.getenv("AZURE_AUTH_ENABLED", "true").strip().lower() == "true"


@lru_cache(maxsize=1)
def get_jwks():
    """Fetch Azure's public signing keys (cached for 1 hour)."""
    try:
        response = requests.get(JWKS_URL, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to validate token at this time"
        )

async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """
    Validate Azure AD bearer token and return user info.
    Used as a dependency in protected routes.
    """
    if not _auth_enabled():
        logger.info("Auth disabled - returning mock user")
        return {
            "oid": "test-user-id",
            "email": "test@example.com",
            "name": "Test User",
        }

    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = creds.credentials

    try:
        # Fetch signing keys
        jwks = get_jwks()
        
        # Decode token (without verification first to get the kid)
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        
        if not kid:
            raise JWTError("Token missing kid header")
        
        # Find the matching key
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = k
                break
        
        if not key:
            raise JWTError("Signing key not found")
        
        # Verify and decode the token
        payload = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=AZURE_CLIENT_ID,
            issuer=ISSUER,
            options={"verify_aud": True}
        )
        
        return payload
        
    except JWTError as e:
        logger.warning(f"JWT validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )