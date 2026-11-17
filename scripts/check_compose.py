#!/usr/bin/env python3
"""Valida deploy/compose.yaml sem Docker: depends_on e volumes consistentes, portas só em 127.0.0.1,
web sem MATRIX_API_KEY e com MATRIX_WEB_ORIGIN obrigatória. Requer PyYAML. Saída 1 se algo falhar."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "deploy" / "compose.yaml"


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
    web = (services.get("web") or {}).get("environment") or {}
    if "MATRIX_API_KEY" in web:
        errors.append("web não deve receber MATRIX_API_KEY")
    if not re.search(r"\$\{MATRIX_WEB_ORIGIN:\?", str(web.get("MATRIX_WEB_ORIGIN", ""))):
        errors.append("web: MATRIX_WEB_ORIGIN deve ser obrigatória (${MATRIX_WEB_ORIGIN:?...})")
    for key in ("MATRIX_WEB_TRUST_FORWARDED_FOR", "MATRIX_TRUSTED_PROXY_HEADER"):
        if key not in web:
            errors.append(f"web: falta {key}")
    api = (services.get("api") or {}).get("environment") or {}
    for key in ("MATRIX_TRUSTED_PROXY_HEADER", "MATRIX_PASSWORD_HASH_CONCURRENCY", "MATRIX_MAX_RUNS_PER_PROJECT",
                "MATRIX_MAX_ACTIVE_RUNS_PER_PROJECT", "MATRIX_ARTIFACT_BACKEND", "MATRIX_ARTIFACT_LOCAL_DIR"):
        if key not in api:
            errors.append(f"api: falta {key}")
    if any(k.startswith("MATRIX_S3_") for k in api) or "minio" in services or "s3" in services:
        errors.append("nenhum serviço S3 / MATRIX_S3_* deve estar no compose (não validado)")
    return errors


def main() -> int:
    errors = check(yaml.safe_load(PATH.read_text()))
    for e in errors:
        print(f"FAIL  {e}")
    print("compose OK" if not errors else f"{len(errors)} problema(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
