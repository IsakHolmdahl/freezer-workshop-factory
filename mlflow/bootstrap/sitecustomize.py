"""Loaded by every MLflow Python worker (PYTHONPATH), including fresh gateway workers."""

# Group gateway traces per orchestration session (see gateway_session.py).
import gateway_session

gateway_session.install()
