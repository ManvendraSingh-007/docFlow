#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

export PYTHONPATH="$DIR"
echo "=========================================================="
echo " Starting DocFlow Engine (Headless PO/Invoice to Webhook) "
echo "=========================================================="
echo "Dashboard UI: http://localhost:8000"
echo "API Docs:     http://localhost:8000/docs"
echo "=========================================================="

exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
