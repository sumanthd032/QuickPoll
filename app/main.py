import os
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# --- App Configuration ---

# Create the FastAPI app instance
app = FastAPI(
    title="QuickPoll API",
    description="Backend API for the QuickPoll application.",
    version="1.0.0"
)

# --- Static Files ---

# Get the absolute path to the 'static' directory
# This is a robust way to ensure it finds the files regardless of where you run the server from
static_dir = os.path.join(os.path.dirname(__file__), '..', 'static')

# Mount the 'static' directory to serve our HTML, CSS, and JS files
# The path "/static" is the URL path, not the directory name.
# We will serve the root pages separately.
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# --- API Endpoints ---

@app.get("/api/health", tags=["Status"])
async def health_check():
    """
    A simple endpoint to check if the API is running.
    """
    return {"status": "ok", "message": "API is running smoothly"}

# --- Frontend Serving ---

@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_home(request: Request):
    """
    Serves the main poll creation page (index.html).
    """
    return FileResponse(os.path.join(static_dir, 'index.html'))

@app.get("/poll.html", response_class=HTMLResponse, tags=["Frontend"])
async def serve_poll_page(request: Request):
    """
    Serves the poll voting/results page (poll.html).
    """
    # This endpoint allows direct navigation to poll.html for now.
    # Later, it will be something like /polls/{poll_id}
    return FileResponse(os.path.join(static_dir, 'poll.html'))
