"""Regression test: the index page's static-asset cache-bust query string must
carry the app version. It was silently empty because the route passes
`config=kj_config` to the template, shadowing Flask's app config (which holds
APP_VERSION) — so every frontend deploy served stale cached JS/CSS."""

from app import create_app


def test_index_cache_bust_uses_app_version(mock_config):
    app = create_app(config=mock_config)
    app.config['TESTING'] = True
    version = app.config['APP_VERSION']
    assert version, "APP_VERSION should be a non-empty version string"

    with app.test_client() as client:
        html = client.get('/').get_data(as_text=True)

    assert f'app.js?v={version}' in html
    assert f'style.css?v={version}' in html
    # And it must not regress to the empty form.
    assert 'app.js?v="' not in html
    assert 'app.js?v=>' not in html
