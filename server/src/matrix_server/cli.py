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


def run_worker() -> None:
    import logging
    import signal
    import socket
    import threading

    from matrix_server.config import Settings
    from matrix_server.hooks import install_worker_hooks
    from matrix_server.storage.db import init_db, make_engine, make_session_factory
    from matrix_server.storage.spans import build_span_store
    from matrix_server.workers import runner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    runner.load_handler_modules()
    # The online-eval handler creates scores here: span store + rules score hook, as in the API.
    install_worker_hooks(build_span_store(settings))
    stop_event = threading.Event()

    def _stop(signum, _frame) -> None:
        logging.getLogger("matrix_server.worker").info("received %s, finishing current job", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    try:
        runner.run_forever(make_session_factory(engine), stop_event=stop_event, worker_id=worker_id)
    finally:
        engine.dispose()


def _build_admin_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="matrix-admin", description="MATRIX administration")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-user", help="create a user (password read from stdin or a prompt)")
    p.add_argument("email")
    p.add_argument("--name", default="")
    p = sub.add_parser("create-project", help="create a project and its first API key")
    p.add_argument("name")
    p.add_argument("--owner-email", default=None, help="existing user who becomes the owner")
    p = sub.add_parser("add-member", help="add an existing user to a project")
    p.add_argument("project_id")
    p.add_argument("email")
    p.add_argument("--role", choices=["viewer", "member", "admin", "owner"], default="member")
    p = sub.add_parser("create-key", help="create a new API key for a project")
    p.add_argument("project_id")
    p = sub.add_parser("revoke-key", help="revoke an API key by its prefix")
    p.add_argument("prefix")
    return parser


def _read_password() -> str:
    """Never from argv (shell history, `ps`): a prompt on a terminal, else the first stdin line."""
    import getpass

    if sys.stdin.isatty():
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            print("error: passwords do not match", file=sys.stderr)
            sys.exit(1)
        return password
    return sys.stdin.readline().rstrip("\r\n")


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def admin(argv: list[str] | None = None) -> None:
    from matrix_server import audit, auth, users
    from matrix_server.storage.db import init_db, make_engine, make_session_factory
    from matrix_server.storage.models import Membership, Project

    args = _build_admin_parser().parse_args(argv)
    database_url = os.environ.get("MATRIX_DATABASE_URL", "sqlite:///./matrix.db")
    engine = make_engine(database_url)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        if args.command == "create-user":
            try:
                user = users.create_user(session, args.email, args.name, _read_password())
            except users.UserError as exc:
                _fail(str(exc))
            audit.record(session, action="user.create", target=user.id, detail={"via": "cli"})
            session.commit()
            print(f"user_id: {user.id}")
        elif args.command == "create-project":
            owner_id = None
            if args.owner_email:
                owner = users.get_user_by_email(session, args.owner_email)
                if owner is None:
                    _fail("owner user not found")
                owner_id = owner.id
            try:
                project, key = auth.create_project(session, args.name, owner_user_id=owner_id)
            except auth.ProjectExistsError:
                _fail(f"project {args.name!r} already exists")
            audit.record(
                session, action="project.create", project_id=project.id, target=project.id,
                detail={"name": project.name, "owner": owner_id, "via": "cli"},
            )
            session.commit()
            print(f"project_id: {project.id}")
            print(f"api_key: {key}")
            print("Store this key now; it will not be shown again.", file=sys.stderr)
        elif args.command == "add-member":
            if session.get(Project, args.project_id) is None:
                _fail("project not found")
            user = users.get_user_by_email(session, args.email)
            if user is None:
                _fail("user not found")
            exists = session.query(Membership).filter_by(user_id=user.id, project_id=args.project_id).first()
            if exists is not None:
                _fail("user is already a member")
            session.add(Membership(user_id=user.id, project_id=args.project_id, role=args.role))
            audit.record(
                session, action="member.add", project_id=args.project_id, target=user.id,
                detail={"role": args.role, "via": "cli"},
            )
            session.commit()
            print(f"member: {user.id} {args.role}")
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
