"""Pytest fixtures for the WiFi Tester (HTTP-only, Pi backend).

Usage:
    pytest test_instrument.py --wt-url http://<pi-ip>:8080
"""

import os
import uuid

import pytest

from wifi_tester_driver import WiFiTesterDriver


def pytest_addoption(parser):
    parser.addoption(
        "--wt-url",
        default=os.environ.get("WIFI_TESTER_URL", "http://localhost:8080"),
        help="Portal URL for the WiFi Tester Pi",
    )
    parser.addoption(
        "--run-dut",
        action="store_true",
        default=False,
        help="Run tests that require a DUT connected",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_dut: test needs a DUT or second WiFi device connected",
    )


def pytest_collection_modifyitems(config, items):
    run_dut = config.getoption("--run-dut", default=False)
    if not run_dut:
        skip_dut = pytest.mark.skip(reason="Requires a DUT (use --run-dut)")
        for item in items:
            if "requires_dut" in item.keywords:
                item.add_marker(skip_dut)


@pytest.fixture(scope="session")
def wifi_tester(request):
    """Session-scoped connection to the WiFi Tester instrument."""
    url = request.config.getoption("--wt-url")
    driver = WiFiTesterDriver(url)
    driver.open()
    driver.ping()
    yield driver
    try:
        driver.ap_stop()
    except Exception:
        pass
    driver.close()


@pytest.fixture
def wifi_network(wifi_tester):
    """Start a fresh AP for this test, stop on teardown."""
    ssid = f"WT-{uuid.uuid4().hex[:6].upper()}"
    password = "testpass123"
    wifi_tester.drain_events()
    wifi_tester.ap_start(ssid, password)
    yield {"ssid": ssid, "password": password, "ap_ip": "192.168.4.1"}
    wifi_tester.ap_stop()
