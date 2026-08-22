import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD") == "1",
        limit_concurrency=int(os.getenv("UVICORN_LIMIT_CONCURRENCY", "200")),
        backlog=int(os.getenv("UVICORN_BACKLOG", "512")),
        timeout_keep_alive=int(os.getenv("UVICORN_KEEP_ALIVE_SECONDS", "10")),
    )
