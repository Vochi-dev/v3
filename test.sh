#!/bin/bash

# Call Tester Service Management Script
APP_MODULE="call_tester:app"
PID_FILE=".uvicorn_test.pid"
LOG_FILE="logs/test.log"
HOST="0.0.0.0"
PORT="8025"

start() {
    if [ -f $PID_FILE ]; then
        if kill -0 $(cat $PID_FILE) 2>/dev/null; then
            echo "⚠️  Call Tester service is already running (PID: $(cat $PID_FILE))"
            return 1
        else
            rm -f $PID_FILE
        fi
    fi

    if [ ! -f "call_tester.py" ]; then
        echo "❌ call_tester.py not found in current directory"
        return 1
    fi

    mkdir -p logs
    echo "🚀 Starting Call Tester service..."
    setsid nohup uvicorn call_tester:app --host $HOST --port $PORT > $LOG_FILE 2>&1 &
    PID=$!
    echo $PID > $PID_FILE
    sleep 2
    
    if netstat -tlnp | grep -q ":$PORT"; then
        echo "✅ Call Tester service started successfully (PID: $PID)"
        echo "📡 URL: http://localhost:$PORT"
        echo "📋 Logs: tail -f $LOG_FILE"
    else
        echo "❌ Failed to start Call Tester service"
        rm -f $PID_FILE
        return 1
    fi
}

stop() {
    if [ ! -f $PID_FILE ]; then
        echo "⚠️  Call Tester service is not running"
        return 1
    fi

    PID=$(cat $PID_FILE)
    echo "🛑 Stopping Call Tester service (PID: $PID)..."
    
    if kill -0 $PID 2>/dev/null; then
        kill $PID
        sleep 3
        
        if kill -0 $PID 2>/dev/null; then
            echo "⚠️  Process still running, force killing..."
            kill -9 $PID
            sleep 1
        fi
    fi
    
    rm -f $PID_FILE
    echo "✅ Call Tester service stopped"
}

restart() {
    echo "🔄 Restarting Call Tester service..."
    stop
    sleep 2
    start
}

status() {
    if [ -f $PID_FILE ]; then
        PID=$(cat $PID_FILE)
        if kill -0 $PID 2>/dev/null; then
            echo "✅ Call Tester service is running (PID: $PID)"
            echo "📡 URL: http://localhost:$PORT"
            echo "📋 Logs: tail -f $LOG_FILE"
            return 0
        else
            echo "❌ Call Tester service is not running (stale PID file)"
            rm -f $PID_FILE
            return 1
        fi
    else
        echo "❌ Call Tester service is not running"
        return 1
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo "  start   - Start the Call Tester service"
        echo "  stop    - Stop the Call Tester service"
        echo "  restart - Restart the Call Tester service"
        echo "  status  - Check service status"
        exit 1
        ;;
esac

exit $?