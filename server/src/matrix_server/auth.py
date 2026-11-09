"""Project authentication. Placeholder contract; T04 implements it.

Routes depend on `current_project_id`; tests override it with
`app.dependency_overrides[current_project_id] = lambda: "p1"`.
"""

from fastapi import HTTPException, Request


def current_project_id(request: Request) -> str:
    raise HTTPException(status_code=401, detail="authentication not configured")
