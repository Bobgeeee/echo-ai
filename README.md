# Echo AI

An intelligent, full-stack productivity web application that captures unstructured voice or text brain dumps, automatically cleans up punctuation, and structures them into actionable calendar events, tasks, and ideas using Gemini AI.

---

## 🚀 Key Features
AI-Powered Thought Parsing: Leverages Google Generative AI (gemini-1.5-flash) to parse raw text and voice notes, automatically categorizing them into calendar events, priority tasks, and notes.

Smart Voice-to-Text Transcription: Built-in browser speech recognition allowing users to dictate thoughts hands-free.

Secure Authentication Pipeline: Complete user session management with password hashing (bcrypt), JWT authentication stored in HTTP-only cookies, and secure password reset workflows.

Automated SMTP Email Notifications: Background task worker (BackgroundTasks) that dispatches HTML welcome emails and password reset links via SMTP.

Persistent History & Workspace: Full history tracking allowing users to review, load, and update past brain dumps seamlessly.
Production Deployment:** Hosted on Render with PostgreSQL database management, custom domain configuration (`echoai.buzz`), and SSL encryption.

---

## 🛠️ Tech Stack
Backend & API: Python, FastAPI, Uvicorn, Pydantic, PyJWT, Passlib

AI & NLP: Google Generative AI (google-generativeai)

Database & ORM: SQLAlchemy with SQLite (local) / PostgreSQL (production) support

Frontend: HTML5, CSS3, JavaScript (Vanilla with Responsive GitHub Dark UI theme)

Hosting & Deployment: Render, Custom Domain (echoai.buzz)

---

## 💻 Local Development Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_GITHUB_USERNAME/echo-ai.git](https://github.com/Bobgeeee/echo-ai.git)
   cd echo-ai