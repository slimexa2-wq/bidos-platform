with open('ultra_modules/qualification_expiry_checker_batch_pipeline.py', 'r') as f:
    content = f.read()

content = content.replace(
"""        if loop and loop.is_running():
            # If we're already in an async context, this might block,
            # but usually for a standalone pipeline call we can use a new loop or asyncio.run
            # Better to run in a thread or just execute if no loop
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(self._process_batch(records))
        else:
            return asyncio.run(self._process_batch(records))""",
"""        if loop and loop.is_running():
            # If we're already in an async context, running asyncio.run will fail.
            # Instead of relying on nest_asyncio, we can run the coroutine in a separate thread.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, self._process_batch(records))
                return future.result()
        else:
            return asyncio.run(self._process_batch(records))"""
)

with open('ultra_modules/qualification_expiry_checker_batch_pipeline.py', 'w') as f:
    f.write(content)
