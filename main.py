# # main.py

# from fastapi import FastAPI, Request
# from fastapi.templating import Jinja2Templates
# from fastapi.responses import HTMLResponse

# from auth.ms_auth import router as auth_router
# from ms_graph.mail import router as mail_router
# from models.db import init_db, SessionLocal, TokenStore

# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.staticfiles import StaticFiles
# from fastapi import APIRouter

# app = FastAPI()

# # ✅ Optional: CORS setup (allow frontend interaction if needed)
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # Replace with your frontend domain in production
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# # ✅ Initialize DB on startup
# @app.on_event("startup")
# def on_startup():
#     init_db()

# # ✅ Templates directory
# templates = Jinja2Templates(directory="templates")

# # ✅ Serve static files (CSS, JS)
# app.mount("/static", StaticFiles(directory="static"), name="static")

# # ✅ Home route (render login page)
# @app.get("/", response_class=HTMLResponse)
# def home(request: Request):
#     return templates.TemplateResponse("login.html", {"request": request})

# # ✅ Include routers
# app.include_router(auth_router, prefix="/auth")
# app.include_router(mail_router, prefix="/mail")

# # ✅ Debug route (view tokens)
# debug_router = APIRouter()

# @debug_router.get("/debug/tokens")
# def list_tokens():
#     db = SessionLocal()
#     tokens = db.query(TokenStore).all()
#     db.close()
#     return [{"user_id": t.user_id, "created_at": t.created_at.isoformat()} for t in tokens]

# app.include_router(debug_router)


from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from auth.ms_auth import router as auth_router
from ms_graph.mail import router as mail_router
from models.db import init_db

app = FastAPI()
init_db()

templates = Jinja2Templates(directory="templates")

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

app.include_router(auth_router, prefix="/auth")
app.include_router(mail_router, prefix="/mail")
