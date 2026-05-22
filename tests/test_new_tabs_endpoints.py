def _get_routes():
    from app.main import app
    from fastapi.routing import APIRoute
    return {r.path for r in app.routes if isinstance(r, APIRoute)}

def _get_methods(path):
    from app.main import app
    from fastapi.routing import APIRoute
    return {m for r in app.routes
            if isinstance(r, APIRoute) and r.path == path
            for m in r.methods}

def test_security_get_route_registered():
    assert "/api/security" in _get_routes()

def test_security_post_route_registered():
    assert "POST" in _get_methods("/api/security")

def test_security_image_route_registered():
    assert "/api/security/image/{camera_name}" in _get_routes()

def test_recon_route_registered():
    assert "/api/recon" in _get_routes()

def test_aero_route_registered():
    assert "/api/aero" in _get_routes()