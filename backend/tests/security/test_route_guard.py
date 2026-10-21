from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.routing import Mount

from linkedbuddy.config import Settings
from linkedbuddy.main import create_app
from linkedbuddy.security.auth import PUBLIC_ROUTES, require_owner

# Plain Starlette routes FastAPI adds itself (dev only). Anything else that is not an
# APIRoute (a Mount, a websocket, add_route) has no dependant and so cannot be guarded.
DOC_ROUTES = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def _calls(dependant: Dependant) -> set[object]:
    found: set[object] = set()
    for dep in dependant.dependencies:
        found.add(dep.call)
        found |= _calls(dep)
    return found


def route_problems(app: FastAPI) -> tuple[list[str], set[tuple[str, str]], int]:
    """Return (unguarded routes, public routes seen, guarded count)."""
    problems: list[str] = []
    seen_public: set[tuple[str, str]] = set()
    guarded = 0
    # FastAPI includes routers lazily; iter_route_contexts yields each route with its
    # effective path and dependencies (prefix and router-level `dependencies` applied).
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute):
            if route.path not in DOC_ROUTES:
                problems.append(
                    f"unguardable route {type(route.original_route).__name__} {route.path}"
                )
            continue
        assert route.path is not None and route.methods is not None
        for method in route.methods:
            if (method, route.path) in PUBLIC_ROUTES:
                seen_public.add((method, route.path))
            else:
                guarded += 1
                if require_owner not in _calls(route.dependant):
                    problems.append(f"{method} {route.path}")
    return problems, seen_public, guarded


def test_every_route_guarded(settings: Settings) -> None:
    problems, seen_public, guarded = route_problems(create_app(settings))
    assert problems == []
    assert seen_public == PUBLIC_ROUTES  # the exemption list names real routes
    assert guarded >= 2  # logout and me; guards against walking nothing


def test_guard_check_flags_mounts_and_unguarded_routes(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/api/leak")
    async def leak() -> str:
        return "x"

    app.mount("/api/x", Mount("/", routes=[]))
    app.router.add_route("/api/raw", lambda request: PlainTextResponse("x"))
    problems, _, _ = route_problems(app)
    joined = "\n".join(problems)
    assert "GET /api/leak" in joined
    assert "/api/x" in joined
    assert "/api/raw" in joined
