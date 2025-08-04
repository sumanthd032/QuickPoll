import os
import uuid
import datetime
import asyncio
import json
import io
import csv
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from threading import Event

# --- Firebase Admin SDK ---
import firebase_admin
from firebase_admin import credentials, firestore

# --- App Configuration ---
app = FastAPI(
    title="QuickPoll API",
    description="Backend API for the QuickPoll application.",
    version="1.0.0"
)

# --- Firebase Initialization ---
try:
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firestore connection successful.")
except Exception as e:
    print(f"🔥 Firestore connection failed: {e}")
    db = None

# --- Static Files ---
static_dir = os.path.join(os.path.dirname(__file__), '..', 'static')
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# --- Pydantic Models ---
class PollCreate(BaseModel):
    question: str = Field(..., min_length=3, max_length=200)
    options: List[str] = Field(..., min_items=2, max_items=10)
    expiry: str

class PollResponse(BaseModel):
    id: str
    question: str
    options: List[Dict[str, str]]
    created_at: datetime.datetime
    is_expired: bool = False

class PollData(PollResponse):
    results: Dict[str, int]
    user_voted: Optional[str] = None

class VoteRequest(BaseModel):
    option_id: str


# --- Helper Functions ---
def get_client_ip(request: Request) -> str:
    x_forwarded_for = request.headers.get('x-forwarded-for')
    if x_forwarded_for: return x_forwarded_for.split(',')[0]
    return request.client.host

def is_poll_expired(poll_data: dict) -> bool:
    duration_str = poll_data.get("expiry_duration")
    if not duration_str or duration_str == "never": return False
    created_at = poll_data.get("created_at")
    if not isinstance(created_at, datetime.datetime):
        created_at = datetime.datetime.fromisoformat(str(created_at))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
    delta = None
    if duration_str.endswith('m'): delta = datetime.timedelta(minutes=int(duration_str[:-1]))
    elif duration_str.endswith('h'): delta = datetime.timedelta(hours=int(duration_str[:-1]))
    elif duration_str.endswith('d'): delta = datetime.timedelta(days=int(duration_str[:-1]))
    if delta: return datetime.datetime.now(datetime.timezone.utc) > created_at + delta
    return False

# --- API Endpoints ---

@app.post("/api/polls", response_model=PollResponse, status_code=status.HTTP_201_CREATED, tags=["Polls"])
async def create_poll(poll_data: PollCreate):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_id = str(uuid.uuid4())[:8]
    options_with_ids = [{"id": f"opt_{i+1}", "text": text} for i, text in enumerate(poll_data.options)]
    results = {option["id"]: 0 for option in options_with_ids}
    poll_record = {"id": poll_id, "question": poll_data.question, "options": options_with_ids, "created_at": datetime.datetime.now(datetime.timezone.utc), "expiry_duration": poll_data.expiry, "results": results, "voter_ips": []}
    try:
        db.collection('polls').document(poll_id).set(poll_record)
        return PollResponse(**poll_record)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create poll: {e}")

@app.get("/api/polls/{poll_id}", response_model=PollData, tags=["Polls"])
async def get_poll_data(poll_id: str, request: Request):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_ref = db.collection('polls').document(poll_id)
    poll_doc = poll_ref.get()
    if not poll_doc.exists: raise HTTPException(status_code=404, detail="Poll not found")
    poll_data = poll_doc.to_dict()
    poll_data["is_expired"] = is_poll_expired(poll_data)
    client_ip = get_client_ip(request)
    if client_ip in poll_data.get("voter_ips", []):
        poll_data["user_voted"] = "yes"
    return PollData(**poll_data)

@app.post("/api/polls/{poll_id}/vote", status_code=status.HTTP_200_OK, tags=["Polls"])
async def cast_vote(poll_id: str, vote: VoteRequest, request: Request):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    poll_ref = db.collection('polls').document(poll_id)
    client_ip = get_client_ip(request)
    @firestore.transactional
    def vote_transaction(transaction, poll_ref, option_id, client_ip):
        snapshot = poll_ref.get(transaction=transaction)
        if not snapshot.exists: raise HTTPException(status_code=404, detail="Poll not found")
        poll_data = snapshot.to_dict()
        if is_poll_expired(poll_data): raise HTTPException(status_code=403, detail="This poll has expired.")
        if client_ip in poll_data.get("voter_ips", []): return {"message": "You have already voted."}
        if option_id not in poll_data.get("results", {}): raise HTTPException(status_code=400, detail="Invalid option ID")
        transaction.update(poll_ref, {f'results.{option_id}': firestore.Increment(1), 'voter_ips': firestore.ArrayUnion([client_ip])})
        return {"message": "Vote cast successfully."}
    transaction = db.transaction()
    return vote_transaction(transaction, poll_ref, vote.option_id, client_ip)

@app.get("/api/polls/{poll_id}/stream", tags=["Polls"])
async def stream_poll_results(poll_id: str, request: Request):
    if not db: raise HTTPException(status_code=503, detail="Firestore service is not available.")
    queue = asyncio.Queue()
    stop_event = Event()
    def on_snapshot_callback(doc_snapshot, changes, read_time):
        for doc in doc_snapshot:
            if doc.exists: queue.put_nowait(doc.to_dict())
    poll_ref = db.collection("polls").document(poll_id)
    listener = poll_ref.on_snapshot(on_snapshot_callback)
    async def event_generator():
        try:
            while not await request.is_disconnected():
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30) 
                    data["is_expired"] = is_poll_expired(data)
                    json_data = json.dumps(data, default=str)
                    yield f"data: {json_data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            print(f"Client disconnected from poll {poll_id}. Cleaning up listener.")
            listener.unsubscribe()
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# --- NEW: CSV Export Endpoint ---
@app.get("/api/polls/{poll_id}/export", tags=["Polls"])
async def export_poll_results(poll_id: str):
    """Exports poll results to a CSV file."""
    if not db:
        raise HTTPException(status_code=503, detail="Firestore service is not available.")
    
    poll_ref = db.collection('polls').document(poll_id)
    poll_doc = poll_ref.get()

    if not poll_doc.exists:
        raise HTTPException(status_code=404, detail="Poll not found")

    poll_data = poll_doc.to_dict()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow(['Question', 'Option', 'Votes'])
    
    # Write data rows
    question = poll_data.get('question')
    results = poll_data.get('results', {})
    options_map = {opt['id']: opt['text'] for opt in poll_data.get('options', [])}
    
    for option_id, vote_count in results.items():
        option_text = options_map.get(option_id, 'Unknown Option')
        writer.writerow([question, option_text, vote_count])
        
    output.seek(0)
    
    # Create a StreamingResponse to send the CSV file
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=poll_results_{poll_id}.csv"}
    )

@app.get("/api/health", tags=["Status"])
async def health_check():
    return {"status": "ok", "message": "API is running smoothly"}

# --- Frontend Serving ---
@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_home():
    return FileResponse(os.path.join(static_dir, 'index.html'))

@app.get("/polls/{poll_id}", response_class=HTMLResponse, tags=["Frontend"])
async def serve_poll_page(poll_id: str):
    return FileResponse(os.path.join(static_dir, 'poll.html'))
