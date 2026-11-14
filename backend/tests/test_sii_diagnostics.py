from app.services.sii_browser_service import SiiBrowserService


def test_diagnostics_sanitize_urls_and_personal_values():
    service = SiiBrowserService()

    assert "99990004-6" not in service._safe_text("RUT 99.990.004-6")
    assert "user@example.com" not in service._safe_text("user@example.com")
    assert service._safe_url("https://misiir.sii.cl/path?token=secret#private") == (
        "https://misiir.sii.cl/path"
    )
