from flask import Flask, request
import subprocess

app = Flask(__name__)

@app.route("/deploy", methods=["POST"])
def deploy():
    repo_path = "/root/asterisk-webhook"
    try:
        subprocess.run(["git", "-C", repo_path, "pull"], check=True)
        subprocess.run(["systemctl", "restart", "asterisk_webhook.service"], check=False)
        return "✅ Deployed", 200
    except Exception as e:
        return f"❌ Error: {e}", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002)
