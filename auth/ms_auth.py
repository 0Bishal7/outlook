# # app/auth/ms_auth.py

# import os
# import httpx
# from fastapi import APIRouter
# from fastapi.responses import RedirectResponse
# from urllib.parse import urlencode
# from dotenv import load_dotenv

# from models.db import SessionLocal, TokenStore
# from utils.encryption import encrypt
# from .store_token import save_tokens  # import save_tokens function

# load_dotenv()

# router = APIRouter()

# CLIENT_ID = os.getenv("CLIENT_ID")
# CLIENT_SECRET = os.getenv("CLIENT_SECRET")
# TENANT_ID = os.getenv("TENANT_ID")
# AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
# REDIRECT_URI = os.getenv("REDIRECT_URI")
# SCOPES = os.getenv("SCOPES")  # e.g., "User.Read Mail.ReadWrite offline_access openid email profile"

# @router.get("/login")
# def login():
#     query_params = {
#         "client_id": CLIENT_ID,
#         "response_type": "code",
#         "redirect_uri": REDIRECT_URI,
#         "response_mode": "query",
#         "scope": SCOPES,
#     }
#     url = f"{AUTHORITY}/oauth2/v2.0/authorize?{urlencode(query_params)}"
#     return RedirectResponse(url)


# @router.get("/callback")
# async def auth_callback(code: str):
#     token_url = f"{AUTHORITY}/oauth2/v2.0/token"
#     data = {
#         "client_id": CLIENT_ID,
#         "scope": SCOPES,
#         "code": code,
#         "redirect_uri": REDIRECT_URI,
#         "grant_type": "authorization_code",
#         "client_secret": CLIENT_SECRET
#     }

#     async with httpx.AsyncClient() as client:
#         res = await client.post(token_url, data=data)
#         token_data = res.json()

#         # Error handling
#         if "access_token" not in token_data:
#             return {"error": "Failed to get access token", "details": token_data}

#         # Step 2: Get user email from Graph API /me
#         headers = {"Authorization": f"Bearer {token_data['access_token']}"}
#         me_response = await client.get("https://graph.microsoft.com/v1.0/me", headers=headers)
#         profile = me_response.json()

#         if "userPrincipalName" not in profile:
#             return {"error": "Failed to fetch user profile", "details": profile}

#         user_id = profile["userPrincipalName"]  # e.g., "example@outlook.com"

#     # Step 3: Save tokens in DB
#     save_tokens(
#         user_id=user_id,
#         access_token=token_data["access_token"],
#         refresh_token=token_data.get("refresh_token", "")
#     )

#     return {"message": "Login successful", "user": user_id}


import os, httpx, base64, json
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode
from dotenv import load_dotenv
from utils.token_manager import save_tokens

load_dotenv()
router = APIRouter()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
REDIRECT_URI = os.getenv("REDIRECT_URI")
SCOPES = os.getenv("SCOPES")

@router.get("/login")
def login():
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "response_mode": "query",
        "scope": SCOPES
    }
    auth_url = f"{AUTHORITY}/oauth2/v2.0/authorize?{urlencode(params)}"
    return RedirectResponse(auth_url)

def extract_user_id_from_token(token: str) -> str:
    payload = token.split(".")[1] + "==="
    padded_payload = payload[:len(payload) - len(payload) % 4]  # Ensure correct padding
    decoded = base64.urlsafe_b64decode(padded_payload).decode()
    return json.loads(decoded)["preferred_username"]

@router.get("/callback")
async def auth_callback(code: str):
    token_url = f"{AUTHORITY}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "scope": SCOPES,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "client_secret": CLIENT_SECRET,
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(token_url, data=data)
        token_data = res.json()

        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        expires_in = token_data["expires_in"]

        # ✅ Fetch user info using /me
        headers = {"Authorization": f"Bearer {access_token}"}
        user_response = await client.get("https://graph.microsoft.com/v1.0/me", headers=headers)

        if user_response.status_code != 200:
            return {"error": "Failed to fetch user info."}

        user_info = user_response.json()
        user_id = user_info["userPrincipalName"]  # or "mail" if you prefer

        # ✅ Save tokens to DB
        save_tokens(user_id, access_token, refresh_token, expires_in)

        return {"message": "Login successful", "user_id": user_id}
