import os
import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session
from dotenv import load_dotenv

import httpx

from models.db import SessionLocal, TokenStore
from utils.encryption import decrypt, encrypt

# ─── Load environment ──────────────────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID")

# Modified scopes - added offline_access and made them space-separated
SCOPES = "openid profile email User.Read Mail.Read offline_access"

AZURE_TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
GRAPH_API = "https://graph.microsoft.com/v1.0"

# ─── Logger ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("mail_inbox")
logger.setLevel(logging.DEBUG)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def get_db_session() -> Session:
    return SessionLocal()

async def fetch_messages(access_token: str) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient() as client:
        try:
            # First try to get user's mailbox settings to check if mail is enabled
            mailbox_resp = await client.get(
                f"{GRAPH_API}/me/mailboxSettings",
                headers=headers
            )
            
            if mailbox_resp.status_code == 200:
                # If mailbox exists, fetch messages
                return await client.get(
                    f"{GRAPH_API}/me/messages?$top=50&$select=subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments",
                    headers=headers
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="User has no mailbox or mail access not enabled"
                )
                
        except Exception as e:
            logger.error(f"Error checking mailbox: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail="Could not verify mailbox access"
            )

async def refresh_access_token(db: Session, token_entry: TokenStore) -> str:
    """Refresh access token with proper error handling"""
    if not token_entry or not token_entry.refresh_token:
        raise HTTPException(400, "No refresh token available")
    
    try:
        refresh_token = decrypt(token_entry.refresh_token)
        
        payload = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPES,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(AZURE_TOKEN_URL, data=payload)
            
            if resp.status_code != 200:
                error_data = resp.json()
                error_msg = error_data.get("error_description", "Unknown error during token refresh")
                
                # Handle specific consent error
                if "AADSTS65001" in error_msg:
                    raise HTTPException(
                        status_code=403,
                        detail="Admin consent required. Please have your administrator grant permissions to this application."
                    )
                
                raise HTTPException(401, f"Token refresh failed: {error_msg}")

            tokens = resp.json()
            
            # Update the tokens in database
            token_entry.access_token = encrypt(tokens["access_token"])
            if "refresh_token" in tokens:
                token_entry.refresh_token = encrypt(tokens["refresh_token"])
            
            db.add(token_entry)
            db.commit()
            
            return tokens["access_token"]
            
    except Exception as e:
        logger.error(f"Token refresh error: {str(e)}")
        raise HTTPException(500, "Internal server error during token refresh")

async def fetch_user_info(access_token: str):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    
    async with httpx.AsyncClient() as client:
        return await client.get(f"{GRAPH_API}/me", headers=headers)

# ─── Router ────────────────────────────────────────────────────────────────────
router = APIRouter(tags=["mail"])

@router.get("/fetch-emails")
async def fetch_emails():
    """Fetch emails with proper permission handling"""
    db = get_db_session()
    try:
        token_entry = db.query(TokenStore).first()
        if not token_entry:
            raise HTTPException(400, "No tokens found; please authenticate first")

        try:
            # First get user info to check account type
            access_token = decrypt(token_entry.access_token)
            user_resp = await fetch_user_info(access_token)
            
            if user_resp.status_code != 200:
                raise HTTPException(401, "Could not fetch user info")
                
            user_data = user_resp.json()
            user_principal = user_data.get("userPrincipalName", "")
            
            # Check if it's a personal Microsoft account
            if "#EXT#" in user_principal:
                raise HTTPException(
                    status_code=400,
                    detail="Personal Microsoft accounts may require different permissions. "
                           "Please use a work/school account or check the app registration permissions."
                )
            
            # Try to fetch messages
            resp = await fetch_messages(access_token)
            
            if resp.status_code == 401:
                # Token expired, try to refresh
                access_token = await refresh_access_token(db, token_entry)
                db.commit()
                db.refresh(token_entry)
                resp = await fetch_messages(access_token)
                
            if resp.status_code != 200:
                error_data = resp.json()
                error_msg = error_data.get("error", {}).get("message", "Unknown error")
                raise HTTPException(resp.status_code, detail=error_msg)
                
            items = resp.json().get("value", [])
            return {
                "success": True,
                "count": len(items),
                "emails": [
                    {
                        "id": m.get("id"),
                        "subject": m.get("subject"),
                        "from": m.get("from", {}).get("emailAddress", {}).get("address"),
                        "receivedDate": m.get("receivedDateTime"),
                        "preview": m.get("bodyPreview"),
                        "isRead": m.get("isRead", False),
                        "hasAttachments": m.get("hasAttachments", False)
                    }
                    for m in items
                ]
            }
            
        except HTTPException as he:
            raise he
        except Exception as e:
            logger.error(f"Error fetching emails: {str(e)}")
            raise HTTPException(500, "Internal server error")
            
    finally:
        db.close()

# @router.post("/refresh-token")
# async def refresh_token():
#     """Refresh token endpoint with better error handling"""
#     db = get_db_session()
#     try:
#         token_entry = db.query(TokenStore).first()
#         if not token_entry:
#             raise HTTPException(400, "No tokens found; please authenticate first")

#         try:
#             new_access_token = await refresh_access_token(db, token_entry)
            
#             # Get user info to return with response
#             user_resp = await fetch_user_info(new_access_token)
#             user_data = user_resp.json() if user_resp.status_code == 200 else {}
            
#             return {
#                 "success": True,
#                 "message": "Token refreshed successfully",
#                 "user_id": user_data.get("userPrincipalName", "unknown"),
#                 "access_token": new_access_token[:50] + "..."  # Don't return full token
#             }
            
#         except HTTPException as he:
#             raise he
#         except Exception as e:
#             logger.error(f"Refresh token error: {str(e)}")
#             raise HTTPException(500, "Internal server error")
            
#     finally:
#         db.close()

@router.post("/logout")
async def logout():
    """Logout endpoint"""
    db = get_db_session()
    try:
        token_entry = db.query(TokenStore).first()
        if not token_entry:
            return {"success": True, "message": "No active session found"}
        
        db.delete(token_entry)
        db.commit()
        
        return {
            "success": True,
            "message": "Logged out successfully"
        }
        
    except Exception as e:
        logger.error(f"Logout error: {str(e)}")
        raise HTTPException(500, "Internal server error")
    finally:
        db.close()

@router.get("/user-info")
async def get_user_info():
    """Get user info with better error handling"""
    db = get_db_session()
    try:
        token_entry = db.query(TokenStore).first()
        if not token_entry:
            raise HTTPException(400, "No tokens found; please authenticate first")

        try:
            access_token = decrypt(token_entry.access_token)
            resp = await fetch_user_info(access_token)
            
            if resp.status_code != 200:
                raise HTTPException(401, "Could not fetch user info")
                
            user_data = resp.json()
            
            return {
                "displayName": user_data.get("displayName"),
                "userPrincipalName": user_data.get("userPrincipalName"),
                "mail": user_data.get("mail"),
                "userType": user_data.get("userType"),
                "accountEnabled": user_data.get("accountEnabled"),
                "id": user_data.get("id"),
                "hasMailbox": user_data.get("mail") is not None,
                "isPersonalAccount": "#EXT#" in user_data.get("userPrincipalName", "")
            }
            
        except HTTPException as he:
            raise he
        except Exception as e:
            logger.error(f"User info error: {str(e)}")
            raise HTTPException(500, "Internal server error")
            
    finally:
        db.close()

@router.get("/emails")
async def get_stored_emails():
    """Placeholder for stored emails"""
    return {
        "success": True,
        "message": "Email storage feature not implemented yet",
        "suggestion": "Use /fetch-emails to get emails directly from Microsoft Graph API"
    }
