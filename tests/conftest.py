import pytest

from app.service import AgentService
from fake_browser import FakeBrowser


@pytest.fixture
def browser():
    return FakeBrowser()


@pytest.fixture
def service(tmp_path, browser):
    instance = AgentService(database_path=tmp_path / "test.db", memory_dir=tmp_path / "memory",
                            use_llm=False, use_embeddings=False, browser=browser)
    instance.runner.base_delay = 0.0
    yield instance
    instance.shutdown()


def wait_for_run(service, run_id):
    for event in service.bus.listen(run_id, heartbeat_seconds=1.0):
        if event and event["type"] == "run_finished":
            return event
    raise AssertionError("run stream closed without finishing")


@pytest.fixture
def order(service):
    def send(session_id, text):
        response = service.handle_message(session_id, text)
        assert response["type"] == "run"
        return wait_for_run(service, response["run_id"]), response["run_id"]
    return send
