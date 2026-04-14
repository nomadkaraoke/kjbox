"""E2E Playwright tests for rotation multi-singer pill input."""

import pytest


@pytest.fixture
def rotation_page(app_page):
    """Navigate to the app and open the rotation add form."""
    page = app_page
    add_btn = page.locator('.rotation-add-btn')
    add_btn.click()
    page.locator('#singer-input-container').wait_for(state='visible')
    return page


class TestSingleSingerAdd:
    def test_type_name_and_submit(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('TestSingleSinger')
        page.locator('#rotation-singer').press('Enter')
        page.locator('#rotation-song').fill('Test Song')
        page.locator('#rotation-add-btn-submit').click()
        # Wait for our specific entry to appear
        page.locator('.rotation-name', has_text='TestSingleSinger').first.wait_for(state='visible')
        assert page.locator('.rotation-name', has_text='TestSingleSinger').count() > 0


class TestMultiSingerPillCreation:
    def test_tab_creates_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('PillTestA')
        page.locator('#rotation-singer').press('Tab')
        pills = page.locator('.singer-pill')
        assert pills.count() == 1
        assert 'PillTestA' in pills.first.text_content()
        assert page.locator('#rotation-singer').input_value() == ''

    def test_comma_creates_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').type('CommaTest,')
        pills = page.locator('.singer-pill')
        assert pills.count() == 1
        assert 'CommaTest' in pills.first.text_content()

    def test_ampersand_creates_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').type('AmpTest&')
        pills = page.locator('.singer-pill')
        assert pills.count() == 1
        assert 'AmpTest' in pills.first.text_content()

    def test_multi_singer_submit_shows_pills_in_rotation(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('DuetAlpha')
        page.locator('#rotation-singer').press('Tab')
        page.locator('#rotation-singer').fill('DuetBeta')
        page.locator('#rotation-singer').press('Enter')
        page.locator('#rotation-song').fill('Duet Test Song')
        page.locator('#rotation-add-btn-submit').click()
        # Wait for entry to appear
        page.locator('.rotation-entry').first.wait_for(state='visible')
        # Multi-singer entry should show individual singer pills
        singer_pills = page.locator('.rotation-singer-pill')
        assert singer_pills.count() >= 2
        texts = [singer_pills.nth(i).text_content() for i in range(singer_pills.count())]
        assert any('DuetAlpha' in t for t in texts)
        assert any('DuetBeta' in t for t in texts)


class TestPillDeletion:
    def test_backspace_removes_last_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('BackA')
        page.locator('#rotation-singer').press('Tab')
        page.locator('#rotation-singer').fill('BackB')
        page.locator('#rotation-singer').press('Tab')
        assert page.locator('.singer-pill').count() == 2
        page.locator('#rotation-singer').press('Backspace')
        assert page.locator('.singer-pill').count() == 1
        assert 'BackA' in page.locator('.singer-pill').first.text_content()

    def test_x_button_removes_pill(self, rotation_page):
        page = rotation_page
        page.locator('#rotation-singer').fill('XTestA')
        page.locator('#rotation-singer').press('Tab')
        page.locator('#rotation-singer').fill('XTestB')
        page.locator('#rotation-singer').press('Tab')
        assert page.locator('.singer-pill').count() == 2
        page.locator('.singer-pill-x').first.click()
        pills = page.locator('.singer-pill')
        assert pills.count() == 1
        assert 'XTestB' in pills.first.text_content()
