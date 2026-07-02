import pytest


@pytest.fixture
def app_ctx():
    import app as app_module
    flask_app = app_module.create_app()
    with flask_app.app_context():
        yield flask_app
