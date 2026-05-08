import hashlib
import json


class ConfigHasher:
    """Utility for producing deterministic SHA-256 hashes of config payloads."""

    @staticmethod
    def generate_hash(config_json: dict) -> str:
        """SHA256 of the JSON with keys sorted deterministically."""
        normalized = json.dumps(config_json, sort_keys=True)
        return hashlib.sha256(normalized.encode()).hexdigest()
