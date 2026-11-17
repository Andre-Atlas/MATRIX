"""Test-suite settings: `MATRIX_ENV=test` lets `Settings` run without MATRIX_ARTIFACT_TOKEN_SECRET
(a per-process key signs MLOps artifact tokens). Real deployments must set the secret."""

import os

os.environ["MATRIX_ENV"] = "test"
