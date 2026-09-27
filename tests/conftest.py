import pytest
from harness import Session, available

HAVE_SWAY = available()


@pytest.fixture
def session(tmp_path):
    if not HAVE_SWAY:
        pytest.skip("needs sway, swaymsg, wtype and GTK 4 for Python")
    started = []

    def start(layout="master", **options):
        instance = Session(tmp_path / str(len(started)), layout, **options).start()
        started.append(instance)
        return instance

    yield start
    for instance in started:
        errors = instance.stderr()
        instance.stop()
        assert not errors.strip(), f"daemon wrote to stderr:\n{errors}"
