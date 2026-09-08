"""Text normalization for Rime TTS compatibility and pronunciation."""

import re

# Phonetic spelling adjustments for Rime's English voice models
PRONUNCIATION_MAP = {
    "paneer": "puh-neer",
    "ghee": "g-hee",
    "masala": "muh-saa-laa",
    "roti": "row-tee",
    "maggi": "mag-gee",
    "maggie": "mag-gee",
    "quesadilla": "keh-suh-dee-uh",
    "1/2": "one half",
    "1/4": "one quarter",
    "3/4": "three quarters",
}

def normalize_for_rime(text: str) -> str:
    """Clean fractions, symbols, and map local culinary terms to English phonetics."""
    if not text:
        return ""
    
    normalized = text.lower()
    
    # Replace mapped culinary terms & fractions with phonetics
    for raw_word, phonetic in PRONUNCIATION_MAP.items():
        normalized = re.sub(r'\b' + re.escape(raw_word) + r'\b', phonetic, normalized)
        
    # Clean up residual non-alphanumeric characters, keep basic punctuation
    normalized = re.sub(r'[^\w\s.,?!-]', '', normalized)
    return normalized.strip()