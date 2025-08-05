# QuickPoll – Instant One-Question Poll Creator

QuickPoll is a real-time, one-question polling application designed for simplicity and speed. It allows users to instantly create a poll, share a unique link, and see the results update live as votes are cast. It's built with a modern Python backend using FastAPI and a lightweight vanilla JavaScript frontend.

**Live Demo:** https://quickpoll-live.vercel.app/

## Features

- Instant Poll Creation: Post a question with multiple options through a simple, clean interface.  
- Anonymous Voting: No login or registration is required. Anyone with the link can vote.  
- Real-Time Results: Vote counts update instantly for all viewers using Server-Sent Events (SSE).  
- Shareable Links: Every poll gets a unique, short, and shareable URL.  
- IP-Based Vote Uniqueness: Prevents duplicate votes from the same IP address to maintain poll integrity.  
- Poll Expiration: Set an optional expiration time for polls (e.g., 5 minutes, 1 hour, 1 day).  
- Export Results: Download poll results as a CSV file for easy analysis.  
- Live Classroom Quiz Mode: A special mode for presenters where results are hidden from participants until the host clicks a "Reveal" button on a private control panel.

## Technology Stack

### Backend:
- **Framework:** FastAPI  
- **Database:** Google Firestore  
- **Language:** Python 3.10+

### Frontend:
- **Markup & Styling:** HTML5, Tailwind CSS (via CDN)  
- **JavaScript:** Vanilla JS (ES6+)

## Setup and Local Development

### Prerequisites
- Python 3.10+
- A Google Firebase project with Firestore enabled

### 1. Clone the Repository

```bash
git clone https://github.com/sumanthd032/QuickPoll.git
cd quickpoll
```

### 2. Set Up a Virtual Environment

```bash
python -m venv venv

# Activate it
# On Windows:
venv\Scripts\activate

# On macOS/Linux:
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Firebase Credentials

Go to your Firebase Console → Project Settings → Service accounts.  
Click "Generate new private key" to download a JSON file.  
Create a `.env` file in the root directory with the following format:

```
FIREBASE_TYPE="service_account"
FIREBASE_PROJECT_ID="your-project-id"
FIREBASE_PRIVATE_KEY_ID="your-private-key-id"
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nYOUR-KEY\n-----END PRIVATE KEY-----\n"
FIREBASE_CLIENT_EMAIL="your-client-email@your-project-id.iam.gserviceaccount.com"
FIREBASE_CLIENT_ID="your-client-id"
FIREBASE_AUTH_URI="https://accounts.google.com/o/oauth2/auth"
FIREBASE_TOKEN_URI="https://oauth2.googleapis.com/token"
FIREBASE_AUTH_PROVIDER_X509_CERT_URL="https://www.googleapis.com/oauth2/v1/certs"
FIREBASE_CLIENT_X509_CERT_URL="your-client-x509-cert-url"
```

### 5. Run the Server

```bash
uvicorn app.main:app --reload
```

Visit [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.
