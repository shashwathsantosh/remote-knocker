from flask import Flask, jsonify, request, render_template_string
import datetime
import time
import uuid

app = Flask(__name__)

# --- IN-MEMORY DATABASE ---
# 1. Devices Registry: Tracks online status of every ESP32
# Format: { "MAC_ADDR": { "last_seen": timestamp, "status": "online", "knocks": 0 } }
devices = {}

# 2. Job Queue: Pending knocks waiting for a worker
# Format: [{ "id": "uuid", "target_device": None (or specific ID), "created_at": time }]
knock_queue = []

# 3. Job History: Tracks status of specific jobs for the UI
# Format: { "uuid": { "status": "queued/assigned/completed", "worker": "MAC_ADDR", "completed_at": time } }
job_history = {}

# 4. System Logs: For the Admin Dashboard feed
system_logs = []

def log_event(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    system_logs.insert(0, f"[{timestamp}] {message}")
    if len(system_logs) > 50: system_logs.pop() # Keep last 50 logs

# --- HTML TEMPLATES ---

PUBLIC_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Remote Knocker</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: 'Segoe UI', sans-serif; text-align: center; background: #f4f4f9; padding: 50px; }
        h1 { color: #333; }
        #btn {
            padding: 25px 50px; font-size: 24px; border: none; border-radius: 50px;
            background: #4CAF50; color: white; cursor: pointer; box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            transition: all 0.2s;
        }
        #btn:active { transform: scale(0.95); }
        #btn:disabled { background: #ccc; cursor: not-allowed; }
        #status { margin-top: 30px; color: #666; font-weight: bold; min-height: 30px; }
        .spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid #666; border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <h1>🪵 Remote Wood Knocker</h1>
    <button id="btn" onclick="sendKnock()">KNOCK ON WOOD</button>
    <div id="status">Ready.</div>

    <script>
        async function sendKnock() {
            const btn = document.getElementById('btn');
            const status = document.getElementById('status');
            
            btn.disabled = true;
            status.innerHTML = 'Queuing knock... <div class="spinner"></div>';
            
            // 1. Request the knock
            try {
                const response = await fetch('/api/queue-knock', { method: 'POST' });
                const data = await response.json();
                const jobId = data.job_id;

                // 2. Poll for completion
                const interval = setInterval(async () => {
                    const check = await fetch(`/api/job-status/${jobId}`);
                    const jobData = await check.json();
                    
                    if (jobData.status === 'assigned') {
                        status.innerHTML = `Dispatched to device <b>${jobData.worker}</b>... <div class="spinner"></div>`;
                    } else if (jobData.status === 'completed') {
                        status.innerHTML = `✅ Knock Delivered by <b>${jobData.worker}</b>!`;
                        clearInterval(interval);
                        btn.disabled = false;
                        
                        // Reset text after 3 seconds
                        setTimeout(() => status.innerHTML = "Ready.", 3000);
                    }
                }, 1000);
            } catch (error) {
                status.innerHTML = "Error connecting to server.";
                btn.disabled = false;
            }
        }
    </script>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Knocker Admin</title>
    <meta http-equiv="refresh" content="2"> <!-- Auto Refresh every 2s -->
    <style>
        body { font-family: monospace; background: #1e1e1e; color: #d4d4d4; padding: 20px; }
        h2 { border-bottom: 1px solid #444; padding-bottom: 10px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .card { background: #252526; padding: 15px; border-radius: 8px; border: 1px solid #333; }
        .online { border-left: 5px solid #4CAF50; }
        .offline { border-left: 5px solid #f44336; opacity: 0.7; }
        table { width: 100%; border-collapse: collapse; background: #252526; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #333; }
        th { background: #333; }
        .btn-test { background: #007acc; color: white; border: none; padding: 5px 10px; cursor: pointer; border-radius: 4px;}
        .btn-test:hover { background: #005f9e; }
    </style>
</head>
<body>
    <h2>📡 Device Fleet</h2>
    <div class="grid">
        {% for mac, dev in devices.items() %}
        <div class="card {{ 'online' if dev.is_online else 'offline' }}">
            <h3>{{ mac }}</h3>
            <p>Status: <b style="color: {{ '#4CAF50' if dev.is_online else '#f44336' }}">{{ 'ONLINE' if dev.is_online else 'OFFLINE' }}</b></p>
            <p>Last Seen: {{ dev.seconds_ago }}s ago</p>
            <p>Knocks Delivered: {{ dev.knocks }}</p>
            {% if dev.is_online %}
                <button class="btn-test" onclick="fetch('/api/queue-knock?target={{ mac }}', {method:'POST'})">FORCE TEST</button>
            {% endif %}
        </div>
        {% else %}
        <p style="color: #666">No devices have connected yet.</p>
        {% endfor %}
    </div>

    <h2>📜 System Logs</h2>
    <table>
        <tr><th>Time / Event</th></tr>
        {% for log in logs %}
        <tr><td>{{ log }}</td></tr>
        {% endfor %}
    </table>
</body>
</html>
"""

# --- ROUTES ---

@app.route('/')
def index():
    return render_template_string(PUBLIC_HTML)

@app.route('/admin')
def admin():
    # Process device status for display
    now = time.time()
    display_devices = {}
    for mac, dev in devices.items():
        seconds_ago = int(now - dev['last_seen'])
        # Consider offline if no heartbeat for 10s
        is_online = seconds_ago < 10
        display_devices[mac] = {**dev, "seconds_ago": seconds_ago, "is_online": is_online}
    
    return render_template_string(ADMIN_HTML, devices=display_devices, logs=system_logs)

# --- API ---

@app.route('/api/queue-knock', methods=['POST'])
def queue_knock():
    target = request.args.get('target') # Optional: Admin can target specific device
    job_id = str(uuid.uuid4())[:8]
    
    # Create Job
    job_history[job_id] = { "status": "queued", "worker": None }
    knock_queue.append({ "id": job_id, "target": target })
    
    if target:
        log_event(f"Admin queued FORCE knock for {target}")
    else:
        log_event(f"User queued knock (Job {job_id})")
        
    return jsonify({"status": "queued", "job_id": job_id})

@app.route('/api/job-status/<job_id>')
def job_status(job_id):
    return jsonify(job_history.get(job_id, {"status": "unknown"}))

@app.route('/api/confirm-knock', methods=['POST'])
def confirm_knock():
    data = request.json
    job_id = data.get('job_id')
    mac = data.get('device_id')
    
    if job_id in job_history:
        job_history[job_id]['status'] = 'completed'
        job_history[job_id]['worker'] = mac
        
        # Increment knock count for the specific device
        if mac in devices:
            devices[mac]['knocks'] += 1
            
        log_event(f"✅ Knock executed by {mac}")
    
    return jsonify({"status": "ok"})

# --- THE HEART OF THE SYSTEM ---
# ESP32 calls this every 2 seconds
@app.route('/api/poll')
def poll():
    mac = request.args.get('id')
    if not mac: return jsonify({"error": "missing_id"}), 400
    
    # 1. Update Registration / Heartbeat
    if mac not in devices:
        log_event(f"✨ New Device Joined: {mac}")
        devices[mac] = { "knocks": 0 }
    
    devices[mac]['last_seen'] = time.time()
    devices[mac]['status'] = 'online'
    
    # 2. Check for work
    # Logic: Look for a job that is either for ANYONE (target=None) or SPECIFICALLY ME
    job_to_assign = None
    
    for i, job in enumerate(knock_queue):
        if job['target'] is None or job['target'] == mac:
            job_to_assign = knock_queue.pop(i)
            break
            
    if job_to_assign:
        job_history[job_to_assign['id']]['status'] = 'assigned'
        job_history[job_to_assign['id']]['worker'] = mac
        log_event(f"🔧 Dispatching Job {job_to_assign['id']} to {mac}")
        return jsonify({"command": "KNOCK", "job_id": job_to_assign['id']})
    
    return jsonify({"command": "SLEEP"})

if __name__ == '__main__':
    # Using Port 5001 to avoid Mac AirPlay conflict
    app.run(host='0.0.0.0', port=5001)
