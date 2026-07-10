"""animica.ai — the AI feature package (5.2.0).

Houses the OpenAI-compatible gateway (``gateway.py``) that ``animica ai serve``
exposes. Kept import-light at package scope: the gateway pulls FastAPI lazily so
``import animica.ai`` never drags a web framework into the base CLI.
"""
