# import httpx
# import os

# async def refresh_access_token(refresh_token: str):
#     token_url = f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}/oauth2/v2.0/token"
#     data = {
#         "client_id": os.getenv("CLIENT_ID"),
#         "scope": os.getenv("SCOPES"),
#         "refresh_token": refresh_token,
#         "grant_type": "refresh_token",
#         "client_secret": os.getenv("CLIENT_SECRET")
#     }

#     async with httpx.AsyncClient() as client:
#         response = await client.post(token_url, data=data)
#         return response.json()


from models.db import SessionLocal, TokenStore
from utils.encryption import encrypt
from datetime import datetime

def save_tokens(user_id: str, access_token: str, refresh_token: str, expires_in: int):
    db = SessionLocal()
    token = db.query(TokenStore).filter_by(user_id=user_id).first()
    if token:
        token.access_token = encrypt(access_token)
        token.refresh_token = encrypt(refresh_token)
        token.expires_in = expires_in
        token.created_at = datetime.utcnow()
    else:
        token = TokenStore(
            user_id=user_id,
            access_token=encrypt(access_token),
            refresh_token=encrypt(refresh_token),
            expires_in=expires_in
        )
        db.add(token)
    db.commit()
    db.close()
