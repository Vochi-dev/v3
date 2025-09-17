#!/bin/bash
cd /root/asterisk-webhook

echo "=== Testing basic commands ==="
echo "Current directory: $(pwd)"
echo "Python version: $(python3 --version)"
echo "FastAPI available: $(python3 -c 'import fastapi; print("OK")' 2>/dev/null || echo "FAIL")"

echo "=== Testing simple service ==="
timeout 3 python3 test_minimal.py > test_output.log 2>&1 &
SERVICE_PID=$!
sleep 1

echo "Service PID: $SERVICE_PID"
ps aux | grep test_minimal | grep -v grep

sleep 2
kill $SERVICE_PID 2>/dev/null

echo "=== Log output ==="
cat test_output.log 2>/dev/null || echo "No log created"

echo "=== Done ==="
