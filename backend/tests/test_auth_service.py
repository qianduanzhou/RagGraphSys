import pytest

from services.auth_service import AuthError, AuthService


def test_register_and_verify_token(tmp_path):
    auth = AuthService(tmp_path / "users.json")
    session = auth.register("user01", "p@ss1")

    assert session["username"] == "user01"
    assert auth.verify_token(session["token"]) == "user01"


@pytest.mark.parametrize("username", ["u1", "ab", "   "])
def test_register_rejects_invalid_username(tmp_path, username):
    auth = AuthService(tmp_path / "users.json")
    with pytest.raises(AuthError):
        auth.register(username, "p@ss1")


@pytest.mark.parametrize("password", ["ab", "123", "x"])
def test_register_rejects_invalid_password(tmp_path, password):
    auth = AuthService(tmp_path / "users.json")
    with pytest.raises(AuthError):
        auth.register("user01", password)


def test_login_rejects_wrong_password(tmp_path):
    auth = AuthService(tmp_path / "users.json")
    auth.register("user01", "p@ss1")

    with pytest.raises(AuthError):
        auth.login("user01", "p@xx9")
