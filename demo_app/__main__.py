"""Entry point: python -m demo_app.server --port 8000"""
from demo_app.server import app
import argparse
import uvicorn

parser = argparse.ArgumentParser(description="SkyOps server")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument("--host", default="127.0.0.1")
args = parser.parse_args()
uvicorn.run(app, host=args.host, port=args.port)
