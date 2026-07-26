from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Bumped by hand on real changes so a stale HF Space build is visible
# immediately in /api/health rather than assumed fixed — a lesson learned
# the hard way on an earlier project (see [[skillcompass-flagship-project]]).
VERSION = "0.1.0"

app = FastAPI(title="Leeway API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://lyhjeremy.github.io",
        "http://localhost:5173",
        "http://localhost:4173",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": VERSION}
