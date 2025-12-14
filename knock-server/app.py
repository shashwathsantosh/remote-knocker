from flask import Flask, jsonify, request, render_template
import datetime
import time
import uuid

app = Flask(__name__)

# --- IN-MEMORY DATABASE ---
# Format: { "MAC_ADDR": { "last_seen": timestamp, "status": "online", "knocks": 0, "angle": 115 } }
devices = {}
knock_queue = []
job_history = {}
system_logs = []

def log_event(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    system_logs.insert(0, f"[{timestamp}] {message}")
    if len(system_logs) > 50: system_logs.pop()

@app.route('/')
def index():
    now = time.time()
    online_count = sum(1 for d in devices.values() if (now - d['last_seen']) < 10)
    return render_template('index.html', online_count=online_count)

@app.route('/admin')
def admin():
    now = time.time()
    display_devices = {}
    for mac, dev in devices.items():
        seconds_ago = int(now - dev['last_seen'])
        is_online = seconds_ago < 10 
        # Ensure angle exists (default 115 if missing)
        angle = dev.get('angle', 115)
        display_devices[mac] = {**dev, "seconds_ago": seconds_ago, "is_online": is_online, "angle": angle}
    
    return render_template('admin.html', devices=display_devices, logs=system_logs)

# --- API ---

@app.route('/api/update-settings', methods=['POST'])
def update_settings():
    data = request.json
    mac = data.get('id')
    new_angle = data.get('angle')
    
    if mac in devices:
        try:
            devices[mac]['angle'] = int(new_angle)
            log_event(f"⚙️ Updated {mac} angle to {new_angle}")
            return jsonify({"status": "ok"})
        except:
            return jsonify({"status": "error"}), 400
    return jsonify({"status": "device not found"}), 404

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
    
    # 1. Update/Create Device Registry
    if mac not in devices:
        log_event(f"✨ New Device Joined: {mac}")
        # Default angle is 115 if new
        devices[mac] = { "knocks": 0, "last_seen": time.time(), "status": "online", "angle": 115 }
    else:
        devices[mac]['last_seen'] = time.time()
        devices[mac]['status'] = 'online'
        # Ensure angle key exists for older records
        if 'angle' not in devices[mac]: devices[mac]['angle'] = 115

    # Get the specific angle for this device
    current_angle = devices[mac]['angle']
    
    # 2. Check for work
    job_to_assign = None
    for i, job in enumerate(knock_queue):
        if job['target'] is None or job['target'] == mac:
            job_to_assign = knock_queue.pop(i)
            break
            
    if job_to_assign:
        job_history[job_to_assign['id']]['status'] = 'assigned'
        job_history[job_to_assign['id']]['worker'] = mac
        log_event(f"🔧 Dispatching Job {job_to_assign['id']} to {mac}")
        return jsonify({
            "command": "KNOCK", 
            "job_id": job_to_assign['id'],
            "angle": current_angle # <--- SENDING ANGLE
        })
    
    return jsonify({
        "command": "SLEEP",
        "angle": current_angle # <--- SENDING ANGLE EVEN WHILE SLEEPING
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)