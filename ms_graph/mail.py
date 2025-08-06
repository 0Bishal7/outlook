# # app/ms_graph/mail.py

# from fastapi import APIRouter, Request
# import httpx, os
# from models.db import SessionLocal, TokenStore
# from utils.encryption import decrypt

# GRAPH_API = os.getenv("GRAPH_API")
# router = APIRouter()

# def get_user_token(user_id: str):
#     db = SessionLocal()
#     token = db.query(TokenStore).filter(TokenStore.user_id == user_id).first()
#     db.close()
#     return decrypt(token.access_token) if token else None

# @router.get("/inbox")
# async def read_emails(request: Request):
#     # hardcoded or session-based user ID (email used to store token)
#     user_id = "roybishal200189_gmail.com#EXT#@roybishal200189gmail.onmicrosoft.com"  # Replace this with actual user from login
#     access_token = get_user_token(user_id)

#     if not access_token:
#         return {"error": "Access token not found for user."}

#     headers = {"Authorization": f"Bearer {access_token}"}
#     endpoint = f"{GRAPH_API}/me/messages?$top=10"

#     async with httpx.AsyncClient() as client:
#         response = await client.get(endpoint, headers=headers)
#         if response.status_code != 200:
#             return {"error": response.text}
#         return response.json()


# from fastapi import APIRouter, Request
# import os, httpx
# from models.db import SessionLocal, TokenStore
# from utils.encryption import decrypt
# from dotenv import load_dotenv

# load_dotenv()
# router = APIRouter()
# GRAPH_API = os.getenv("GRAPH_API")

# def get_user_token(user_id: str):
#     db = SessionLocal()
#     token = db.query(TokenStore).filter(TokenStore.user_id == user_id).first()
#     db.close()
#     return decrypt(token.access_token) if token else None

# @router.get("/inbox")
# async def read_emails():
#     user_id = "roybishal200189@gmail.com"  # should match the decoded preferred_username
#     access_token = get_user_token(user_id)

#     if not access_token:
#         return {"error": "Access token not found for user."}

#     headers = {"Authorization": f"Bearer {access_token}"}
#     endpoint = f"{GRAPH_API}/me/messages?$top=10"

#     async with httpx.AsyncClient() as client:
#         res = await client.get(endpoint, headers=headers)
#         if res.status_code != 200:
#             return {"error": res.text}
#         return res.json()


# # app/ms_graph/mail.py

# from fastapi import APIRouter, Request
# import httpx, os
# from models.db import SessionLocal, TokenStore
# from utils.encryption import decrypt

# GRAPH_API = os.getenv("GRAPH_API")
# router = APIRouter()

# def get_user_token(user_id: str):
#     db = SessionLocal()
#     token = db.query(TokenStore).filter(TokenStore.user_id == user_id).first()
#     db.close()
#     return decrypt(token.access_token) if token else None

# @router.get("/inbox")
# async def read_emails(request: Request):
#     # hardcoded or session-based user ID (email used to store token)
#     user_id = "roybishal200189_gmail.com#EXT#@roybishal200189gmail.onmicrosoft.com"  # Replace this with actual user from login
#     access_token = get_user_token(user_id)

#     if not access_token:
#         return {"error": "Access token not found for user."}

#     headers = {"Authorization": f"Bearer {access_token}"}
#     endpoint = f"{GRAPH_API}/me/messages?$top=10"

#     async with httpx.AsyncClient() as client:
#         response = await client.get(endpoint, headers=headers)
#         if response.status_code != 200:
#             return {"error": response.text}
#         return response.json()


from fastapi import APIRouter, HTTPException
from models.db import SessionLocal, TokenStore
from utils.encryption import decrypt
import httpx

router = APIRouter()

GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"

@router.get("/inbox")
async def read_mail():
    # 1. Get token from DB
    db = SessionLocal()
    token_entry = db.query(TokenStore).first()  # or filter_by(user_id='xyz')
    db.close()

    if not token_entry:
        raise HTTPException(status_code=400, detail="Access token not found.")

    # 2. Decrypt token
    access_token = decrypt(token_entry.access_token)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    # 3. Call Microsoft Graph API to get messages
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{GRAPH_API_ENDPOINT}/me/messages", headers=headers)

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch emails from Graph API")

    data = response.json()

    # 4. Extract relevant message fields
    messages = data.get("value", [])
    return [
        {
            "subject": msg.get("subject"),
            "from": msg.get("from", {}).get("emailAddress", {}).get("address"),
            "received": msg.get("receivedDateTime"),
            "bodyPreview": msg.get("bodyPreview"),
        }
        for msg in messages
    ]
