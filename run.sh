#!/usr/bin/env bash
# run.sh — Start the Competitive Intelligence Radar

set -e

# Check for .env
if [ ! -f ".env" ]; then
  echo "No .env file found. Copying from .env.example..."
  cp .env.example .env
  echo "Please edit .env and add your EXA_API_KEY, then re-run this script."
  exit 1
fi

# Check for venv or install deps
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

echo ""
echo "  Competitive Intelligence Radar"
echo "  --------------------------------"
echo "  Serving at: http://localhost:8000"
echo ""

cd backend && uvicorn main:app --host 0.0.0.0 --port 8000 --reload
