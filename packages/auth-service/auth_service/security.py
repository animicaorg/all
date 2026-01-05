"""
Authentication and security utilities
"""

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from auth_service.config import settings
import secrets
import hashlib

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    """Create a JWT refresh token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token"""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key
    Returns: (full_key, prefix, hash)
    """
    # Generate random key
    random_part = secrets.token_urlsafe(32)
    full_key = f"{settings.API_KEY_PREFIX}{random_part}"
    
    # Create prefix (first 8 chars)
    prefix = full_key[:8]
    
    # Hash the full key for storage
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    
    return full_key, prefix, key_hash


def hash_api_key(api_key: str) -> str:
    """Hash an API key for verification"""
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_wallet_challenge() -> str:
    """Generate a random challenge for wallet signature authentication"""
    return secrets.token_urlsafe(32)
