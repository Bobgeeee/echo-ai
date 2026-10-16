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
import bcrypt
import resend
from groq import Groq

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./echo_ai.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

APP_DOMAIN = os.getenv("APP_DOMAIN", "echoai.buzz")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "support@echoai.buzz")
resend.api_key = RESEND_API_KEY

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
groq_client = Groq(api_key=GROQ_API_KEY)

app = FastAPI(title="Echo AI Engine")
app.mount("/static", StaticFiles(directory="static"), name="static")

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

try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"Database error: {e}")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

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
        email = payload.get("sub")
        if email is None:
            return None
        return db.query(User).filter(User.email == email).first()
    except Exception:
        return None

def send_email_notification(recipient_email: str, subject: str, body_content: str):
    if not RESEND_API_KEY:
        return
    try:
        resend.Emails.send({
            "from": f"Echo AI <{SENDER_EMAIL}>",
            "to": [recipient_email],
            "subject": subject,
            "html": body_content,
        })
    except Exception as e:
        print(f"Resend error: {e}")

SYSTEM_PROMPT = """
You are the intelligence engine for Echo AI. Analyze the user's input and strictly split mixed thoughts across all three categories simultaneously:
1. "calendar_events": For scheduled meetings, calls, appointments, or time-specific commitments. Must include keys: "title", "day_or_date", "time".
2. "tasks": For actionable to-do items, errands, or operational items without a strict meeting time. Must include keys: "task", "priority", "due_date_mention".
3. "ideas_and_notes": For passive concepts, brainstorms, feature ideas, or general notes. Must include keys: "category", "summary".

Return ONLY a valid JSON object matching this exact schema:
{
  "cleaned_text": "Polished text with perfect punctuation.",
  "calendar_events": [{"title": "Event title", "day_or_date": "Today", "time": "12:30 PM"}],
  "tasks": [{"task": "Task description", "priority": "High", "due_date_mention": "Today"}],
  "ideas_and_notes": [{"category": "General Thought", "summary": "Brief summary"}]
}
"""

def parse_with_gemini(raw_text: str) -> dict:
    try:
        completion = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + "\nReturn ONLY valid JSON."},
                {"role": "user", "content": raw_text}
            ],
            response_format={"type": "json_object"}
        )
        text_res = completion.choices[0].message.content.strip()
        parsed_data = json.loads(text_res)
        
        return {
            "cleaned_text": parsed_data.get("cleaned_text", raw_text),
            "calendar_events": parsed_data.get("calendar_events", []),
            "tasks": parsed_data.get("tasks", []),
            "ideas_and_notes": parsed_data.get("ideas_and_notes", [])
        }
    except Exception as e:
        print(f"AI parsing error: {e}")
        return {
            "cleaned_text": raw_text,
            "calendar_events": [],
            "tasks": [],
            "ideas_and_notes": []
        }

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
def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/reset-password", response_class=HTMLResponse)
def read_reset_password():
    with open("reset_password.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.post("/api/register")
def register(user_data: RegisterSchema, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if len(user_data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")
    if user_data.password != user_data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="This email is already registered. Please sign in.")

    new_user = User(email=user_data.email, hashed_password=get_password_hash(user_data.password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    email_body = f"<p>Welcome to Echo AI, {user_data.email}!</p>"
    background_tasks.add_task(send_email_notification, user_data.email, "Welcome to Echo AI", email_body)

    token = create_access_token({"sub": new_user.email})
    response = JSONResponse(content={"message": "Account created successfully"})
    response.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True)
    return response

@app.post("/api/login")
def login(user_data: LoginSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

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

    reset_token = create_access_token({"sub": user.email}, expires_delta=timedelta(minutes=30))
    reset_link = f"https://{APP_DOMAIN}/reset-password?token={reset_token}"
    email_body = f"<p>Click <a href='{reset_link}'>here</a> to reset your password. Valid for 30 minutes.</p>"
    background_tasks.add_task(send_email_notification, user.email, "Echo AI - Password Reset", email_body)
    return {"message": "If the email exists, a reset link has been sent."}

@app.post("/api/reset-password-confirm")
def reset_password_confirm(data: ResetPasswordSchema, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(data.token, SECRET_KEY, algorithms=[ALGORITHM])
        user = db.query(User).filter(User.email == payload.get("sub")).first()
        if not user:
            raise HTTPException(status_code=400, detail="Invalid user")
        
        user.hashed_password = get_password_hash(data.new_password)
        db.commit()
        return {"message": "Password successfully updated."}
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

@app.post("/parse-voice-dump")
def process_dump(dump: DumpSchema, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    parsed_result = parse_with_gemini(dump.raw_text)
    db_dump = ThoughtDump(user_id=user.id, raw_text=parsed_result.get("cleaned_text", dump.raw_text), parsed_data=parsed_result)
    db.add(db_dump)
    db.commit()
    return parsed_result

@app.get("/history")
def get_history(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    dumps = db.query(ThoughtDump).filter(ThoughtDump.user_id == user.id).order_by(ThoughtDump.created_at.desc()).all()
    return [{"id": d.id, "raw_text": d.raw_text, "parsed_data": d.parsed_data, "created_at": d.created_at.replace(tzinfo=timezone.utc).isoformat()} for d in dumps]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))