from ta_in.placeholder import ping


def test_smoke() -> None:
    assert ping() == "pong"
