#!/usr/bin/env python3
"""Valida deploy/compose.yaml sem Docker: depends_on e volumes consistentes, portas só em 127.0.0.1,
web sem MATRIX_API_KEY e com MATRIX_WEB_ORIGIN obrigatória. Requer PyYAML. Saída 1 se algo falhar."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "deploy" / "compose.yaml"


def _env_map(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                out[k] = v
    return out


def check(doc: dict) -> list[str]:
    errors: list[str] = []
    services = doc.get("services") or {}
    volumes = set((doc.get("volumes") or {}).keys())
    used: set[str] = set()
    for name, svc in services.items():
        deps = svc.get("depends_on") or []
        for dep in deps if isinstance(deps, list) else deps.keys():
            if dep not in services:
                errors.append(f"{name}: depends_on aponta para serviço inexistente '{dep}'")
        for vol in svc.get("volumes") or []:
            src = vol.split(":")[0] if isinstance(vol, str) else vol.get("source", "")
            if src and not src.startswith(("/", ".", "~")):
                used.add(src)
                if src not in volumes:
                    errors.append(f"{name}: volume nomeado '{src}' não declarado em volumes:")
        for port in svc.get("ports") or []:
            if not str(port).startswith("127.0.0.1:"):
                errors.append(f"{name}: porta publicada fora de 127.0.0.1: {port}")
    for v in volumes - used:
        errors.append(f"volume '{v}' declarado mas não usado")
    web = _env_map((services.get("web") or {}).get("environment"))
    if "MATRIX_API_KEY" in web:
        errors.append("web não deve receber MATRIX_API_KEY")
    if not re.search(r"\$\{MATRIX_WEB_ORIGIN:\?", web.get("MATRIX_WEB_ORIGIN", "")):
        errors.append("web: MATRIX_WEB_ORIGIN deve ser obrigatória (${MATRIX_WEB_ORIGIN:?...})")
    for key in ("MATRIX_WEB_TRUST_FORWARDED_FOR", "MATRIX_TRUSTED_PROXY_HEADER"):
        if key not in web:
            errors.append(f"web: falta {key}")
    api = _env_map((services.get("api") or {}).get("environment"))
    worker = _env_map((services.get("worker") or {}).get("environment"))
    for key in (
        "MATRIX_TRUSTED_PROXY_HEADER",
        "MATRIX_PASSWORD_HASH_CONCURRENCY",
        "MATRIX_MAX_RUNS_PER_PROJECT",
        "MATRIX_MAX_ACTIVE_RUNS_PER_PROJECT",
        "MATRIX_ARTIFACT_BACKEND",
        "MATRIX_ARTIFACT_LOCAL_DIR",
        "MATRIX_SECRET_KEY",
        "MATRIX_SANDBOX_IMAGES",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ):
        if key not in api:
            errors.append(f"api: falta {key}")
    for svc_name, env_map in (("api", api), ("worker", worker)):
        for secret_key in ("MATRIX_ADMIN_TOKEN", "MATRIX_SECRET_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            val = env_map.get(secret_key, "")
            if val and not re.fullmatch(r"\$\{[A-Z0-9_]+(?::-|:\?[^}]+)?\}", val):
                errors.append(f"{svc_name}: {secret_key} não pode conter valor literal")
        if any(k.startswith("MATRIX_S3_") for k in env_map):
            errors.append(f"{svc_name}: MATRIX_S3_* não deve estar no compose (não validado)")
    if "minio" in services or "s3" in services:
        errors.append("nenhum serviço S3/MinIO padrão deve estar no compose")
    if "rustfs" in services:
        rustfs = services["rustfs"] or {}
        if rustfs.get("profiles") != ["dev"]:
            errors.append("rustfs deve restringir-se a profiles: ['dev']")
        rustfs_env = _env_map(rustfs.get("environment"))
        for k, v in rustfs_env.items():
            if not re.fullmatch(r"\$\{[A-Z0-9_]+:\?[^}]+\}", v):
                errors.append(f"rustfs: variável {k} deve vir do ambiente sem valor hardcoded")
        for svc_name, svc in services.items():
            deps = svc.get("depends_on") or []
            dep_names = deps if isinstance(deps, list) else deps.keys()
            if "rustfs" in dep_names:
                errors.append(f"{svc_name}: não deve depender de rustfs (perfil dev apenas)")
    return errors


def main() -> int:
    errors = check(yaml.safe_load(PATH.read_text()))
    for e in errors:
        print(f"FAIL  {e}")
    print("compose OK" if not errors else f"{len(errors)} problema(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
