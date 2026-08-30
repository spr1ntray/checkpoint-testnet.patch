from __future__ import annotations

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "hub_package"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugin.adspower import (
    KEY_MESSAGE,
    AdsPowerError,
    _failure,
    ads_code,
    assert_local_cdp_ws,
    assert_local_http_base,
    find_duplicate_profile_accounts,
    match_profile_row,
    normalize_api_key,
    normalize_profile_id,
)


class AdsPowerSafetyTests(unittest.TestCase):
    def test_local_http_ok(self) -> None:
        self.assertEqual(
            assert_local_http_base("http://local.adspower.net:50325/"),
            "http://local.adspower.net:50325",
        )
        self.assertEqual(
            assert_local_http_base("http://127.0.0.1:50325"),
            "http://127.0.0.1:50325",
        )

    def test_remote_http_rejected(self) -> None:
        with self.assertRaises(AdsPowerError) as ctx:
            assert_local_http_base("http://evil.example:50325")
        self.assertEqual(ctx.exception.code, "unsafe_endpoint")
        self.assertNotIn("evil.example", str(ctx.exception))

    def test_credentials_in_url_rejected(self) -> None:
        with self.assertRaises(AdsPowerError) as ctx:
            assert_local_http_base("http://user:secret@127.0.0.1:50325")
        self.assertEqual(ctx.exception.code, "unsafe_endpoint")
        self.assertNotIn("secret", str(ctx.exception))

    def test_local_ws_ok(self) -> None:
        url = "ws://127.0.0.1:19222/devtools/browser/abc"
        self.assertEqual(assert_local_cdp_ws(url), url)

    def test_remote_ws_rejected(self) -> None:
        with self.assertRaises(AdsPowerError) as ctx:
            assert_local_cdp_ws("ws://10.1.2.3:9222/devtools/browser/x")
        self.assertEqual(ctx.exception.code, "unsafe_endpoint")

    def test_duplicate_profiles(self) -> None:
        groups = find_duplicate_profile_accounts(
            [
                ("a1", "h1yynkm"),
                ("a2", "h1yynkm"),
                ("a3", "otherid1"),
                ("a4", ""),
                ("a5", "  "),
            ]
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0]), ["a1", "a2"])

    def test_code_zero_is_success(self) -> None:
        self.assertEqual(ads_code({"code": 0, "msg": "success"}), 0)
        self.assertEqual(ads_code({"code": "0"}), 0)
        self.assertIsNone(_failure({"code": 0, "msg": "success"}, default="down"))
        self.assertEqual(ads_code({"code": -1}), -1)
        err = _failure(
            {"code": -1, "msg": "API Key mismatch. Please replace it"},
            default="down",
        )
        self.assertIsNotNone(err)
        self.assertEqual(str(err), KEY_MESSAGE)

    def test_normalize_api_key(self) -> None:
        self.assertEqual(normalize_api_key('  Bearer abcdef  '), "abcdef")
        self.assertEqual(normalize_api_key('"abcdef"'), "abcdef")
        self.assertEqual(normalize_api_key("\ufeffabcdef"), "abcdef")

    def test_normalize(self) -> None:
        self.assertEqual(normalize_profile_id("  abc123  "), "abc123")
        self.assertEqual(normalize_profile_id(None), "")

    def test_zerodev_headers_include_origin(self) -> None:
        from checkpoint_bot.kernel_aa import ZERODEV_HEADERS, ZERODEV_RPC

        from checkpoint_bot.kernel_aa import DUMMY_SIG

        self.assertEqual(ZERODEV_HEADERS.get("Origin"), "https://checkpoint.exchange")
        self.assertEqual(ZERODEV_HEADERS.get("Accept"), "*/*")
        self.assertIn("rpc.zerodev.app", ZERODEV_RPC)
        self.assertTrue(DUMMY_SIG.hex().startswith("ffffffffffffffff"))
        self.assertEqual(len(DUMMY_SIG), 65)

    def test_fingerprint_pool_is_large_and_stable(self) -> None:
        from checkpoint_bot.fingerprint_pool import pool_size
        from checkpoint_bot.identity import identity_from_pool, resolve_identity

        self.assertGreaterEqual(pool_size(), 2000)
        a = identity_from_pool("0xabc")
        b = identity_from_pool("0xabc")
        c = identity_from_pool("0xdef")
        self.assertEqual(a.user_agent, b.user_agent)
        self.assertEqual(a.source, "pool")
        self.assertFalse(a.fallback)
        self.assertNotEqual(a.user_agent, c.user_agent)
        pooled = resolve_identity("0xabc", ads_row={"name": "empty"})
        self.assertEqual(pooled.source, "pool")
        ads = resolve_identity(
            "0xabc",
            ads_row={
                "fingerprint_config": {
                    "ua": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    )
                }
            },
        )
        self.assertEqual(ads.source, "ads")
        self.assertIn("Chrome/131", ads.user_agent)

    def test_low_gas_is_not_a_farm_failure(self) -> None:
        from checkpoint_bot.actions import _is_low_gas
        from checkpoint_bot.kernel_aa import LowGasError

        self.assertTrue(_is_low_gas(LowGasError("Мало ETH на газ", eth="0", need="0.00008")))
        self.assertTrue(_is_low_gas(RuntimeError("insufficient funds for gas * price + value")))
        self.assertFalse(_is_low_gas(RuntimeError("zerodev HTTP 403 allowlist")))

    def test_match_profile_row_v1_and_v2(self) -> None:
        v1 = {
            "code": 0,
            "data": {"list": [{"user_id": "h1yynkm", "ua": "Mozilla/5.0 Chrome/143.0.0.0"}]},
        }
        v2 = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "profile_id": "h1yynkm",
                        "fingerprint_config": {"ua": "Mozilla/5.0 Chrome/120.0.0.0"},
                    }
                ]
            },
        }
        self.assertEqual(match_profile_row(v1, "h1yynkm")["user_id"], "h1yynkm")
        self.assertEqual(match_profile_row(v2, "h1yynkm")["profile_id"], "h1yynkm")
        self.assertIsNone(match_profile_row(v1, "otherid1"))

    def test_identity_from_adspower_fingerprint(self) -> None:
        from checkpoint_bot.identity import identity_from_profile

        ident = identity_from_profile(
            {
                "profile_id": "h1yynkm",
                "fingerprint_config": {
                    "ua": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/143.0.0.0 Safari/537.36"
                    ),
                    "language": ["de-DE", "de", "en"],
                    "timezone": "Europe/Berlin",
                    "screen_resolution": "1440_900",
                    "hardware_concurrency": "8",
                },
                "user_proxy_config": {
                    "proxy_host": "10.0.0.1",
                    "proxy_user": "secret-user",
                    "proxy_password": "secret-pass",
                },
            }
        )
        headers = ident.headers()
        self.assertIn("Chrome/143", headers["User-Agent"])
        self.assertEqual(ident.platform, "macOS")
        self.assertEqual(ident.chrome_major, 143)
        self.assertTrue(headers["sec-ch-ua"].startswith('"Not)A;Brand"'))
        self.assertIn("143", headers["sec-ch-ua"])
        self.assertEqual(headers["sec-ch-ua-platform"], '"macOS"')
        self.assertTrue(headers["Accept-Language"].startswith("de-DE"))
        self.assertEqual(headers["Origin"], "https://checkpoint.exchange")
        self.assertFalse(ident.fallback)
        blob = str(ident) + str(headers)
        self.assertNotIn("secret-user", blob)
        self.assertNotIn("secret-pass", blob)

    def test_identity_fallback_without_ua(self) -> None:
        from checkpoint_bot.identity import identity_from_profile

        ident = identity_from_profile({})
        self.assertTrue(ident.fallback)
        self.assertIn("Chrome/143", ident.user_agent)
        self.assertEqual(ident.headers()["Origin"], "https://checkpoint.exchange")

    def test_capsolver_proxy_keeps_url(self) -> None:
        from pathlib import Path
        from checkpoint_bot.capsolver import proxy_task_fields

        fields = proxy_task_fields("http://alice:s3cret@10.1.2.3:8080")
        self.assertEqual(fields["proxy"], "http://alice:s3cret@10.1.2.3:8080")
        self.assertEqual(fields["proxyType"], "http")
        self.assertEqual(fields["proxyAddress"], "10.1.2.3")
        self.assertEqual(fields["proxyPort"], 8080)
        source = (
            Path(__file__).resolve().parents[1] / "hub_package" / "checkpoint_bot" / "capsolver.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"HCaptchaEnterpriseTask"', source)
        self.assertIn('"HCaptchaTask"', source)

    def test_personal_sign_does_not_echo_key(self) -> None:
        from eth_account import Account
        from checkpoint_bot.browser_farm import BrowserFarm

        key = "0x" + "11" * 32
        acct = Account.from_key(key)
        farm = BrowserFarm(ws_url="ws://127.0.0.1:9", account=acct, kernel=None)
        sig = farm._personal_sign("personal_sign", ["hello", acct.address])
        self.assertTrue(sig.startswith("0x"))
        self.assertNotIn("11" * 8, sig)

    def test_farm_faucet_playwright_stays_in_faucet_module(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "hub_package"
        main = (root / "plugin" / "main.py").read_text(encoding="utf-8")
        faucet = (root / "checkpoint_bot" / "faucet.py").read_text(encoding="utf-8")
        self.assertNotIn("from playwright", main)
        self.assertNotIn("connect_over_cdp", main)
        self.assertNotIn("BrowserFarm", main)
        self.assertIn("claim_sepolia_eth", main)
        self.assertIn("_claim_gas_if_needed", main)
        self.assertIn("connect_over_cdp", faucet)
        self.assertIn("faucet.quicknode.com/drip", faucet)
        self.assertIn('select[name="chain"]', faucet)
        self.assertIn('select[name="network"]', faucet)
        self.assertIn('input[name="wallet"]', faucet)
        self.assertIn("step-two-skip", faucet)
        self.assertIn("_click_enabled_continue", faucet)
        self.assertIn("_dismiss_cookies", faucet)
        self.assertIn("Decline", faucet)
        self.assertIn("invalid eth mainnet", faucet)
        self.assertIn("please come back in 12 hours", faucet)
        self.assertNotIn("Сеть уже Arbitrum Sepolia", faucet)
        self.assertIn("_wait_recaptcha_ready", faucet)
        self.assertIn("_visible_has", faucet)
        from checkpoint_bot.faucet import CONTINUE_WAIT_SEC, SEND_WAIT_SEC, TRANSFER_WAIT_SEC

        self.assertGreaterEqual(CONTINUE_WAIT_SEC, 40)
        self.assertGreaterEqual(SEND_WAIT_SEC, 80)
        self.assertGreaterEqual(TRANSFER_WAIT_SEC, 120)

    def test_needs_faucet_threshold(self) -> None:
        from checkpoint_bot.faucet import GAS_NEED_WEI, needs_faucet

        self.assertTrue(needs_faucet(0))
        self.assertTrue(needs_faucet(1))
        self.assertTrue(needs_faucet(GAS_NEED_WEI - 1))
        self.assertFalse(needs_faucet(GAS_NEED_WEI))
        self.assertFalse(needs_faucet(10**16))

    def test_needs_mainnet_for_faucet(self) -> None:
        from checkpoint_bot.faucet import MAINNET_NEED_WEI, needs_mainnet_for_faucet

        self.assertTrue(needs_mainnet_for_faucet(0))
        self.assertTrue(needs_mainnet_for_faucet(MAINNET_NEED_WEI - 1))
        self.assertFalse(needs_mainnet_for_faucet(MAINNET_NEED_WEI))
        self.assertEqual(MAINNET_NEED_WEI, 10**15)

    def test_manifest_1_7_15_ads_faucet(self) -> None:
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "hub_package"
        manifest = json.loads((root / "hub.plugin.json").read_text())
        self.assertEqual(manifest["version"], "1.7.15")
        self.assertTrue(manifest["permissions"]["browser"])
        self.assertEqual(manifest["permissions"]["local_services"], ["adspower"])
        self.assertIn("adspower_profile", manifest["permissions"]["secrets"])
        self.assertIn("adspower_api_key", manifest["permissions"]["secrets"])
        self.assertIn("faucet.quicknode.com", manifest["permissions"]["network"])
        self.assertIn("local.adspower.com", manifest["permissions"]["network"])
        farm = next(action for action in manifest["actions"] if action["id"] == "farm")
        self.assertEqual(farm["options"]["properties"]["account_concurrency"]["maximum"], 5)
        props = farm["options"]["properties"]
        self.assertEqual(props["trades_min"]["minimum"], 5)
        self.assertEqual(props["trades_max"]["maximum"], 10)
        self.assertEqual(props["trades_min"]["x-ui"]["control"], "dual_range")
        self.assertEqual(props["trades_max"]["x-ui"]["control"], "dual_range")
        self.assertEqual(props["trades_min"]["x-ui"]["range"]["role"], "from")
        self.assertEqual(props["trades_max"]["x-ui"]["range"]["role"], "to")
        self.assertEqual(props["trades_min"]["title"], props["trades_max"]["title"])
        self.assertNotIn("max_usdc_per_fill", props)
        self.assertNotIn("trades", props)
        self.assertIn("ethereum.publicnode.com", manifest["permissions"]["network"])
        self.assertIn("adspower_profile", farm["resources"]["account"])
        self.assertIn("adspower_api", farm["resources"]["settings"])
        self.assertIn("adspower_profile", farm["permissions"]["secrets"])
        self.assertNotIn("capsolver_api_key", farm["permissions"]["secrets"])
        inspect_action = next(action for action in manifest["actions"] if action["id"] == "inspect")
        self.assertEqual(inspect_action["options"]["properties"]["account_concurrency"]["maximum"], 5)
        self.assertNotIn("adspower_profile", inspect_action["permissions"]["secrets"])
        self.assertNotIn("capsolver_api_key", inspect_action["permissions"]["secrets"])
        kernel = (root / "checkpoint_bot" / "kernel_aa.py").read_text(encoding="utf-8")
        self.assertIn("hex(self.cfg.chain_id), None]", kernel)
        self.assertIn("playwright", (root / "requirements.txt").read_text())


if __name__ == "__main__":
    unittest.main()
