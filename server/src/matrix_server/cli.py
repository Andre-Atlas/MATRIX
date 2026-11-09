import argparse
import os
import sys


def run_api() -> None:
    import uvicorn

    uvicorn.run(
        "matrix_server.app:create_app",
        factory=True,
        host=os.environ.get("MATRIX_HOST", "0.0.0.0"),
        port=int(os.environ.get("MATRIX_PORT", "8000")),
    )


def _not_implemented() -> None:
    print("not implemented yet")
    sys.exit(1)


def run_worker() -> None:
    _not_implemented()


def _build_admin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matrix-admin", description="MATRIX administration")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-project", help="create a project and its first API key")
    p.add_argument("name")
    p = sub.add_parser("create-key", help="create a new API key for a project")
    p.add_argument("project_id")
    p = sub.add_parser("revoke-key", help="revoke an API key by its prefix")
    p.add_argument("prefix")
    return parser


def admin(argv: list[str] | None = None) -> None:
    from matrix_server import auth
    from matrix_server.storage.db import init_db, make_engine, make_session_factory
    from matrix_server.storage.models import Project

    args = _build_admin_parser().parse_args(argv)
    database_url = os.environ.get("MATRIX_DATABASE_URL", "sqlite:///./matrix.db")
    engine = make_engine(database_url)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        if args.command == "create-project":
            try:
                project, key = auth.create_project(session, args.name)
            except auth.ProjectExistsError:
                print(f"error: project {args.name!r} already exists", file=sys.stderr)
                sys.exit(1)
            session.commit()
            print(f"project_id: {project.id}")
            print(f"api_key: {key}")
            print("Store this key now; it will not be shown again.", file=sys.stderr)
        elif args.command == "create-key":
            if session.get(Project, args.project_id) is None:
                print("error: project not found", file=sys.stderr)
                sys.exit(1)
            key = auth.create_api_key(session, args.project_id)
            session.commit()
            print(f"api_key: {key}")
            print("Store this key now; it will not be shown again.", file=sys.stderr)
        elif args.command == "revoke-key":
            if not auth.revoke_api_key(session, args.prefix):
                print("error: key not found", file=sys.stderr)
                sys.exit(1)
            session.commit()
            print(f"revoked: {args.prefix}")
    finally:
        session.close()
        engine.dispose()
