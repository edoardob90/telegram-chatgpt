"""
Utils
"""

import hashlib


def hash_data(data: str) -> str:
    """Hash an input string with SHA-256"""
    return hashlib.sha256(data.encode()).hexdigest()
