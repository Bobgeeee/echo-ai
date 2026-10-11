import os
import re
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Request, Response, BackgroundTasks, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import jwt
from passlib.context import CryptContext
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import google.generativeai as genai
import bcrypt
try:
    import passlib.handlers.bcrypt
    if not hasattr(bcrypt, '__about__'):
        bcrypt.__about__ = type('obj', (object,), {'__version__': '4.0.0'})
except Exception:
    pass
# ==========================================
# CONFIGURATION & ENVIRONMENT VARIABLES
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./echo_ai.db")
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# Email Configuration (SMTP)
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "ibrahimmuhammadlawal491@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "codk pems fpog hqiy")
APP_DOMAIN = os.getenv("APP_DOMAIN", "localhost:8000")

# Gemini API Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# DATABASE SETUP
# ==========================================
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ThoughtDump(Base):
    __tablename__ = "thought_dumps"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    raw_text = Column(Text, nullable=False)
    parsed_data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(bind=engine)

# ==========================================
# SECURITY & AUTH HELPERS
# ==========================================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        if token.startswith("Bearer "):
            token = token.split(" ")[1]
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
        user = db.query(User).filter(User.email == email).first()
        return user
    except Exception:
        return None

# ==========================================
# ASYNCHRONOUS EMAIL SENDER
# ==========================================
def send_email_notification(recipient_email: str, subject: str, body_content: str):
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body_content, 'html'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"✅ Email successfully sent to {recipient_email}")
    except Exception as e:
        print(f"❌ Failed to send email to {recipient_email}: {e}")

# ==========================================
# GEMINI AI SMART ENGINE & PUNCTUATOR
# ==========================================
SYSTEM_PROMPT = """
You are an advanced AI assistant that handles voice transcript restoration and smart task routing.

TASK 1: TEXT RESTORATION
- Automatically polish the user's input text by adding proper capitalization, commas, full stops, question marks, apostrophes (e.g. "don't", "it's"), and paragraph line breaks.
- Standardize all time references cleanly. Example: "12 30 pm", "12:30 p.m.", or "12.30pm" MUST become "12:30 PM".

TASK 2: SINGLE-CATEGORY ROUTING
- Route the refined input into EXACTLY ONE category:
  1. "calendar_events": If text mentions a SPECIFIC TIME (e.g., "12:20 PM", "3:00 PM").
  2. "tasks": If text is an ACTIONABLE TASK without a specific time.
  3. "ideas_and_notes": If text is a GENERAL THOUGHT or note.
- Return EMPTY ARRAYS [] for categories that do not apply.

Return ONLY a valid JSON object matching this schema:
{
  "cleaned_text": "Polished text with perfect punctuation, apostrophes, and uppercase AM/PM times.",
  "calendar_events": [
    {
      "title": "Clear event title",
      "day_or_date": "Today / Tomorrow or YYYY-MM-DD",
      "time": "Standardized time (e.g. '12:30 PM')"
    }
  ],
  "tasks": [
    {
      "task": "Task description",
      "priority": "High / Medium / Low",
      "due_date_mention": "Due date or 'Today'"
    }
  ],
  "ideas_and_notes": [
    {
      "category": "General Thought / Project / Personal",
      "summary": "Brief summary"
    }
  ]
}
"""

def normalize_time_string(time_str: str) -> str:
    """Fallback utility to clean up any messy time format to '12:30 PM'."""
    if not time_str:
        return ""
    clean = time_str.replace('.', '').strip().upper()
    clean = re.sub(r'(\d+:\d+)(AM|PM)', r'\1 \2', clean)
    return clean

def extract_time_fallback(raw_text: str) -> Optional[str]:
    pattern = r'(1[0-2]|0?[1-9])[:\.]?([0-5][0-9])?\s*(a\.?m\.?|p\.?m\.?|am|pm)'
    match = re.search(pattern, raw_text, re.IGNORECASE)
    if match:
        return normalize_time_string(match.group(0))
    return None

def parse_with_gemini(raw_text: str) -> dict:
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(f"{SYSTEM_PROMPT}\n\nUser Input: {raw_text}")
        
        cleaned_json = response.text.strip().replace("```json", "").replace("```", "")
        parsed_data = json.loads(cleaned_json)
        
        extracted_time = extract_time_fallback(raw_text)
        
        if parsed_data.get("calendar_events"):
            for event in parsed_data["calendar_events"]:
                if event.get("time"):
                    event["time"] = normalize_time_string(event["time"])
                elif extracted_time:
                    event["time"] = extracted_time

        if extracted_time:
            parsed_data["tasks"] = []
            parsed_data["ideas_and_notes"] = []
            if not parsed_data.get("calendar_events"):
                parsed_data["calendar_events"] = [{
                    "title": raw_text.strip(),
                    "day_or_date": "Today",
                    "time": extracted_time
                }]

        return parsed_data

    except Exception as e:
        print("Gemini Parsing Exception:", e)
        extracted_time = extract_time_fallback(raw_text)
        if extracted_time:
            return {
                "cleaned_text": raw_text,
                "calendar_events": [{
                    "title": raw_text.strip(),
                    "day_or_date": "Today",
                    "time": extracted_time
                }],
                "tasks": [],
                "ideas_and_notes": []
            }
        return {
            "cleaned_text": raw_text,
            "calendar_events": [],
            "tasks": [{"task": raw_text, "priority": "Medium", "due_date_mention": "Today"}],
            "ideas_and_notes": []
        }

# ==========================================
# FASTAPI APP & ROUTES
# ==========================================
app = FastAPI(title="Echo AI Engine")

class RegisterSchema(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str

class LoginSchema(BaseModel):
    email: EmailStr
    password: str

class DumpSchema(BaseModel):
    raw_text: str

class ForgotPasswordSchema(BaseModel):
    email: EmailStr

class ResetPasswordSchema(BaseModel):
    token: str
    new_password: str

@app.get("/", response_class=HTMLResponse)
@app.get("/reset-password", response_class=HTMLResponse)
def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/api/register")
def register(user_data: RegisterSchema, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if user_data.password != user_data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_pwd = get_password_hash(user_data.password)
    new_user = User(email=user_data.email, hashed_password=hashed_pwd)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    email_body = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; background: #0d1117; color: #c9d1d9; border-radius: 8px;">
        <h2 style="color: #58a6ff;">Welcome to Echo AI! 🎉</h2>
        <p>Your account (<strong>{user_data.email}</strong>) was created successfully.</p>
        <p>You can now unload your mind, capture tasks, and organize your schedule seamlessly.</p>
        <br>
        <p style="font-size: 12px; color: #8b949e;">Echo AI Productivity Engine</p>
    </div>
    """
    background_tasks.add_task(send_email_notification, user_data.email, "Welcome to Echo AI!", email_body)

    token = create_access_token({"sub": new_user.email})
    response = JSONResponse(content={"message": "Account created successfully"})
    response.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True)
    return response

@app.post("/api/login")
def login(user_data: LoginSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token({"sub": user.email})
    response = JSONResponse(content={"message": "Logged in successfully"})
    response.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True)
    return response

@app.post("/api/logout")
def logout():
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie(key="access_token")
    return response

@app.get("/api/me")
def get_me(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"id": user.id, "email": user.email}

@app.post("/api/forgot-password")
def forgot_password(data: ForgotPasswordSchema, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        return {"message": "If the email exists, a reset link has been sent."}

    # Generate a 30-minute secure reset token
    reset_token = create_access_token({"sub": user.email}, expires_delta=timedelta(minutes=30))
    
    protocol = "https" if "localhost" not in APP_DOMAIN else "http"
    reset_link = f"{protocol}://{APP_DOMAIN}/reset-password?token={reset_token}"

    email_body = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; background: #0d1117; color: #c9d1d9; border-radius: 8px;">
        <h2 style="color: #58a6ff;">Password Reset Request</h2>
        <p>You requested to reset your password for Echo AI.</p>
        <p>Click the button below to set a new password. This link expires in 30 minutes:</p>
        <a href="{reset_link}" style="background-color: #238636; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px; display: inline-block; font-weight: bold; margin-top: 10px;">Reset Password</a>
        <p style="margin-top: 20px; font-size: 12px; color: #8b949e;">If you didn't request this, please ignore this email.</p>
    </div>
    """
    background_tasks.add_task(send_email_notification, user.email, "Echo AI - Password Reset Link", email_body)
    return {"message": "If the email exists, a reset link has been sent."}

@app.post("/api/reset-password")
def reset_password(data: ResetPasswordSchema, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(data.token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=400, detail="Invalid token or user")
        
        user.hashed_password = get_password_hash(data.new_password)
        db.commit()
        return {"message": "Password successfully updated. You can now sign in."}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

@app.post("/parse-voice-dump")
def process_dump(dump: DumpSchema, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in to save dumps")

    parsed_result = parse_with_gemini(dump.raw_text)
    saved_text = parsed_result.get("cleaned_text", dump.raw_text)

    db_dump = ThoughtDump(
        user_id=user.id,
        raw_text=saved_text,
        parsed_data=parsed_result
    )
    db.add(db_dump)
    db.commit()
    db.refresh(db_dump)

    return parsed_result

@app.put("/update-dump/{dump_id}")
def update_dump(dump_id: int, dump: DumpSchema, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    db_dump = db.query(ThoughtDump).filter(ThoughtDump.id == dump_id, ThoughtDump.user_id == user.id).first()
    if not db_dump:
        raise HTTPException(status_code=404, detail="Dump entry not found")

    parsed_result = parse_with_gemini(dump.raw_text)
    saved_text = parsed_result.get("cleaned_text", dump.raw_text)

    db_dump.raw_text = saved_text
    db_dump.parsed_data = parsed_result
    db.commit()

    return parsed_result

@app.get("/history")
def get_history(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    dumps = db.query(ThoughtDump).filter(ThoughtDump.user_id == user.id).order_by(ThoughtDump.created_at.desc()).all()
    
    return [
        {
            "id": d.id,
            "raw_text": d.raw_text,
            "parsed_data": d.parsed_data,
            "created_at": d.created_at.replace(tzinfo=timezone.utc).isoformat()
        }
        for d in dumps
    ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)