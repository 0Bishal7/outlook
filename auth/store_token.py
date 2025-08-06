# app/auth/store_token.py

from models.db import SessionLocal, TokenStore
from utils.encryption import encrypt
from datetime import datetime

def save_tokens(user_id: str, access_token: str, refresh_token: str):
    db = SessionLocal()
    existing = db.query(TokenStore).filter_by(user_id=user_id).first()

    if existing:
        existing.access_token = encrypt(access_token)
        existing.refresh_token = encrypt(refresh_token)
        existing.created_at = datetime.utcnow()
    else:
        new_token = TokenStore(
            user_id=user_id,
            access_token=encrypt(access_token),
            refresh_token=encrypt(refresh_token),
            created_at=datetime.utcnow()
        )
        db.add(new_token)

    db.commit()
    db.close()
