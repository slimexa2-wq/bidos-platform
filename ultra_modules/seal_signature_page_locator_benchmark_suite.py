import time
import uuid
import datetime
import concurrent.futures
import random

def _is_garbled(text):
    if not isinstance(text, str):
        return False
    # Simple check for typical garbled char sequences or non-printable chars
    return '\x00' in text

def generate_seal_locations(document_metadata=None, include_dirty=False):
    """
    Generate page locations for various seals based on document metadata.
    Args:
        document_metadata: Dictionary with total_pages, title, date, etc.
        include_dirty: Whether to simulate dirty data healing.
    Returns:
        List of dictionaries with seal locations.
    """
    if document_metadata is None:
        document_metadata = {}

    total_pages = document_metadata.get("total_pages", 0)

    # Handle missing params with default fallback
    if total_pages is None or not isinstance(total_pages, int) or total_pages <= 0:
        return []

    seal_types = document_metadata.get("seal_types", ["official", "legal_rep", "cross_page", "signature"])

    locations = []

    title = document_metadata.get("title", "")
    date_str = document_metadata.get("date", "")

    # Simple date boundary / dirty check
    if date_str:
        try:
            parsed_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            # If date is cross-year boundary (e.g. Dec 31 to Jan 1), it doesn't fail.
        except ValueError:
            pass # Self-healing: ignore invalid date

    # Title garbled check
    if _is_garbled(title):
        title = "Untitled (Recovered)"

    if "official" in seal_types:
        locations.append({"type": "official", "page": total_pages, "doc_title": title, "doc_date": date_str})

    if "legal_rep" in seal_types:
        locations.append({"type": "legal_rep", "page": total_pages, "doc_title": title, "doc_date": date_str})

    if "signature" in seal_types:
        locations.append({"type": "signature", "page": total_pages, "doc_title": title, "doc_date": date_str})

    if "cross_page" in seal_types:
        if total_pages > 1:
            locations.append({"type": "cross_page", "pages": list(range(1, total_pages + 1)), "doc_title": title, "doc_date": date_str})

    if include_dirty or document_metadata.get("has_empty_values"):
        locations.append({"type": "unknown", "page": None, "doc_title": title, "doc_date": date_str})

    return locations


class SealLocatorBenchmarkSuite:
    def __init__(self):
        pass

    def _synthetic_fixture_generator(self):
        """Generates a synthetic document metadata dictionary."""
        scenarios = [
            {"total_pages": 10, "date": "2023-12-31"}, # normal, end of year
            {"total_pages": 1000, "date": "2024-01-01"}, # massive, start of year
            {"total_pages": 5, "title": "GarbledText\x00"}, # dirty
            {"total_pages": None}, # missing param
            {"total_pages": 50, "has_empty_values": True, "date": "invalid-date"}
        ]
        return random.choice(scenarios)

    def benchmark_generation(self, iterations=100, concurrency=10):
        start_time = time.time()

        def task():
            fixture = self._synthetic_fixture_generator()
            return generate_seal_locations(fixture)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(task) for _ in range(iterations)]
            for future in concurrent.futures.as_completed(futures):
                _ = future.result()

        end_time = time.time()
        return (end_time - start_time) * 1000 / iterations  # Time per iteration in ms
