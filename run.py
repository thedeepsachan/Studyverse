import os
import sys
from pathlib import Path
import uvicorn

ROOT_DIR = str(Path(__file__).resolve().parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

if __name__ == "__main__":
    host = os.environ.get("STUDYVERSE_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("STUDYVERSE_PORT", "8000")))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
