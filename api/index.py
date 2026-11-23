"""Vercel serverless entry: wraps the FastAPI app from rd_engine/api.py.

The build step (vercel.json buildCommand) copies rd_engine/, the demo data
(clean/sample1_dev_seeded, results/sample1_dev_seeded) and answer_keys into
api/_bundle/, mirroring the repo layout. That way rd_engine/api.py's root
detection (two dirname levels up from rd_engine/api.py) and its relative
paths (results/, clean/) work unchanged inside the lambda.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BUNDLE = os.path.join(_HERE, "_bundle")

if os.path.isdir(_BUNDLE):
    # Deployed: everything the backend needs sits in the bundle.
    sys.path.insert(0, _BUNDLE)
    os.chdir(_BUNDLE)
else:
    # Local dev: run against the repo root.
    sys.path.insert(0, os.path.dirname(_HERE))
    os.chdir(os.path.dirname(_HERE))

from rd_engine.api import app  # noqa: E402  (FastAPI ASGI app served by Vercel)
