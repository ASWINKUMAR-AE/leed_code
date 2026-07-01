import os
import queue
import threading
import asyncio
from flask import Flask, render_template, jsonify, request, Response
from dotenv import load_dotenv
from bot import run_leetcode_solver

load_dotenv()

app = Flask(__name__, template_folder='templates')

# Thread-safe log queue
log_queue = queue.Queue()

# Global bot runner state
bot_thread = None
bot_loop = None
bot_state = {
    "running": False,
    "stop_requested": False
}

def log_message(msg):
    """Logs a message to the console and adds it to the queue for SSE streaming."""
    print(msg)
    log_queue.put(msg)

def save_env_file(config_data):
    """Saves the config dictionary into the .env file."""
    env_lines = []
    # Read existing lines if .env exists, to preserve other configs
    existing_config = {}
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2:
                        existing_config[parts[0]] = parts[1]
                        
    # Update with new values
    for k, v in config_data.items():
        existing_config[k] = v
        
    # Write back to .env
    with open(".env", "w") as f:
        for k, v in existing_config.items():
            f.write(f"{k}={v}\n")

@app.route('/')
def index():
    # Load configuration from .env if available
    config = {
        "email": os.getenv("LEETCODE_EMAIL", ""),
        "password": os.getenv("LEETCODE_PASSWORD", ""),
        "openrouter_key": os.getenv("OPENROUTER_API_KEY", ""),
        "gemini_key": os.getenv("GEMINI_API_KEY", ""),
        "provider": os.getenv("API_PROVIDER", "gemini"),
        "model": os.getenv("API_MODEL", "gemini-2.5-flash"),
        "difficulty": os.getenv("LEETCODE_DIFFICULTY", "EASY"),
        "topic": os.getenv("LEETCODE_TOPIC", "algorithms"),
        "typing_delay": os.getenv("TYPING_DELAY_MS", "10")
    }
    return render_template('index.html', config=config)

@app.route('/api/config', methods=['POST'])
def save_config():
    data = request.json
    env_updates = {}
    if "email" in data:
        env_updates["LEETCODE_EMAIL"] = data["email"]
    if "password" in data:
        env_updates["LEETCODE_PASSWORD"] = data["password"]
    if "openrouter_key" in data:
        env_updates["OPENROUTER_API_KEY"] = data["openrouter_key"]
    if "gemini_key" in data:
        env_updates["GEMINI_API_KEY"] = data["gemini_key"]
    if "provider" in data:
        env_updates["API_PROVIDER"] = data["provider"]
    if "model" in data:
        env_updates["API_MODEL"] = data["model"]
    if "difficulty" in data:
        env_updates["LEETCODE_DIFFICULTY"] = data["difficulty"]
    if "topic" in data:
        env_updates["LEETCODE_TOPIC"] = data["topic"]
    if "typing_delay" in data:
        env_updates["TYPING_DELAY_MS"] = data["typing_delay"]
        
    try:
        save_env_file(env_updates)
        return jsonify({"success": True, "message": "Configuration saved to .env file."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/start', methods=['POST'])
def start_bot():
    global bot_thread, bot_loop, bot_state
    if bot_state["running"]:
        return jsonify({"success": False, "error": "Bot is already running."}), 400
        
    data = request.json
    
    # Reset state
    bot_state["running"] = True
    bot_state["stop_requested"] = False
    
    # Flush old logs from queue
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except queue.Empty:
            break
            
    # Gather config
    email = data.get("email") or os.getenv("LEETCODE_EMAIL")
    password = data.get("password") or os.getenv("LEETCODE_PASSWORD")
    provider = data.get("provider", "gemini")
    model = data.get("model")
    
    # Retrieve key based on provider
    if provider == "openrouter":
        api_key = data.get("openrouter_key") or os.getenv("OPENROUTER_API_KEY")
    else:
        api_key = data.get("gemini_key") or os.getenv("GEMINI_API_KEY")
        
    difficulty = data.get("difficulty", "EASY")
    topic = data.get("topic", "algorithms")
    typing_delay = data.get("typing_delay", "10")
    
    if not email or not password:
        bot_state["running"] = False
        return jsonify({"success": False, "error": "LeetCode Email and Password are required."}), 400
        
    if not api_key:
        bot_state["running"] = False
        return jsonify({"success": False, "error": "API Key is required for the selected provider."}), 400
        
    bot_config = {
        "email": email,
        "password": password,
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "difficulty": difficulty,
        "topic": topic,
        "typing_delay": typing_delay
    }
    
    log_message("[System] Booting program...")
    
    def thread_target():
        global bot_loop, bot_state
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)
        try:
            bot_loop.run_until_complete(run_leetcode_solver(bot_config, log_message, bot_state))
        except Exception as e:
            log_message(f"[System Error] Unexpected error in bot execution: {str(e)}")
        finally:
            bot_loop.close()
            bot_state["running"] = False
            log_message("[System] Bot process stopped.")
            
    bot_thread = threading.Thread(target=thread_target, daemon=True)
    bot_thread.start()
    
    return jsonify({"success": True, "message": "Bot thread initiated."})

@app.route('/api/stop', methods=['POST'])
def stop_bot():
    global bot_state
    if not bot_state["running"]:
        return jsonify({"success": False, "error": "Bot is not running."}), 400
        
    log_message("[System] Requesting bot shutdown...")
    bot_state["stop_requested"] = True
    return jsonify({"success": True, "message": "Stop signal sent to bot."})

@app.route('/api/status')
def get_status():
    return jsonify({"running": bot_state["running"]})

@app.route('/stream')
def stream_logs():
    def event_stream():
        yield "data: [Console] Connected to log stream...\n\n"
        while True:
            try:
                # Poll queue for log messages
                msg = log_queue.get(timeout=2.0)
                yield f"data: {msg}\n\n"
            except queue.Empty:
                # Send keep-alive comment
                yield ": keep-alive\n\n"
            except Exception as e:
                break
    return Response(event_stream(), mimetype="text/event-stream")

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)
