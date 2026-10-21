from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts

from linkedbuddy.config import Settings
from linkedbuddy.main import create_app
from linkedbuddy.security.auth import PUBLIC_ROUTES, require_owner


def _calls(dependant: Dependant) -> set[object]:
    found: set[object] = set()
    for dep in dependant.dependencies:
        found.add(dep.call)
        found |= _calls(dep)
    return found


def test_every_route_guarded(settings: Settings) -> None:
    app = create_app(settings)
    seen_public: set[tuple[str, str]] = set()
    guarded = 0
    # FastAPI includes routers lazily; iter_route_contexts yields each route with its
    # effective path and dependencies (prefix and router-level `dependencies` applied).
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue  # /docs, /openapi.json (dev only) are plain Starlette routes
        assert route.path is not None and route.methods is not None
        for method in route.methods:
            if (method, route.path) in PUBLIC_ROUTES:
                seen_public.add((method, route.path))
            else:
                guarded += 1
                assert require_owner in _calls(route.dependant), f"{method} {route.path}"
    assert seen_public == PUBLIC_ROUTES  # the exemption list names real routes
    assert guarded >= 2  # logout and me; guards against walking nothing
