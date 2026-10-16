"""Authentication tests: registration, sign in, logout, forgot/reset password, CSRF, rate limits.

Run from the project folder:  python -m unittest discover -s tests -v
Account files live in a temporary folder (see tests/_appenv.py); nothing real is touched.
"""
import json
import os
import re
import time
import unittest
from unittest import mock

import _appenv
from _appenv import TEST_ADMIN_PASSWORD, TEST_ADMIN_USER

import app as flask_app
import auth

GOOD = "Sup3rSecret"
NEW = "Brand9NewPass"


class AuthTestBase(unittest.TestCase):
    def setUp(self):
        auth.limiter.reset()
        self.app = flask_app.app
        self.sent = []  # (recipient, link) captured instead of using SMTP
        patcher = mock.patch.object(auth, "_send_reset_email_async",
                                    lambda to, link, minutes: self.sent.append((to, link)))
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"SMTP_HOST": "smtp.test", "MAIL_FROM": "x@test.local",
                                           "APP_BASE_URL": "http://app.test"})
        env.start()
        self.addCleanup(env.stop)
        self.creds_backup = open(self.app.config["AUTH_FILE"], encoding="utf-8").read() \
            if os.path.exists(self.app.config["AUTH_FILE"]) else None
        self.addCleanup(self._restore_admin)

    def _restore_admin(self):
        if self.creds_backup is not None:
            with open(self.app.config["AUTH_FILE"], "w", encoding="utf-8") as f:
                f.write(self.creds_backup)

    # -- helpers -----------------------------------------------------------
    def new_client(self):
        client, token = _appenv.csrf_client(flask_app)
        client.token = token
        return client

    def post(self, client, url, payload=None, token=True, **kw):
        headers = {"X-CSRF-Token": client.token} if token else {}
        return client.post(url, json=payload if payload is not None else {}, headers=headers, **kw)

    def register(self, client, email, password=GOOD, confirm=None, name="Test User", **extra):
        body = {"full_name": name, "email": email, "password": password,
                "confirm_password": password if confirm is None else confirm}
        body.update(extra)
        return self.post(client, "/auth/register", body)

    def login(self, client, ident, password):
        return self.post(client, "/auth/login", {"email": ident, "password": password})

    def make_user(self, email, password=GOOD):
        c = self.new_client()
        self.assertEqual(self.register(c, email, password).status_code, 201)
        return c

    def token_from(self, link):
        return re.search(r"token=([\w\-]+)", link).group(1)

    def users(self):
        return json.load(open(self.app.config["REGISTERED_USERS_FILE"], encoding="utf-8"))


class RegistrationTests(AuthTestBase):
    def test_valid_registration_and_login(self):
        c = self.new_client()
        r = self.register(c, "reg.ok@example.com")
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertTrue(r.get_json()["success"])
        r = self.login(c, "reg.ok@example.com", GOOD)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["role"], "user")

    def test_email_is_normalised(self):
        c = self.new_client()
        self.assertEqual(self.register(c, "  Mixed.Case@Example.COM ").status_code, 201)
        self.assertEqual(self.login(c, "mixed.case@example.com", GOOD).status_code, 200)

    def test_missing_fields_rejected(self):
        c = self.new_client()
        for body in ({}, {"full_name": "A B", "email": "m1@example.com"},
                     {"full_name": "", "email": "m2@example.com", "password": GOOD, "confirm_password": GOOD},
                     {"full_name": "A B", "email": "", "password": GOOD, "confirm_password": GOOD},
                     {"full_name": "A B", "email": "m3@example.com", "password": GOOD}):
            r = self.post(c, "/auth/register", body)
            self.assertEqual(r.status_code, 400, body)

    def test_no_recovery_answer_needed_and_ignored(self):
        c = self.new_client()
        self.assertEqual(self.register(c, "norecov@example.com").status_code, 201)
        self.assertEqual(self.register(c, "withrecov@example.com", recovery_answer="x").status_code, 201)
        for u in self.users():
            self.assertNotIn("recovery_answer_hash", u)
            self.assertNotIn("recovery_answer", u)

    def test_invalid_email_rejected(self):
        c = self.new_client()
        for bad in ("plainaddress", "a@b", "@example.com", "a b@example.com", "x" * 250 + "@example.com"):
            self.assertEqual(self.register(c, bad).status_code, 400, bad)

    def test_password_rules_and_mismatch(self):
        c = self.new_client()
        for bad in ("short1A", "alllowercase1", "ALLUPPERCASE1", "NoDigitsHere", "A1a" + "x" * 200):
            self.assertEqual(self.register(c, "pw@example.com", bad).status_code, 400, bad)
        r = self.register(c, "pw@example.com", GOOD, confirm=GOOD + "x")
        self.assertEqual(r.status_code, 400)
        self.assertIn("do not match", r.get_json()["error"])

    def test_duplicate_rejected_case_insensitive(self):
        c = self.new_client()
        self.assertEqual(self.register(c, "dup@example.com").status_code, 201)
        self.assertEqual(self.register(c, "DUP@example.com", "Other1Passw").status_code, 409)
        self.assertEqual(len([u for u in self.users() if u["email"] == "dup@example.com"]), 1)
        # the original account's password is untouched
        self.assertEqual(self.login(c, "dup@example.com", GOOD).status_code, 200)

    def test_cannot_register_the_administrator_username(self):
        c = self.new_client()
        # the admin username is not an e-mail, so this is rejected as invalid
        self.assertEqual(self.register(c, TEST_ADMIN_USER).status_code, 400)

    def test_cannot_become_administrator(self):
        c = self.new_client()
        r = self.register(c, "sneaky@example.com", role="administrator", is_admin=True)
        self.assertEqual(r.status_code, 201)
        self.assertEqual([u for u in self.users() if u["email"] == "sneaky@example.com"][0]["role"], "user")
        self.assertEqual(self.login(c, "sneaky@example.com", GOOD).get_json()["role"], "user")
        self.assertEqual(c.get("/auth/registered-users").status_code, 403)

    def test_password_stored_as_hash_only(self):
        c = self.new_client()
        self.register(c, "hash@example.com")
        raw = open(self.app.config["REGISTERED_USERS_FILE"], encoding="utf-8").read()
        self.assertNotIn(GOOD, raw)
        user = [u for u in self.users() if u["email"] == "hash@example.com"][0]
        self.assertNotEqual(user["password_hash"], GOOD)
        self.assertTrue(user["password_hash"].startswith(("scrypt:", "pbkdf2:")))

    def test_existing_users_not_overwritten(self):
        c = self.new_client()
        self.register(c, "first.keep@example.com")
        before = [u for u in self.users() if u["email"] == "first.keep@example.com"][0]
        self.register(c, "second.keep@example.com")
        after = [u for u in self.users() if u["email"] == "first.keep@example.com"][0]
        self.assertEqual(before, after)


class SignInTests(AuthTestBase):
    def test_admin_login_and_session(self):
        c = self.new_client()
        r = self.login(c, TEST_ADMIN_USER, TEST_ADMIN_PASSWORD)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["role"], "administrator")
        s = c.get("/auth/session").get_json()
        self.assertTrue(s["authenticated"])
        self.assertEqual(s["role"], "administrator")
        self.assertEqual(c.get("/auth/registered-users").status_code, 200)

    def test_wrong_password_unknown_account_and_empty(self):
        c = self.new_client()
        self.make_user("signin1@example.com")
        wrong = self.login(c, "signin1@example.com", "Wrong1Password")
        unknown = self.login(c, "nobody@example.com", "Wrong1Password")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.get_json(), unknown.get_json())  # generic, no account enumeration
        self.assertEqual(self.login(c, TEST_ADMIN_USER, "nope").status_code, 401)
        self.assertEqual(self.login(c, "", "").status_code, 400)
        self.assertEqual(self.login(c, "signin1@example.com", "").status_code, 400)
        self.assertEqual(self.post(c, "/auth/login", {}).status_code, 400)
        self.assertFalse(c.get("/auth/session").get_json()["authenticated"])

    def test_malformed_payloads_do_not_crash(self):
        c = self.new_client()
        for payload in ([], "text", {"email": ["x"], "password": {"a": 1}}, {"email": 5, "password": 6}):
            r = c.post("/auth/login", data=json.dumps(payload), content_type="application/json",
                       headers={"X-CSRF-Token": c.token})
            self.assertIn(r.status_code, (400, 401), payload)

    def test_protected_routes_reject_anonymous(self):
        c = self.new_client()
        self.assertEqual(self.post(c, "/generate").status_code, 401)
        for url in ("/download/timetable", "/download/timetable.pdf", "/download/timetable.docx",
                    "/download/cleaned", "/auth/registered-users"):
            self.assertEqual(c.get(url).status_code, 401, url)

    def test_forged_session_cookie_flag_is_not_enough(self):
        c = self.new_client()
        with c.session_transaction() as sess:
            sess["authenticated"] = True
            sess["role"] = "administrator"
        self.assertFalse(c.get("/auth/session").get_json()["authenticated"])
        self.assertEqual(c.get("/auth/registered-users").status_code, 401)
        with c.session_transaction() as sess:
            sess["sid"] = "made-up"
        self.assertEqual(c.get("/download/timetable").status_code, 401)

    def test_logout_invalidates_session_even_if_cookie_replayed(self):
        c = self.new_client()
        self.login(c, TEST_ADMIN_USER, TEST_ADMIN_PASSWORD)
        with c.session_transaction() as sess:
            saved = dict(sess)
        self.assertEqual(self.post(c, "/auth/logout").status_code, 200)
        self.assertFalse(c.get("/auth/session").get_json()["authenticated"])
        self.assertEqual(c.get("/auth/registered-users").status_code, 401)
        with c.session_transaction() as sess:  # attacker replays the old signed cookie
            sess.update(saved)
        self.assertFalse(c.get("/auth/session").get_json()["authenticated"])
        self.assertEqual(c.get("/auth/registered-users").status_code, 401)

    def test_regular_user_cannot_use_admin_endpoint(self):
        c = self.make_user("plain@example.com")
        self.login(c, "plain@example.com", GOOD)
        self.assertEqual(c.get("/auth/registered-users").status_code, 403)

    def test_login_lockout_after_repeated_failures(self):
        c = self.make_user("lock@example.com")
        for _ in range(5):
            self.assertEqual(self.login(c, "lock@example.com", "Bad1Passw0rd").status_code, 401)
        r = self.login(c, "lock@example.com", GOOD)  # even the right password is held back
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)

    def test_session_cookie_flags(self):
        c = self.new_client()
        r = self.login(c, TEST_ADMIN_USER, TEST_ADMIN_PASSWORD)
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_security_headers(self):
        c = self.new_client()
        r = c.get("/")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")


class CsrfTests(AuthTestBase):
    def test_post_without_or_with_wrong_token_is_rejected(self):
        c = self.new_client()
        r = self.post(c, "/auth/login", {"email": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD}, token=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error_code"], "csrf")
        r = c.post("/auth/login", json={"email": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
                   headers={"X-CSRF-Token": "wrong"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.post(c, "/generate", token=False).status_code, 403)
        self.assertEqual(c.post("/auth/logout").status_code, 403)

    def test_token_from_another_session_is_rejected(self):
        a, b = self.new_client(), self.new_client()
        r = b.post("/auth/login", json={"email": TEST_ADMIN_USER, "password": TEST_ADMIN_PASSWORD},
                   headers={"X-CSRF-Token": a.token})
        self.assertEqual(r.status_code, 403)


class ForgotPasswordTests(AuthTestBase):
    def request_reset(self, c, ident):
        return self.post(c, "/auth/forgot-password", {"email": ident})

    def link_for(self, c, ident):
        self.sent.clear()
        r = self.request_reset(c, ident)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(len(self.sent), 1)
        return self.sent[0]

    def test_no_recovery_answer_anywhere(self):
        page = self.new_client().get("/").get_data(as_text=True)
        self.assertNotIn("RecoveryAnswer", page)
        self.assertNotIn("recovery_answer", page)
        self.assertNotIn("Recovery Answer", page)
        src = open(os.path.join(_appenv.PROJECT, "auth.py"), encoding="utf-8").read().replace("recovery_answer_hash\" in", "")
        self.assertNotIn("recovery_answer\"", src)

    def test_full_reset_flow(self):
        c = self.make_user("flow@example.com", GOOD)
        to, link = self.link_for(c, "flow@example.com")
        self.assertEqual(to, "flow@example.com")
        self.assertTrue(link.startswith("http://app.test/reset-password?token="))
        token = self.token_from(link)

        # the link opens the app page, which asks only for the new password
        page = c.get("/reset-password?token=" + token)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(self.post(c, "/auth/reset-password/check", {"token": token}).status_code, 200)

        # mismatch and weak password do not consume the token
        r = self.post(c, "/auth/reset-password", {"token": token, "new_password": NEW, "confirm_password": NEW + "x"})
        self.assertEqual(r.status_code, 400)
        r = self.post(c, "/auth/reset-password", {"token": token, "new_password": "weak", "confirm_password": "weak"})
        self.assertEqual(r.status_code, 400)

        r = self.post(c, "/auth/reset-password", {"token": token, "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 200, r.get_json())
        # new password works, old does not
        self.assertEqual(self.login(c, "flow@example.com", NEW).status_code, 200)
        self.post(c, "/auth/logout")
        self.assertEqual(self.login(c, "flow@example.com", GOOD).status_code, 401)
        # hashed with the same mechanism as sign in
        user = [u for u in self.users() if u["email"] == "flow@example.com"][0]
        self.assertNotEqual(user["password_hash"], NEW)
        self.assertNotIn(NEW, open(self.app.config["REGISTERED_USERS_FILE"], encoding="utf-8").read())
        # single use
        r = self.post(c, "/auth/reset-password", {"token": token, "new_password": "Another1Pass", "confirm_password": "Another1Pass"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.post(c, "/auth/reset-password/check", {"token": token}).status_code, 400)
        self.assertEqual(self.login(c, "flow@example.com", NEW).status_code, 200)

    def test_invalid_forged_and_empty_tokens(self):
        c = self.new_client()
        for tok in ("", "forged", "A" * 43, "x" * 500, "' OR 1=1 --"):
            r = self.post(c, "/auth/reset-password", {"token": tok, "new_password": NEW, "confirm_password": NEW})
            self.assertEqual(r.status_code, 400, tok)
        self.assertEqual(self.post(c, "/auth/reset-password", {"new_password": NEW, "confirm_password": NEW}).status_code, 400)
        self.assertEqual(self.post(c, "/auth/reset-password/check", {"token": "forged"}).status_code, 400)

    def test_expired_token_rejected(self):
        c = self.make_user("expire@example.com")
        _, link = self.link_for(c, "expire@example.com")
        token = self.token_from(link)
        path = self.app.config["RESET_TOKENS_FILE"]
        items = json.load(open(path, encoding="utf-8"))
        for it in items:
            it["expires"] = time.time() - 1
        json.dump(items, open(path, "w", encoding="utf-8"))
        r = self.post(c, "/auth/reset-password", {"token": token, "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.login(c, "expire@example.com", GOOD).status_code, 200)

    def test_token_stored_only_as_hash_with_expiry(self):
        c = self.make_user("hashtok@example.com")
        _, link = self.link_for(c, "hashtok@example.com")
        token = self.token_from(link)
        raw = open(self.app.config["RESET_TOKENS_FILE"], encoding="utf-8").read()
        self.assertNotIn(token, raw)
        entry = json.loads(raw)[-1]
        self.assertGreater(entry["expires"], time.time())
        self.assertLessEqual(entry["expires"] - time.time(), 24 * 3600)
        self.assertGreaterEqual(len(token), 40)

    def test_new_request_invalidates_previous_token(self):
        c = self.make_user("twice@example.com")
        _, first = self.link_for(c, "twice@example.com")
        auth.limiter.reset()
        _, second = self.link_for(c, "twice@example.com")
        self.assertNotEqual(first, second)
        r = self.post(c, "/auth/reset-password", {"token": self.token_from(first), "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 400)
        r = self.post(c, "/auth/reset-password", {"token": self.token_from(second), "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 200)

    def test_token_unusable_after_password_changed_another_way(self):
        c = self.make_user("changed@example.com")
        _, link = self.link_for(c, "changed@example.com")
        self.login(c, "changed@example.com", GOOD)
        self.post(c, "/auth/change-password", {"current_password": GOOD, "new_password": NEW, "confirm_password": NEW})
        r = self.post(c, "/auth/reset-password", {"token": self.token_from(link), "new_password": "Third3Passw", "confirm_password": "Third3Passw"})
        self.assertEqual(r.status_code, 400)

    def test_responses_do_not_reveal_whether_account_exists(self):
        c = self.make_user("exists@example.com")
        self.sent.clear()
        known = self.request_reset(c, "exists@example.com")
        unknown = self.request_reset(c, "ghost@example.com")
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.get_json(), unknown.get_json())
        self.assertEqual(len(self.sent), 1)  # mail only for the real account
        self.assertNotIn("token", json.dumps(known.get_json()).lower().replace("security token", ""))

    def test_forgot_empty_and_rate_limited(self):
        c = self.new_client()
        self.assertEqual(self.request_reset(c, "").status_code, 400)
        auth.limiter.reset()
        codes = [self.request_reset(c, f"spam{i}@example.com").status_code for i in range(7)]
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(codes[5:], [429, 429])
        auth.limiter.reset()
        codes = [self.request_reset(c, "same@example.com").status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])  # per-account limit applies to unknown accounts too

    def test_reset_attempts_rate_limited(self):
        c = self.new_client()
        codes = [self.post(c, "/auth/reset-password", {"token": "bad", "new_password": NEW, "confirm_password": NEW}).status_code
                 for _ in range(22)]
        self.assertEqual(codes[:20], [400] * 20)
        self.assertEqual(codes[20:], [429, 429])

    def test_admin_cannot_be_reset_by_username_alone(self):
        c = self.new_client()
        self.sent.clear()
        # no ADMIN_EMAIL configured: nothing is sent and nothing changes
        r = self.request_reset(c, TEST_ADMIN_USER)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.login(c, TEST_ADMIN_USER, TEST_ADMIN_PASSWORD).status_code, 200)
        # the old unauthenticated endpoint contract no longer works
        r = self.post(c, "/auth/reset-password", {"username": TEST_ADMIN_USER, "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 400)

    def test_admin_reset_via_admin_email(self):
        c = self.new_client()
        with mock.patch.dict(os.environ, {"ADMIN_EMAIL": "boss@example.com"}):
            to, link = self.link_for(c, TEST_ADMIN_USER)
        self.assertEqual(to, "boss@example.com")
        r = self.post(c, "/auth/reset-password", {"token": self.token_from(link), "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.login(c, TEST_ADMIN_USER, NEW).status_code, 200)
        self.post(c, "/auth/logout")
        self.assertEqual(self.login(c, TEST_ADMIN_USER, TEST_ADMIN_PASSWORD).status_code, 401)

    def test_reset_signs_out_existing_sessions(self):
        victim = self.make_user("sessions@example.com")
        self.login(victim, "sessions@example.com", GOOD)
        self.assertTrue(victim.get("/auth/session").get_json()["authenticated"])
        other = self.new_client()
        _, link = self.link_for(other, "sessions@example.com")
        self.post(other, "/auth/reset-password", {"token": self.token_from(link), "new_password": NEW, "confirm_password": NEW})
        self.assertFalse(victim.get("/auth/session").get_json()["authenticated"])

    def test_no_email_configured_is_reported_honestly(self):
        c = self.new_client()
        with mock.patch.dict(os.environ, {"SMTP_HOST": ""}):
            self.sent.clear()
            r = self.request_reset(c, "whoever@example.com")
            self.assertFalse(r.get_json()["email_enabled"])
            self.assertIn("not configured", r.get_json()["message"])
            self.assertEqual(self.sent, [])
            self.assertFalse(c.get("/auth/session").get_json()["email_reset_enabled"])

    def test_operator_reset_link_command(self):
        c = self.make_user("cli@example.com")
        with self.app.app_context():
            link = auth.create_reset_link_for("cli@example.com")
            self.assertIsNone(auth.create_reset_link_for("missing@example.com"))
        r = self.post(c, "/auth/reset-password", {"token": self.token_from(link), "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.login(c, "cli@example.com", NEW).status_code, 200)

    def test_email_sender_builds_message_without_leaking_secrets(self):
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=None, **kw): sent["host"] = (host, port)
            def starttls(self, context=None): sent["tls"] = True
            def login(self, u, p): sent["login"] = (u, p)
            def send_message(self, m): sent["msg"] = m
            def quit(self): pass
        with mock.patch.dict(os.environ, {"SMTP_HOST": "smtp.test", "SMTP_PORT": "2525", "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p"}), \
                mock.patch.object(auth.smtplib, "SMTP", FakeSMTP):
            auth._send_reset_email("to@example.com", "http://app.test/reset-password?token=abc", 30)
        self.assertEqual(sent["host"], ("smtp.test", 2525))
        self.assertTrue(sent["tls"])
        self.assertEqual(sent["msg"]["To"], "to@example.com")
        self.assertIn("token=abc", sent["msg"].get_content())


class ChangePasswordTests(AuthTestBase):
    def test_change_password(self):
        c = self.make_user("chg@example.com")
        self.login(c, "chg@example.com", GOOD)
        r = self.post(c, "/auth/change-password", {"current_password": "Wrong1Pass", "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 400)
        r = self.post(c, "/auth/change-password", {"current_password": GOOD, "new_password": NEW, "confirm_password": NEW + "x"})
        self.assertEqual(r.status_code, 400)
        r = self.post(c, "/auth/change-password", {"current_password": GOOD, "new_password": "weak", "confirm_password": "weak"})
        self.assertEqual(r.status_code, 400)
        r = self.post(c, "/auth/change-password", {"current_password": GOOD, "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(c.get("/auth/session").get_json()["authenticated"])  # current session stays
        self.post(c, "/auth/logout")
        self.assertEqual(self.login(c, "chg@example.com", GOOD).status_code, 401)
        self.assertEqual(self.login(c, "chg@example.com", NEW).status_code, 200)

    def test_requires_login(self):
        c = self.new_client()
        r = self.post(c, "/auth/change-password", {"current_password": GOOD, "new_password": NEW, "confirm_password": NEW})
        self.assertEqual(r.status_code, 401)


class LegacyDataTests(AuthTestBase):
    def test_migration_strips_recovery_answers_and_keeps_accounts(self):
        users_path = self.app.config["REGISTERED_USERS_FILE"]
        backup = open(users_path, encoding="utf-8").read() if os.path.exists(users_path) else None
        self.addCleanup(lambda: open(users_path, "w", encoding="utf-8").write(backup) if backup is not None else os.path.exists(users_path) and os.remove(users_path))
        legacy_hash = auth.generate_password_hash("Legacy1Pass")
        json.dump([{"id": "legacy1", "full_name": "Old User", "email": "old@example.com",
                    "password_hash": legacy_hash, "recovery_answer_hash": "x", "role": "user",
                    "created_at": "2025-01-01T00:00:00+00:00"}], open(users_path, "w", encoding="utf-8"))
        creds_path = self.app.config["AUTH_FILE"]
        creds = json.load(open(creds_path, encoding="utf-8"))
        creds["recovery_answer_hash"] = "legacy"
        json.dump(creds, open(creds_path, "w", encoding="utf-8"))

        # existing admin with the legacy field still signs in (must not be reset to defaults)
        c = self.new_client()
        self.assertEqual(self.login(c, TEST_ADMIN_USER, TEST_ADMIN_PASSWORD).status_code, 200)
        with self.app.app_context():
            self.assertEqual(auth.migrate_legacy_fields(), 2)
            self.assertEqual(auth.migrate_legacy_fields(), 0)  # idempotent
        self.assertNotIn("recovery_answer_hash", json.load(open(creds_path, encoding="utf-8")))
        users = json.load(open(users_path, encoding="utf-8"))
        self.assertEqual(len(users), 1)
        self.assertNotIn("recovery_answer_hash", users[0])
        self.assertEqual(users[0]["password_hash"], legacy_hash)
        c2 = self.new_client()
        self.assertEqual(self.login(c2, "old@example.com", "Legacy1Pass").status_code, 200)

    def test_corrupt_user_store_is_not_overwritten_by_registration(self):
        users_path = self.app.config["REGISTERED_USERS_FILE"]
        backup = open(users_path, encoding="utf-8").read() if os.path.exists(users_path) else None
        self.addCleanup(lambda: open(users_path, "w", encoding="utf-8").write(backup) if backup is not None else os.remove(users_path))
        open(users_path, "w", encoding="utf-8").write("{not json")
        c = self.new_client()
        r = self.register(c, "corrupt@example.com")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("Traceback", r.get_data(as_text=True))
        self.assertEqual(open(users_path, encoding="utf-8").read(), "{not json")


if __name__ == "__main__":
    unittest.main()
