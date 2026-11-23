import os

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")

import uvicorn

from app.config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run("app.server:app", host=HOST, port=PORT, log_level="info")
