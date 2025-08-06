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
# import os
# import logging

# from fastapi import APIRouter, HTTPException
# from sqlalchemy.orm import Session
# from dotenv import load_dotenv

# import httpx

# from models.db import SessionLocal, TokenStore
# from utils.encryption import decrypt, encrypt

# # ─── Load environment ──────────────────────────────────────────────────────────
# load_dotenv()

# CLIENT_ID     = os.getenv("CLIENT_ID")
# CLIENT_SECRET = os.getenv("CLIENT_SECRET")
# TENANT_ID     = os.getenv("TENANT_ID")
# # SCOPE         = "https://graph.microsoft.com/.default"
# SCOPE = "openid profile offline_access User.Read Mail.Read"

# # format the token URL with your tenant
# AZURE_TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

# GRAPH_API     = "https://graph.microsoft.com/v1.0"

# # ─── Logger ────────────────────────────────────────────────────────────────────
# logger = logging.getLogger("mail_inbox")
# logger.setLevel(logging.DEBUG)

# # ─── Helpers ───────────────────────────────────────────────────────────────────
# def get_db_session() -> Session:
#     """Open a new SQLAlchemy session, caller must close it"""
#     return SessionLocal()

# async def fetch_messages(access_token: str) -> httpx.Response:
#     headers = {
#         "Authorization": f"Bearer {access_token}",
#         "Accept":        "application/json",
#     }
#     async with httpx.AsyncClient() as client:
#         # Try general messages endpoint first, then inbox-specific
#         resp = await client.get(f"{GRAPH_API}/me/messages?$top=50", headers=headers)
#         if resp.status_code == 200:
#             return resp
#         else:
#             # Fallback to inbox-specific endpoint
#             return await client.get(f"{GRAPH_API}/me/mailFolders/inbox/messages", headers=headers)

# async def refresh_access_token(db: Session, token_entry: TokenStore) -> str:
#     """Use the stored refresh_token to get a new access_token from Azure."""
#     refresh_token = decrypt(token_entry.refresh_token)

#     payload = {
#         "client_id":     CLIENT_ID,
#         "client_secret": CLIENT_SECRET,
#         "grant_type":    "refresh_token",
#         "refresh_token": refresh_token,
#         "scope":         SCOPE,
#     }

#     async with httpx.AsyncClient() as client:
#         resp = await client.post(AZURE_TOKEN_URL, data=payload)

#     if resp.status_code != 200:
#         try:
#             error_data = resp.json()
#             error_msg = error_data.get("error_description", resp.text)
#         except:
#             error_msg = resp.text if resp.text else f"HTTP {resp.status_code} error"
#         logger.error("Refresh token failed [%d]: %s", resp.status_code, error_msg)
#         raise HTTPException(401, f"Could not refresh access token: {error_msg}")

#     tokens = resp.json()
#     # Persist encrypted tokens
#     token_entry.access_token  = encrypt(tokens["access_token"])
#     token_entry.refresh_token = encrypt(tokens["refresh_token"])
#     db.add(token_entry)
#     db.commit()
#     return tokens["access_token"]

# # ─── Router ────────────────────────────────────────────────────────────────────
# router = APIRouter(tags=["mail"])

# @router.get("/inbox")
# async def read_inbox():
#     db = get_db_session()
#     try:
#         # Force refresh from database
#         db.commit()
#         token_entry = db.query(TokenStore).first()
#         if not token_entry:
#             raise HTTPException(400, "No tokens found; please authenticate first.")

#         # 1) Decrypt and call Graph
#         access_token = decrypt(token_entry.access_token)
#         resp = await fetch_messages(access_token)

#         # 2) If 401, try refresh
#         if resp.status_code == 401:
#             logger.info("Access token expired, refreshing...")
#             access_token = await refresh_access_token(db, token_entry)
#             # Force database refresh after token update
#             db.commit()
#             db.refresh(token_entry)
#             resp = await fetch_messages(access_token)

#         # 3) If still not OK, raise with Graph's message
#         if resp.status_code != 200:
#             try:
#                 error_data = resp.json()
#                 err = error_data.get("error", {}).get("message", resp.text)
#             except:
#                 # Handle empty response or non-JSON response
#                 err = resp.text if resp.text else f"HTTP {resp.status_code} error"
#             logger.error("Graph GET failed [%d]: %s", resp.status_code, err)

#             # Check if user has no mailbox
#             if resp.status_code == 401:
#                 # First check if user has a mailbox
#                 user_resp = await fetch_user_info(access_token)
#                 if user_resp.status_code == 200:
#                     user_data = user_resp.json()
#                     user_email = user_data.get('mail')
#                     user_type = user_data.get('userType', 'Unknown')
                    
#                     if not user_email:
#                         raise HTTPException(
#                             400, 
#                             f"User has no mailbox. User type: {user_type}. "
#                             "Please use an account with a mailbox or create a test user in Azure AD. "
#                             "Alternatively, use /mail/user-info, /mail/groups, or /mail/status endpoints."
#                         )
#                     else:
#                         # Check if it's a permissions issue
#                         if "permission" in err.lower() or "scope" in err.lower():
#                             raise HTTPException(403, f"Permission denied: {err}")
#                         else:
#                             raise HTTPException(401, f"Authentication failed: {err}")
#                 else:
#                     raise HTTPException(401, f"Authentication failed: {err}")
#             elif resp.status_code == 403:
#                 raise HTTPException(403, f"Permission denied: {err}")
#             else:
#                 raise HTTPException(502, f"Graph API error: {err}")

#         # 4) Success: parse and return
#         items = resp.json().get("value", [])
#         return [
#             {
#                 "subject":      m.get("subject"),
#                 "from":         m.get("from", {})
#                                      .get("emailAddress", {})
#                                      .get("address"),
#                 "receivedDate": m.get("receivedDateTime"),
#                 "preview":      m.get("bodyPreview"),
#             }
#             for m in items
#         ]

#     finally:
#         db.close()

# async def fetch_user_info(access_token: str):
#     """Fetch user information from Graph API"""
#     headers = {
#         "Authorization": f"Bearer {access_token}",
#         "Accept": "application/json",
#     }
    
#     async with httpx.AsyncClient() as client:
#         return await client.get("https://graph.microsoft.com/v1.0/me", headers=headers)

# @router.get("/user-info")
# async def get_user_info():
#     """Get user information that works with any account type"""
#     db = get_db_session()
#     try:
#         token_entry = db.query(TokenStore).first()
#         if not token_entry:
#             raise HTTPException(400, "No tokens found; please authenticate first.")

#         access_token = decrypt(token_entry.access_token)
#         resp = await fetch_user_info(access_token)

#         if resp.status_code != 200:
#             raise HTTPException(401, "Failed to fetch user information")

#         user_data = resp.json()
#         return {
#             "displayName": user_data.get("displayName"),
#             "userPrincipalName": user_data.get("userPrincipalName"),
#             "mail": user_data.get("mail"),
#             "userType": user_data.get("userType"),
#             "accountEnabled": user_data.get("accountEnabled"),
#             "id": user_data.get("id"),
#             "hasMailbox": user_data.get("mail") is not None
#         }

#     finally:
#         db.close()

# @router.get("/groups")
# async def get_user_groups():
#     """Get user groups that works with any account type"""
#     db = get_db_session()
#     try:
#         token_entry = db.query(TokenStore).first()
#         if not token_entry:
#             raise HTTPException(400, "No tokens found; please authenticate first.")

#         access_token = decrypt(token_entry.access_token)
#         headers = {
#             "Authorization": f"Bearer {access_token}",
#             "Accept": "application/json",
#         }

#         async with httpx.AsyncClient() as client:
#             resp = await client.get("https://graph.microsoft.com/v1.0/me/memberOf", headers=headers)

#             if resp.status_code != 200:
#                 raise HTTPException(401, "Failed to fetch user groups")

#             data = resp.json()
#             groups = data.get("value", [])
#             return {
#                 "groups": [
#                     {
#                         "id": group.get("id"),
#                         "displayName": group.get("displayName"),
#                         "description": group.get("description")
#                     }
#                     for group in groups
#                 ],
#                 "count": len(groups)
#             }

#     finally:
#         db.close()

# @router.get("/status")
# async def get_mail_status():
#     """Check if user has mail access and provide detailed status"""
#     db = get_db_session()
#     try:
#         token_entry = db.query(TokenStore).first()
#         if not token_entry:
#             raise HTTPException(400, "No tokens found; please authenticate first.")

#         access_token = decrypt(token_entry.access_token)
        
#         # Get user info
#         user_resp = await fetch_user_info(access_token)
#         if user_resp.status_code != 200:
#             raise HTTPException(401, "Failed to fetch user information")

#         user_data = user_resp.json()
#         user_email = user_data.get('mail')
#         user_type = user_data.get('userType', 'Unknown')

#         # Test mail access
#         headers = {
#             "Authorization": f"Bearer {access_token}",
#             "Accept": "application/json",
#         }

#         async with httpx.AsyncClient() as client:
#             # Test mail folders
#             folders_resp = await client.get("https://graph.microsoft.com/v1.0/me/mailFolders", headers=headers)
            
#             # Test messages
#             messages_resp = await client.get("https://graph.microsoft.com/v1.0/me/messages?$top=1", headers=headers)

#         return {
#             "user": {
#                 "displayName": user_data.get("displayName"),
#                 "userPrincipalName": user_data.get("userPrincipalName"),
#                 "mail": user_email,
#                 "userType": user_type,
#                 "accountEnabled": user_data.get("accountEnabled"),
#                 "hasMailbox": user_email is not None
#             },
#             "mailAccess": {
#                 "hasMailbox": user_email is not None,
#                 "foldersAccessible": folders_resp.status_code == 200,
#                 "messagesAccessible": messages_resp.status_code == 200,
#                 "foldersStatus": folders_resp.status_code,
#                 "messagesStatus": messages_resp.status_code
#             },
#             "availableEndpoints": [
#                 "/mail/user-info - Get user information (works with any account)",
#                 "/mail/groups - Get user groups (works with any account)",
#                 "/mail/status - Check mail access status (works with any account)",
#                 "/mail/inbox - Get inbox messages (requires mailbox)"
#             ],
#             "recommendations": [
#                 "Use an account with a mailbox for full mail functionality" if not user_email else "Mail access is available",
#                 "Create a test user in Azure AD with a mailbox" if not user_email else "Account is properly configured",
#                 "Use the /user-info and /groups endpoints for basic functionality" if not user_email else "All endpoints are available"
#             ]
#         }

#     finally:
#         db.close()

# @router.get("/help")
# async def get_api_help():
#     """Get API help and documentation"""
#     return {
#         "title": "Outlook Mail API",
#         "description": "Microsoft Graph Mail API for accessing Outlook mail data",
#         "endpoints": {
#             "/auth/login": {
#                 "method": "GET",
#                 "description": "Start OAuth 2.0 flow",
#                 "requires": "None"
#             },
#             "/auth/callback": {
#                 "method": "GET", 
#                 "description": "Handle OAuth redirect, exchange tokens",
#                 "requires": "Authorization code from login"
#             },
#             "/mail/fetch-emails": {
#                 "method": "GET",
#                 "description": "Fetch emails using access token",
#                 "requires": "Authentication and mailbox"
#             },
#             "/mail/refresh-token": {
#                 "method": "POST",
#                 "description": "Manually refresh access token",
#                 "requires": "Valid refresh token"
#             },
#             "/mail/logout": {
#                 "method": "POST",
#                 "description": "Log out and revoke access",
#                 "requires": "Active session"
#             },
#             "/mail/user-info": {
#                 "method": "GET",
#                 "description": "Get user information (works with any account)",
#                 "requires": "Authentication only"
#             },
#             "/mail/groups": {
#                 "method": "GET",
#                 "description": "Get user groups (works with any account)",
#                 "requires": "Authentication only"
#             },
#             "/mail/status": {
#                 "method": "GET",
#                 "description": "Check mail access status and capabilities",
#                 "requires": "Authentication only"
#             },
#             "/mail/inbox": {
#                 "method": "GET",
#                 "description": "Get inbox messages",
#                 "requires": "Account with mailbox"
#             },
#             "/mail/emails": {
#                 "method": "GET",
#                 "description": "Get stored emails from DB (placeholder)",
#                 "requires": "Authentication only"
#             }
#         },
#         "authentication": {
#             "method": "OAuth 2.0",
#             "endpoint": "/auth/login",
#             "callback": "/auth/callback"
#         },
#         "errorCodes": {
#             "400": "Bad Request - User has no mailbox or invalid request",
#             "401": "Unauthorized - Authentication failed or token expired",
#             "403": "Forbidden - Permission denied",
#             "502": "Bad Gateway - Graph API error"
#         },
#         "solutions": {
#             "noMailbox": "Create a test user in Azure AD with a mailbox",
#             "authentication": "Use /auth/login to authenticate",
#             "tokenRefresh": "Tokens are automatically refreshed when needed"
#         },
#         "workflow": {
#             "step1": "Call /auth/login to start authentication",
#             "step2": "User completes OAuth flow",
#             "step3": "Call /auth/callback to exchange code for tokens",
#             "step4": "Use /mail/fetch-emails or /mail/inbox to get emails",
#             "step5": "Use /mail/refresh-token if needed",
#             "step6": "Use /mail/logout when done"
#         }
#     }

# @router.get("/fetch-emails")
# async def fetch_emails():
#     """Fetch emails using access token - alternative to /inbox"""
#     db = get_db_session()
#     try:
#         token_entry = db.query(TokenStore).first()
#         if not token_entry:
#             raise HTTPException(400, "No tokens found; please authenticate first.")

#         # 1) Decrypt and call Graph
#         access_token = decrypt(token_entry.access_token)
#         resp = await fetch_messages(access_token)

#         # 2) If 401, try refresh
#         if resp.status_code == 401:
#             logger.info("Access token expired, refreshing...")
#             access_token = await refresh_access_token(db, token_entry)
#             # Force database refresh after token update
#             db.commit()
#             db.refresh(token_entry)
#             resp = await fetch_messages(access_token)

#         # 3) If still not OK, raise with Graph's message
#         if resp.status_code != 200:
#             try:
#                 error_data = resp.json()
#                 err = error_data.get("error", {}).get("message", resp.text)
#             except:
#                 err = resp.text if resp.text else f"HTTP {resp.status_code} error"
#             logger.error("Graph GET failed [%d]: %s", resp.status_code, err)

#             # Check if user has no mailbox
#             if resp.status_code == 401:
#                 user_resp = await fetch_user_info(access_token)
#                 if user_resp.status_code == 200:
#                     user_data = user_resp.json()
#                     user_email = user_data.get('mail')
#                     user_type = user_data.get('userType', 'Unknown')
                    
#                     if not user_email:
#                         raise HTTPException(
#                             400, 
#                             f"User has no mailbox. User type: {user_type}. "
#                             "Please use an account with a mailbox or create a test user in Azure AD."
#                         )
#                     else:
#                         if "permission" in err.lower() or "scope" in err.lower():
#                             raise HTTPException(403, f"Permission denied: {err}")
#                         else:
#                             raise HTTPException(401, f"Authentication failed: {err}")
#                 else:
#                     raise HTTPException(401, f"Authentication failed: {err}")
#             elif resp.status_code == 403:
#                 raise HTTPException(403, f"Permission denied: {err}")
#             else:
#                 raise HTTPException(502, f"Graph API error: {err}")

#         # 4) Success: parse and return
#         items = resp.json().get("value", [])
#         return {
#             "success": True,
#             "count": len(items),
#             "emails": [
#                 {
#                     "id": m.get("id"),
#                     "subject": m.get("subject"),
#                     "from": m.get("from", {})
#                                      .get("emailAddress", {})
#                                      .get("address"),
#                     "receivedDate": m.get("receivedDateTime"),
#                     "preview": m.get("bodyPreview"),
#                     "isRead": m.get("isRead", False),
#                     "hasAttachments": m.get("hasAttachments", False)
#                 }
#                 for m in items
#             ]
#         }

#     finally:
#         db.close()

# @router.post("/refresh-token")
# async def refresh_token():
#     """Manually refresh access token using refresh token"""
#     db = get_db_session()
#     try:
#         token_entry = db.query(TokenStore).first()
#         if not token_entry:
#             raise HTTPException(400, "No tokens found; please authenticate first.")

#         # Decrypt refresh token
#         refresh_token = decrypt(token_entry.refresh_token)
        
#         # Token refresh parameters
#         token_url = f"{os.getenv('AUTHORITY')}/oauth2/v2.0/token"
#         data = {
#             "client_id": os.getenv("CLIENT_ID"),
#             "scope": os.getenv("SCOPES"),
#             "refresh_token": refresh_token,
#             "grant_type": "refresh_token",
#             "client_secret": os.getenv("CLIENT_SECRET"),
#         }
        
#         async with httpx.AsyncClient() as client:
#             resp = await client.post(token_url, data=data)
            
#             if resp.status_code == 200:
#                 token_data = resp.json()
#                 new_access_token = token_data["access_token"]
#                 new_refresh_token = token_data["refresh_token"]
#                 expires_in = token_data["expires_in"]
                
#                 # Update database
#                 from utils.encryption import encrypt
#                 from utils.token_manager import save_tokens
                
#                 # Get user info to save tokens
#                 user_response = await client.get(
#                     "https://graph.microsoft.com/v1.0/me",
#                     headers={"Authorization": f"Bearer {new_access_token}"}
#                 )
                
#                 if user_response.status_code == 200:
#                     user_info = user_response.json()
#                     user_id = user_info["userPrincipalName"]
                    
#                     # Save new tokens
#                     save_tokens(user_id, new_access_token, new_refresh_token, expires_in)
                    
#                     return {
#                         "success": True,
#                         "message": "Token refreshed successfully",
#                         "expires_in": expires_in,
#                         "user_id": user_id
#                     }
#                 else:
#                     raise HTTPException(500, "Failed to get user info after token refresh")
#             else:
#                 error_data = resp.json() if resp.text else {}
#                 error_msg = error_data.get("error_description", "Token refresh failed")
#                 raise HTTPException(400, f"Token refresh failed: {error_msg}")
                
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Token refresh error: {e}")
#         raise HTTPException(500, f"Token refresh error: {str(e)}")
#     finally:
#         db.close()

# @router.post("/logout")
# async def logout():
#     """Log out and revoke access"""
#     db = get_db_session()
#     try:
#         token_entry = db.query(TokenStore).first()
#         if not token_entry:
#             return {"success": True, "message": "No active session found"}
        
#         # Delete tokens from database
#         db.delete(token_entry)
#         db.commit()
        
#         return {
#             "success": True,
#             "message": "Logged out successfully",
#             "user_id": token_entry.user_id
#         }
        
#     except Exception as e:
#         logger.error(f"Logout error: {e}")
#         raise HTTPException(500, f"Logout error: {str(e)}")
#     finally:
#         db.close()

# @router.get("/emails")
# async def get_stored_emails():
#     """Get stored emails from database (if implemented)"""
#     # This endpoint would typically fetch emails stored in your database
#     # For now, we'll return a message indicating this feature
#     return {
#         "success": True,
#         "message": "Email storage feature not implemented yet",
#         "suggestion": "Use /fetch-emails or /inbox to get emails directly from Microsoft Graph API",
#         "available_endpoints": [
#             "/mail/fetch-emails - Get emails from Graph API",
#             "/mail/inbox - Get inbox messages",
#             "/mail/user-info - Get user information",
#             "/mail/status - Check mail access status"
#         ]
#     }
