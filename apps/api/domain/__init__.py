"""Deterministic, side-effect-free governance domain for Stage 1.

This package imports only the Python standard library and domain peers.  It
must never import FastAPI, SQLAlchemy, providers, repositories, or network
clients, and it must not read clocks, environment variables, files, or
process-global state.
"""
