# # utils/encryption.py

# from cryptography.fernet import Fernet
# import os
# from dotenv import load_dotenv

# load_dotenv()

# # Get Fernet key from environment
# ENCRYPTION_KEY = os.getenv("SECRET_KEY")

# if not ENCRYPTION_KEY:
#     raise ValueError("SECRET_KEY not found in environment")

# f = Fernet(ENCRYPTION_KEY.encode())

# def encrypt(text: str) -> str:
#     return f.encrypt(text.encode()).decode()

# def decrypt(token: str) -> str:
#     return f.decrypt(token.encode()).decode()


from cryptography.fernet import Fernet
import os
from dotenv import load_dotenv

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
fernet = Fernet(SECRET_KEY.encode())

def encrypt(data: str) -> str:
    return fernet.encrypt(data.encode()).decode()

def decrypt(data: str) -> str:
    return fernet.decrypt(data.encode()).decode()
