from flask import Flask, jsonify, request, render_template
import datetime
import time
import uuid

app = Flask(__name__)

# --- IN-MEMORY DATABASE ---
devices = {}
knock_queue = []
job_history = {}
system_logs = []

def log_event(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    system_logs.insert(0, f"[{timestamp}] {message}")
    if len(system_logs) > 50: system_logs.pop()

# --- ROUTES (Now using the templates folder) ---

@app.route('/')
def index():
    # Calculate how many devices are actually online right now
    now = time.time()
    online_count = sum(1 for d in devices.values() if (now - d['last_seen']) < 10)
    
    # Send this number to the HTML file
    return render_template('index.html', online_count=online_count)

@app.route('/admin')
def admin():
    now = time.time()
    display_devices = {}
    for mac, dev in devices.items():
        seconds_ago = int(now - dev['last_seen'])
        is_online = seconds_ago < 10
        display_devices[mac] = {**dev, "seconds_ago": seconds_ago, "is_online": is_online}
    
    # Pass the data to the admin.html file
    return render_template('admin.html', devices=display_devices, logs=system_logs)

# --- API (Logic remains the same) ---

@app.route('/api/queue-knock', methods=['POST'])
def queue_knock():
    target = request.args.get('target')
    job_id = str(uuid.uuid4())[:8]
    job_history[job_id] = { "status": "queued", "worker": None }
    knock_queue.append({ "id": job_id, "target": target })
    
    if target: log_event(f"Admin queued FORCE knock for {target}")
    else: log_event(f"User queued knock (Job {job_id})")
        
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
        if mac in devices: devices[mac]['knocks'] += 1
        log_event(f"✅ Knock executed by {mac}")
    return jsonify({"status": "ok"})

@app.route('/api/poll')
def poll():
    mac = request.args.get('id')
    if not mac: return jsonify({"error": "missing_id"}), 400
    
    if mac not in devices:
        log_event(f"✨ New Device Joined: {mac}")
        devices[mac] = { "knocks": 0 }
    
    devices[mac]['last_seen'] = time.time()
    devices[mac]['status'] = 'online'
    
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
    app.run(host='0.0.0.0', port=5001)
