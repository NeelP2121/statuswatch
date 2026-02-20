import asyncio
import aiohttp
import json
from datetime import datetime

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
        # For components: component_id -> previous state dict
        self.component_states = {}
        # For incidents: incident_id -> last update timestamp string or list of update ids
        self.incident_states = {}
        self.known_incident_updates = set()

    def print_event(self, product, status, timestamp=None):
        if not timestamp:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        else:
            try:
                # Parse standard ISO format "2023-01-01T12:00:00.000Z"
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
                
        print(f"[{timestamp}] Product: {product}")
        print(f"Status: {status}\n")

    async def fetch_status(self, session, url):
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            print(f"Error fetching {url}: {e}")
        return None

    def process_data(self, source_name, data):
        # 0. Print initial overall status on startup
        if not self.initialized and "status" in data:
            desc = data["status"].get("description", "Unknown")
            indicator = data["status"].get("indicator", "none")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {source_name} Current Status: {desc} (Indicator: {indicator})\n")

        # 1. Check components for status changes
        if "components" in data:
            for comp in data["components"]:
                comp_id = comp["id"]
                comp_name = comp["name"]
                status = comp["status"]
                
                if comp_id in self.component_states:
                    prev_status = self.component_states[comp_id]
                    if prev_status != status:
                        # State changed! Emit event.
                        self.print_event(
                            product=f"{source_name} - {comp_name}",
                            status=f"Status changed from '{prev_status}' to '{status}'",
                            timestamp=comp.get("updated_at")
                        )
                # Update state
                self.component_states[comp_id] = status
                
        # 2. Check active incidents for new updates
        if "incidents" in data:
            for incident in data["incidents"]:
                inc_id = incident["id"]
                inc_name = incident["name"]
                
                updates = incident.get("incident_updates", [])
                for update in updates:
                    update_id = update["id"]
                    if update_id not in self.known_incident_updates:
                        self.known_incident_updates.add(update_id)
                        
                        # Note: We probably don't want to print historical updates on startup
                        # We'll skip printing if this is the first time we're seeing this incident update during initialization
                        if self.initialized:
                            body = update.get("body", "No description")
                            self.print_event(
                                product=f"{source_name} Incident: {inc_name}",
                                status=body,
                                timestamp=update.get("created_at")
                            )
                
                self.incident_states[inc_id] = incident.get("updated_at")

    async def track(self):
        print(f"Starting async Status Tracker (interval: {self.polling_interval}s)...")
        self.initialized = False
        
        async with aiohttp.ClientSession() as session:
            while True:
                tasks = [self.fetch_status(session, source["url"]) for source in self.sources]
                results = await asyncio.gather(*tasks)
                
                for source, data in zip(self.sources, results):
                    if data:
                        self.process_data(source["name"], data)
                        
                # After the first successful loop, mark as initialized to only trigger ON NEW events
                self.initialized = True
                await asyncio.sleep(self.polling_interval)

if __name__ == "__main__":
    tracker = StatusTracker(polling_interval=30)
    try:
        asyncio.run(tracker.track())
    except KeyboardInterrupt:
        print("\nTracker stopped.")
