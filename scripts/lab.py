"""Launch the local-only experiment viewer on http://127.0.0.1:8010."""
import uvicorn
from memory_app.lab.app import create_lab


if __name__ == "__main__":
    uvicorn.run(create_lab(), host="127.0.0.1", port=8010)
