# AI Website Audit & Optimization Platform — Backend

A Python-based backend built with FastAPI for an AI-powered website auditing and optimization platform. It provides REST APIs for user authentication, website management, website audits, and AI-generated recommendations.

## 🚀 Features

- **User Authentication** — Login and authentication endpoints.
- **Website Management** — Manage websites submitted for auditing.
- **Website Auditing** — API endpoints for website audit operations.
- **AI Recommendations** — Generate actionable recommendations using AI integrations.
- **Admin APIs** — Administrative endpoints for platform management.
- **Database Integration** — Database access through Python.
- **REST API** — FastAPI endpoints with automatic API documentation.

## 🛠️ Tech Stack

- **Language:** Python
- **Backend Framework:** FastAPI
- **Database:** PostgreSQL via Supabase (planned/configuration-dependent)
- **ORM / Database Access:** SQLAlchemy or the database library configured in the project
- **API Server:** Uvicorn
- **AI Integration:** Groq (if configured in the project)

## 📂 Project Structure

```text
ai-website-audit-backend/
├── dependencies/
│   └── auth.py
├── models/
│   ├── audit.py
│   ├── user.py
│   └── website.py
├── routers/
│   ├── admin.py
│   ├── audits.py
│   ├── auth.py
│   └── websites.py
├── schemas/
├── services/
│   └── ai_recommendations.py
├── database.py
├── main.py
├── requirements.txt
└── README.md
```

## ⚙️ Getting Started

### Prerequisites

- Python 3.11 or a compatible version supported by the dependencies
- pip
- A configured database
- Required API keys for any enabled AI integrations

### 1. Clone the repository

```bash
git clone https://github.com/zakriyawahid83-crypto/ai-website-audit-backend.git
cd ai-website-audit-backend
```

### 2. Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root and add the variables required by your application.

Example:

```env
DATABASE_URL=your_database_connection_string
GROQ_API_KEY=your_groq_api_key
```

Replace the example values with your actual credentials. Never commit `.env` or API keys to GitHub.

### 5. Run the application

```bash
uvicorn main:app --reload
```

The local API will usually be available at:

- API: `http://127.0.0.1:8000`
- Interactive Swagger documentation: `http://127.0.0.1:8000/docs`

## 📡 API Endpoints

The application organizes API routes into the following modules:

- **Authentication:** `routers/auth.py`
- **Websites:** `routers/websites.py`
- **Audits:** `routers/audits.py`
- **Administration:** `routers/admin.py`

Visit `/docs` while the backend is running to see the actual registered endpoints and their request formats.

## ☁️ Deployment

The planned deployment architecture is:

- **Backend:** Railway
- **Database:** Supabase PostgreSQL
- **Frontend:** Vercel

Configure database credentials, API keys, and other required environment variables in Railway's service settings. Configure CORS to allow requests from the deployed frontend.

## 🔐 Security

- Keep API keys and database credentials in environment variables.
- Never commit `.env`, virtual environments, or local database files.
- Use secure authentication and authorization.
- Restrict CORS to trusted frontend origins in production.

## 👨‍💻 Author

**Zakriya Wahid**

GitHub: [@zakriyawahid83-crypto](https://github.com/zakriyawahid83-crypto)

---

*AI Website Audit & Optimization Platform — helping identify website issues and improve website quality.*
