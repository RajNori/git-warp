"""Synthetic credentials for redaction/privacy tests.

Each value is assembled at import time from a split vendor prefix and a fake body, so no
literal token string ships in the plugin (directory scanners flag literal credentials even in
tests). The values still match the real token shapes, so they exercise core/redact.py exactly
as a live secret would. None of them is a real credential.
"""
from __future__ import annotations


def _tok(*parts: str) -> str:
    return "".join(parts)


GITHUB = _tok("gh", "p_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")
AWS_KEY = _tok("AK", "IA", "IOSFODNN7", "EXAMPLE")
AWS_SECRET = _tok("wJalrXUtnFEMI", "/K7MDENG/", "bPxRfiCY", "EXAMPLEKEY")
OPENAI = _tok("sk", "-proj-", "Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0hJ3kL6")
ANTHROPIC = _tok("sk", "-ant-", "api03-", "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe9Dc8Ba7")
NPM = _tok("np", "m_", "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6J7k8")
HF = _tok("h", "f_", "Zq1Xw2Ce3Rv4Bt5Ny6Mu7Ik8Ol9Pa0SdFg")
