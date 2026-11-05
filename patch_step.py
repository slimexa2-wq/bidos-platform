import re

filepath = "ultra_modules/credit_china_blacklist_check_batch_pipeline.py"
with open(filepath, "r") as f:
    content = f.read()

# Add lock import
if "import threading" not in content:
    content = re.sub(r"(import concurrent.futures\n)", r"\1import threading\n", content)

# Add lock initialization
if "self._lock" not in content:
    content = re.sub(r"(self\.executor = concurrent\.futures\.ThreadPoolExecutor\(max_workers=self\.max_workers\))", r"\1\n        self._lock = threading.Lock()", content)

# Fix thread safety issue in _on_record_completed
if "with self._lock:" not in content:
    replacement = """
    def _on_record_completed(self, batch_id: str, future: concurrent.futures.Future):
        if batch_id in self._tasks:
            try:
                result = future.result()
                with self._lock:
                    self._tasks[batch_id]['results'].append(result)
            except Exception as e:
                logger.error(f"Error processing record: {e}")
                with self._lock:
                    self._tasks[batch_id]['results'].append({
                        'status': 'error',
                        'error_msg': str(e)
                    })
            finally:
                with self._lock:
                    self._tasks[batch_id]['completed'] += 1

                    if self._tasks[batch_id]['completed'] >= self._tasks[batch_id]['total']:
                        self._tasks[batch_id]['status'] = 'completed'
"""
    content = re.sub(r"\n    def _on_record_completed\(self, batch_id: str, future: concurrent\.futures\.Future\):.*?\n                    self\._tasks\[batch_id\]\['status'\] = 'completed'\n", replacement, content, flags=re.DOTALL)

with open(filepath, "w") as f:
    f.write(content)
