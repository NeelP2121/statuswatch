import asyncio
import aiohttp
from aiohttp import web
import json
from datetime import datetime
import os

class StatusTracker:
    def __init__(self, polling_interval=60, config_file="config.json"):
        # Load sources from config file
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                self.sources = config.get("sources", [])
        except FileNotFoundError:
            print(f"Warning: {config_file} not found. Using empty sources list.")
            self.sources = []
            
        self.polling_interval = polling_interval
        
        # State tracking
        self.component_states = {}
        self.incident_states = {}
        self.known_incident_updates = set()
        
        # New: Store recent logs in memory to display on the web!
        self.recent_logs = []

    def add_log(self, message):
        """Helper to print to console AND store in our recent_logs list"""
        print(message)
        self.recent_logs.insert(0, message) # Add to the top
        # Keep only the last 50 logs so we don't run out of memory
        if len(self.recent_logs) > 50:
            self.recent_logs.pop()

    def record_event(self, product, status, timestamp=None):
        if not timestamp:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        else:
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
                
        log_message = f"[{timestamp}] Product: {product} | Status: {status}"
        self.add_log(log_message)

    async def fetch_status(self, session, url):
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            self.add_log(f"Error fetching {url}: {e}")
        return None

    def process_data(self, source_name, data):
        if not self.initialized and "status" in data:
            desc = data["status"].get("description", "Unknown")
            indicator = data["status"].get("indicator", "none")
            self.add_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {source_name} Current Status: {desc} (Indicator: {indicator})")

        if "components" in data:
            for comp in data["components"]:
                comp_id = comp["id"]
                comp_name = comp["name"]
                status = comp["status"]
                
                if comp_id in self.component_states:
                    prev_status = self.component_states[comp_id]
                    if prev_status != status:
                        self.record_event(
                            product=f"{source_name} - {comp_name}",
                            status=f"Changed from '{prev_status}' to '{status}'",
                            timestamp=comp.get("updated_at")
                        )
                self.component_states[comp_id] = status
                
        if "incidents" in data:
            for incident in data["incidents"]:
                inc_id = incident["id"]
                inc_name = incident["name"]
                
                updates = incident.get("incident_updates", [])
                for update in updates:
                    update_id = update["id"]
                    if update_id not in self.known_incident_updates:
                        self.known_incident_updates.add(update_id)
                        if self.initialized:
                            body = update.get("body", "")
                            status_val = update.get("status", "updated")
                            
                            # Often the body is an empty string, so display the status step instead
                            display_text = body if body else f"Status updated to: {status_val}"
                            
                            self.record_event(
                                product=f"{source_name} Incident: {inc_name}",
                                status=display_text,
                                timestamp=update.get("created_at")
                            )
                
                self.incident_states[inc_id] = incident.get("updated_at")

    async def track_loop(self):
        """The background task that polls for status updates constantly"""
        self.add_log(f"Starting async Status Tracker (interval: {self.polling_interval}s)...")
        self.initialized = False
        
        async with aiohttp.ClientSession() as session:
            while True:
                tasks = [self.fetch_status(session, source["url"]) for source in self.sources]
                results = await asyncio.gather(*tasks)
                
                for source, data in zip(self.sources, results):
                    if data:
                        self.process_data(source["name"], data)
                        
                self.initialized = True
                await asyncio.sleep(self.polling_interval)

    async def handle_web_request(self, request):
        """Serves the logs as a simple HTML page"""
        html = "<html><head><title>Status Tracker Logs</title></head><body style='font-family: monospace;'>"
        html += "<h2>Recent Status Events</h2>"
        html += "<ul>"
        for log in self.recent_logs:
            html += f"<li>{log}</li>"
        html += "</ul></body></html>"
        return web.Response(text=html, content_type='text/html')

async def main():
    tracker = StatusTracker(polling_interval=30)
    
    # 1. Setup Web Server
    app = web.Application()
    app.router.add_get('/', tracker.handle_web_request)
    
    # Use Railway's provided PORT or default to 8080
    port = int(os.environ.get('PORT', 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    tracker.add_log(f"Web server started on port {port}...")

    # 2. Start the tracking loop concurrently
    await tracker.track_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTracker stopped.")
