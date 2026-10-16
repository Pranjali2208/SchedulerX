"""Import this before ``import app`` in tests: isolates account files in a temp folder."""
import atexit
import os
import shutil
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

if "REEXAM_INSTANCE_DIR" not in os.environ:
    _tmp = tempfile.mkdtemp(prefix="reexam-test-instance-")
    os.environ["REEXAM_INSTANCE_DIR"] = _tmp
    atexit.register(shutil.rmtree, _tmp, ignore_errors=True)
for _k in ("SMTP_HOST", "MAIL_FROM", "APP_BASE_URL", "ADMIN_EMAIL", "COOKIE_SECURE"):
    os.environ.pop(_k, None)
os.environ.setdefault("ADMIN_PASSWORD", "admin")

TEST_ADMIN_USER = "admin"
TEST_ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


def csrf_client(flask_app):
    """A test client with a CSRF token, plus a helper to post JSON with it."""
    client = flask_app.app.test_client()
    token = client.get("/auth/csrf").get_json()["csrf_token"]
    return client, token


def login(client, token, ident=TEST_ADMIN_USER, password=TEST_ADMIN_PASSWORD):
    return client.post("/auth/login", json={"email": ident, "password": password},
                       headers={"X-CSRF-Token": token})
