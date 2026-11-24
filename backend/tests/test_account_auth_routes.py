"""Comprehensive FastAPI TestClient suite for backend/app/account_auth.py."""
import copy
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from app import account_auth
from app.auth import (
    BIOMETRIC_SESSION_SECONDS,
    OWNER_ID,
    SESSION_SECONDS,
    login_attempts,
    owner_cache,
    verify_password,
)


class StatefulAccountCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.bump_version_before_next_update = False

    def _matches(self, doc: dict, query: dict) -> bool:
        for k, v in (query or {}).items():
            if k == "sessions.id":
                if not any(s.get("id") == v for s in doc.get("sessions", [])):
                    return False
            elif doc.get(k) != v:
                return False
        return True

    def find_one(self, query: dict | None = None):
        for doc in self.docs:
            if self._matches(doc, query or {}):
                return copy.deepcopy(doc)
        return None

    def insert_one(self, doc: dict):
        if any(existing.get("_id") == doc.get("_id") for existing in self.docs):
            raise DuplicateKeyError("duplicate _id")
        stored = copy.deepcopy(doc)
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    def update_one(self, query: dict, update):
        if self.bump_version_before_next_update and self.docs:
            self.bump_version_before_next_update = False
            self.docs[0]["auth_version"] = int(self.docs[0].get("auth_version", 1)) + 99

        target = next((d for d in self.docs if self._matches(d, query)), None)
        if target is None:
            return SimpleNamespace(matched_count=0, modified_count=0)

        if isinstance(update, list):
            stage = update[0].get("$set", {}).get("sessions", {})
            slice_args = stage.get("$slice", [])
            concat_args = slice_args[0].get("$concatArrays", [])
            filter_spec = concat_args[0].get("$filter", {})
            cutoff_now = filter_spec.get("cond", {}).get("$gt", [None, 0])[1]
            appended = copy.deepcopy(list(concat_args[1].get("$literal", [])))
            cap = abs(int(slice_args[1]))
            kept = [s for s in target.get("sessions", []) if int(s.get("expires_at") or 0) > cutoff_now]
            target["sessions"] = (kept + appended)[-cap:]
            return SimpleNamespace(matched_count=1, modified_count=1)

        sets = update.get("$set", {})
        sid = (query or {}).get("sessions.id")
        for k, v in sets.items():
            if k.startswith("sessions.$."):
                field = k.split("sessions.$.", 1)[1]
                for s in target.get("sessions", []):
                    if s.get("id") == sid:
                        s[field] = copy.deepcopy(v)
                        break
            else:
                target[k] = copy.deepcopy(v)
        for k, v in update.get("$inc", {}).items():
            target[k] = int(target.get(k, 0)) + v
        if "$pull" in update and "sessions" in update["$pull"]:
            cond = update["$pull"]["sessions"]
            target["sessions"] = [s for s in target.get("sessions", []) if s.get("id") != cond.get("id")]
        return SimpleNamespace(matched_count=1, modified_count=1)


class StatefulAuditCollection:
    def __init__(self):
        self.docs: list[dict] = []

    def insert_one(self, doc: dict):
        self.docs.append(copy.deepcopy(doc))
        return SimpleNamespace(inserted_id=len(self.docs))


class AccountAuthRoutesTests(unittest.TestCase):
    def setUp(self):
        login_attempts.attempts.clear()
        owner_cache.invalidate()
        self.settings = SimpleNamespace(
            jwt_secret="k" * 48,
            environment="production",
            app_password="",
            account_setup_token="s" * 48,
        )
        self.p1 = patch("app.auth.get_settings", return_value=self.settings)
        self.p2 = patch("app.account_auth.get_settings", return_value=self.settings)
        self.p1.start()
        self.p2.start()

        self.db = SimpleNamespace(
            account=StatefulAccountCollection(),
            audit_log=StatefulAuditCollection(),
        )
        self.app = FastAPI()
        self.app.include_router(account_auth.router)
        self.app.state.db = self.db
        self.client = TestClient(self.app)

    def tearDown(self):
        self.p2.stop()
        self.p1.stop()
        login_attempts.attempts.clear()
        owner_cache.invalidate()

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _setup_owner(
        self,
        username: str = "daniel.owner",
        display_name: str = "Daniel Owner",
        password: str = "SenhaForteSegura123!",
        device_label: str = "Pixel 9",
    ) -> dict:
        resp = self.client.post(
            "/v1/auth/setup",
            json={
                "username": username,
                "display_name": display_name,
                "password": password,
                "setup_token": self.settings.account_setup_token,
                "device_label": device_label,
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def test_status_transitions_across_unconfigured_setup_legacy_and_configured_owner(self):
        # 1. Unconfigured jwt_secret (<32 chars)
        self.settings.jwt_secret = "short"
        r_unconf = self.client.get("/v1/auth/status").json()
        self.assertEqual(
            r_unconf,
            {
                "auth_required": True,
                "configured": False,
                "setup_required": True,
                "setup_available": False,
                "legacy_available": False,
            },
        )

        # 2. Configured jwt_secret + setup token + legacy app_password, no owner yet
        self.settings.jwt_secret = "k" * 48
        self.settings.app_password = "legacy-server-password"
        r_ready = self.client.get("/v1/auth/status").json()
        self.assertEqual(
            r_ready,
            {
                "auth_required": True,
                "configured": True,
                "setup_required": True,
                "setup_available": True,
                "legacy_available": True,
            },
        )

        # 3. After owner setup: setup_required=False, legacy_available=False
        self._setup_owner()
        r_owner = self.client.get("/v1/auth/status").json()
        self.assertEqual(
            r_owner,
            {
                "auth_required": True,
                "configured": True,
                "setup_required": False,
                "setup_available": True,
                "legacy_available": False,
            },
        )

    def test_setup_validation_invalid_token_valid_creation_and_duplicate_conflict(self):
        # Username / display_name / password validation -> 422
        for bad_payload in (
            {"username": "a!", "display_name": "Daniel", "password": "SenhaForteSegura123!", "setup_token": "s" * 48},
            {"username": "validuser", "display_name": "   ", "password": "SenhaForteSegura123!", "setup_token": "s" * 48},
            {"username": "validuser", "display_name": "Bad\x07Name", "password": "SenhaForteSegura123!", "setup_token": "s" * 48},
            {"username": "validuser", "display_name": "Daniel", "password": "short", "setup_token": "s" * 48},
        ):
            with self.subTest(username=bad_payload["username"], display=bad_payload["display_name"]):
                resp = self.client.post("/v1/auth/setup", json=bad_payload)
                self.assertEqual(resp.status_code, 422)

        # Invalid setup_token -> 401
        bad_tok = self.client.post(
            "/v1/auth/setup",
            json={
                "username": "daniel",
                "display_name": "Daniel",
                "password": "SenhaForteSegura123!",
                "setup_token": "w" * 48,
            },
        )
        self.assertEqual(bad_tok.status_code, 401)
        self.assertEqual(len(self.db.account.docs), 0)

        # Valid setup -> 201
        created = self._setup_owner(username="  Daniel.Owner  ", display_name="  Daniel Owner  ")
        self.assertEqual(created["account"], {"username": "daniel.owner", "display_name": "Daniel Owner"})
        self.assertEqual(created["expires_in"], BIOMETRIC_SESSION_SECONDS)
        self.assertEqual(len(self.db.account.docs), 1)
        self.assertEqual(self.db.account.docs[0]["_id"], OWNER_ID)

        # Duplicate setup -> 409
        dup = self.client.post(
            "/v1/auth/setup",
            json={
                "username": "outro",
                "display_name": "Outro",
                "password": "SenhaForteSegura123!",
                "setup_token": self.settings.account_setup_token,
            },
        )
        self.assertEqual(dup.status_code, 409)

    def test_legacy_migration_owner_login_and_bruteforce_throttle_reset(self):
        self.settings.app_password = "LegacyPassword#2026"

        # Wrong legacy password -> 401 and does not create owner
        wrong_leg = self.client.post("/v1/auth/session", json={"password": "WrongLegacyPassword"})
        self.assertEqual(wrong_leg.status_code, 401)
        self.assertEqual(len(self.db.account.docs), 0)

        # Correct legacy password -> 200 one-time migration to 'proprietario'
        migrated = self.client.post(
            "/v1/auth/session",
            json={"password": "LegacyPassword#2026", "device_label": "Navegador"},
        )
        self.assertEqual(migrated.status_code, 200)
        self.assertEqual(migrated.json()["account"], {"username": "proprietario", "display_name": "Minha conta"})
        self.assertEqual(migrated.json()["expires_in"], SESSION_SECONDS)
        self.assertEqual(len(self.db.account.docs), 1)

        # Subsequent login requires matching username + password (legacy blank username rejected!)
        missing_user = self.client.post("/v1/auth/session", json={"username": "", "password": "LegacyPassword#2026"})
        self.assertEqual(missing_user.status_code, 401)
        wrong_pw = self.client.post("/v1/auth/session", json={"username": "proprietario", "password": "wrong"})
        self.assertEqual(wrong_pw.status_code, 401)

        # Valid normal owner login resets throttle
        ok_login = self.client.post(
            "/v1/auth/session",
            json={"username": "PROPRIETARIO", "password": "LegacyPassword#2026", "device_label": "Android"},
        )
        self.assertEqual(ok_login.status_code, 200)
        self.assertEqual(ok_login.json()["expires_in"], BIOMETRIC_SESSION_SECONDS)

        # Brute-force throttle: 10 attempts allowed in 60s window, 11th returns 429 with Retry-After
        for _ in range(10):
            r = self.client.post("/v1/auth/session", json={"username": "proprietario", "password": "bad"})
            self.assertEqual(r.status_code, 401)
        throttled = self.client.post("/v1/auth/session", json={"username": "proprietario", "password": "bad"})
        self.assertEqual(throttled.status_code, 429)
        self.assertEqual(throttled.headers.get("retry-after"), "60")

        # Advance monotonic clock >60s -> valid login succeeds and resets throttle counter
        future_mono = time.monotonic() + 65
        with patch("app.auth.time.monotonic", return_value=future_mono):
            recovered = self.client.post(
                "/v1/auth/session",
                json={"username": "proprietario", "password": "LegacyPassword#2026"},
            )
            self.assertEqual(recovered.status_code, 200)
            # Throttle was reset, so another 10 attempts are available immediately
            after_reset = self.client.post(
                "/v1/auth/session",
                json={"username": "proprietario", "password": "bad"},
            )
            self.assertEqual(after_reset.status_code, 401)

    def test_account_sliding_headers_biometric_refresh_and_profile_patch_with_conflict(self):
        web_session = self._setup_owner(device_label="Navegador")
        token = web_session["access_token"]
        self.assertEqual(web_session["expires_in"], SESSION_SECONDS)

        # GET /v1/auth/account emits X-Session-Token and X-Session-Expires-In headers
        acc_resp = self.client.get("/v1/auth/account", headers=self._auth_headers(token))
        self.assertEqual(acc_resp.status_code, 200)
        self.assertEqual(acc_resp.json(), {"username": "daniel.owner", "display_name": "Daniel Owner"})
        self.assertIn("X-Session-Token", acc_resp.headers)
        self.assertEqual(acc_resp.headers.get("X-Session-Expires-In"), str(SESSION_SECONDS))

        # POST /v1/auth/biometric upgrades/renews session to 30 days
        bio_resp = self.client.post("/v1/auth/biometric", headers=self._auth_headers(token))
        self.assertEqual(bio_resp.status_code, 200)
        self.assertEqual(bio_resp.json()["expires_in"], BIOMETRIC_SESSION_SECONDS)
        bio_token = bio_resp.json()["access_token"]

        # PATCH /v1/auth/account bumps auth_version, writes audit_log, invalidates prior token
        patch_resp = self.client.patch(
            "/v1/auth/account",
            headers=self._auth_headers(bio_token),
            json={"username": "daniel.novo", "display_name": "Daniel Novo"},
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()["account"], {"username": "daniel.novo", "display_name": "Daniel Novo"})
        self.assertEqual(self.db.account.docs[0]["auth_version"], 2)
        self.assertEqual(len(self.db.audit_log.docs), 1)
        self.assertEqual(self.db.audit_log.docs[0]["event"], "account_profile_updated")
        self.assertEqual(self.db.audit_log.docs[0]["before"]["username"], "daniel.owner")
        self.assertEqual(self.db.audit_log.docs[0]["after"]["username"], "daniel.novo")

        # Old token with ver=1 is now rejected
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(bio_token)).status_code, 401)

        # Concurrent stale auth_version conflict during PATCH /v1/auth/account -> 409
        fresh_token = patch_resp.json()["access_token"]
        self.db.account.bump_version_before_next_update = True
        conflict_resp = self.client.patch(
            "/v1/auth/account",
            headers=self._auth_headers(fresh_token),
            json={"username": "daniel.conflito", "display_name": "Conflito"},
        )
        self.assertEqual(conflict_resp.status_code, 409)

    def test_password_rotation_validation_session_wipe_and_concurrent_conflict(self):
        s1 = self._setup_owner(password="SenhaInicial123!", device_label="Phone-1")
        s2 = self.client.post(
            "/v1/auth/session",
            json={"username": "daniel.owner", "password": "SenhaInicial123!", "device_label": "Laptop-2"},
        ).json()
        self.assertEqual(len(self.db.account.docs[0]["sessions"]), 2)

        # Wrong current_password -> 403
        wrong_cur = self.client.post(
            "/v1/auth/password",
            headers=self._auth_headers(s1["access_token"]),
            json={"current_password": "ErradaPassword123!", "new_password": "NovaSenhaSegura456!"},
        )
        self.assertEqual(wrong_cur.status_code, 403)

        # Same password -> 422
        same_pw = self.client.post(
            "/v1/auth/password",
            headers=self._auth_headers(s1["access_token"]),
            json={"current_password": "SenhaInicial123!", "new_password": "SenhaInicial123!"},
        )
        self.assertEqual(same_pw.status_code, 422)

        # Valid rotation -> 200, rotates hash, wipes old sessions & issues 1 new session, bumps auth_version
        rotated = self.client.post(
            "/v1/auth/password",
            headers=self._auth_headers(s1["access_token"]),
            json={
                "current_password": "SenhaInicial123!",
                "new_password": "NovaSenhaSegura456!",
                "device_label": "Phone-Rotated",
            },
        )
        self.assertEqual(rotated.status_code, 200)
        new_token = rotated.json()["access_token"]
        stored_acc = self.db.account.docs[0]
        self.assertEqual(stored_acc["auth_version"], 2)
        self.assertTrue(verify_password("NovaSenhaSegura456!", stored_acc["password_hash"]))
        self.assertEqual(len(stored_acc["sessions"]), 1)
        self.assertEqual(stored_acc["sessions"][0]["label"], "Phone-Rotated")

        # Both previous tokens are invalidated; new token works
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(s1["access_token"])).status_code, 401)
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(s2["access_token"])).status_code, 401)
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(new_token)).status_code, 200)

        # Concurrent update conflict on POST /v1/auth/password -> 409
        self.db.account.bump_version_before_next_update = True
        conflict = self.client.post(
            "/v1/auth/password",
            headers=self._auth_headers(new_token),
            json={"current_password": "NovaSenhaSegura456!", "new_password": "OutraSenhaSegura789!"},
        )
        self.assertEqual(conflict.status_code, 409)

    def test_sessions_listing_revoke_logout_and_logout_all(self):
        s1 = self._setup_owner(password="SenhaForteSegura123!", device_label="Device-A")
        s2 = self.client.post(
            "/v1/auth/session",
            json={"username": "daniel.owner", "password": "SenhaForteSegura123!", "device_label": "Device-B"},
        ).json()
        s3 = self.client.post(
            "/v1/auth/session",
            json={"username": "daniel.owner", "password": "SenhaForteSegura123!", "device_label": "Device-C"},
        ).json()

        listed = self.client.get("/v1/auth/sessions", headers=self._auth_headers(s1["access_token"]))
        self.assertEqual(listed.status_code, 200)
        items = listed.json()["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual(sum(1 for it in items if it["current"]), 1)
        s2_id = next(it["id"] for it in items if it["label"] == "Device-B")

        # Invalid session hex -> 422
        bad_del = self.client.delete("/v1/auth/sessions/not-a-valid-64-hex", headers=self._auth_headers(s1["access_token"]))
        self.assertEqual(bad_del.status_code, 422)

        # Revoke Device-B -> 200; Device-B token becomes 401 while Device-A and Device-C stay active
        rev_del = self.client.delete(f"/v1/auth/sessions/{s2_id}", headers=self._auth_headers(s1["access_token"]))
        self.assertEqual(rev_del.status_code, 200)
        self.assertEqual(rev_del.json(), {"revoked": True})
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(s2["access_token"])).status_code, 401)
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(s1["access_token"])).status_code, 200)

        # POST /v1/auth/logout revokes only current session (Device-A); Device-C remains active
        out_a = self.client.post("/v1/auth/logout", headers=self._auth_headers(s1["access_token"]))
        self.assertEqual(out_a.status_code, 200)
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(s1["access_token"])).status_code, 401)
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(s3["access_token"])).status_code, 200)

        # POST /v1/auth/logout-all bumps auth_version and wipes all sessions
        s4 = self.client.post(
            "/v1/auth/session",
            json={"username": "daniel.owner", "password": "SenhaForteSegura123!", "device_label": "Device-D"},
        ).json()
        ver_before = self.db.account.docs[0]["auth_version"]
        out_all = self.client.post("/v1/auth/logout-all", headers=self._auth_headers(s3["access_token"]))
        self.assertEqual(out_all.status_code, 200)
        self.assertEqual(out_all.json(), {"revoked": True})
        self.assertEqual(self.db.account.docs[0]["auth_version"], ver_before + 1)
        self.assertEqual(self.db.account.docs[0]["sessions"], [])
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(s3["access_token"])).status_code, 401)
        self.assertEqual(self.client.get("/v1/auth/account", headers=self._auth_headers(s4["access_token"])).status_code, 401)


if __name__ == "__main__":
    unittest.main()
