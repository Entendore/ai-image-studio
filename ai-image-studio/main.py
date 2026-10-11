#!/usr/bin/env python3
"""Launcher shim — real entry point: app.main:main"""
from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
