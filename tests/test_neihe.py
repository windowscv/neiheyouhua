"""在隔离 Linux 容器中运行真实脚本，验证配置覆盖、参数应用和失败行为。"""

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
CONFIG = Path("/etc/sysctl.d/99-sysctl.conf")
PROTECTED = [
    Path("/etc/sysctl.conf"),
    Path("/etc/systemd/system.conf"),
    Path("/etc/security/limits.conf"),
    Path("/etc/profile"),
    Path("/etc/pam.d/common-session"),
]

# 按已确认的吞吐、并发目标手工列出期望，和被测脚本独立维护。
EXPECTED = {
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
    "net.core.rmem_max": "67108864",
    "net.core.wmem_max": "67108864",
    "net.ipv4.tcp_rmem": "4096 262144 67108864",
    "net.ipv4.tcp_wmem": "4096 262144 67108864",
    "net.core.netdev_max_backlog": "65536",
    "net.core.netdev_budget": "600",
    "net.core.netdev_budget_usecs": "4000",
    "net.core.somaxconn": "65535",
    "net.ipv4.tcp_max_syn_backlog": "65536",
    "net.ipv4.ip_local_port_range": "10240 65535",
    "net.ipv4.tcp_tw_reuse": "1",
    "net.ipv4.tcp_fin_timeout": "30",
    "net.ipv4.tcp_mtu_probing": "1",
    "net.ipv4.tcp_slow_start_after_idle": "0",
    "net.ipv4.tcp_fastopen": "3",
    "net.ipv4.tcp_keepalive_time": "300",
    "net.ipv4.tcp_keepalive_intvl": "30",
    "net.ipv4.tcp_keepalive_probes": "5",
}
BASE_VALUES = {
    "net.ipv4.tcp_retries2": "15",
    "net.ipv4.tcp_synack_retries": "5",
    "net.core.rmem_default": "212992",
    "net.core.wmem_default": "212992",
    "net.ipv4.ip_forward": "0",
    "vm.overcommit_memory": "0",
    "net.netfilter.nf_conntrack_tcp_timeout_established": "432000",
}


def parse_config(text):
    """解析公开输出，同时检查重复键，防止重复运行堆积配置。"""
    result = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key in result:
            raise AssertionError(f"duplicate setting: {key}")
        result[key] = value
    return result


class ScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 测试要写容器的 /etc；拒绝直接在开发机或生产机运行。
        if not Path("/.dockerenv").exists() or os.geteuid() != 0:
            raise RuntimeError("请按 README 在 root Docker 容器中运行测试。")

    def setUp(self):
        self.originals = {}
        for path in [CONFIG, *PROTECTED]:
            if path.is_symlink():
                self.originals[path] = ("link", os.readlink(path), None)
            elif path.exists():
                self.originals[path] = (
                    "file", path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
                )
            else:
                self.originals[path] = ("missing", None, None)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
            path.write_text(f"# existing configuration: {path}\n")
            path.chmod(0o644)
        self.initial_config = CONFIG.read_bytes()
        self.initial_protected = {path: path.read_bytes() for path in PROTECTED}
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        root.chmod(0o755)
        self.state_path = root / "state.json"
        self.state_path.write_text(json.dumps({
            "os": "Linux", "available": "reno cubic", "modules": [],
            "calls": [], "values": BASE_VALUES.copy(),
        }))
        bindir = root / "bin"
        bindir.mkdir()
        for command in ["sysctl", "modprobe", "uname", "reboot"]:
            (bindir / command).symlink_to(REPO / "tests/fake_kernel.py")
        self.env = os.environ | {
            "PATH": f"{bindir}:/usr/sbin:/usr/bin:/sbin:/bin",
            "NEIHE_TEST_STATE": str(self.state_path),
        }

    def tearDown(self):
        for path, (kind, data, mode) in self.originals.items():
            path.unlink(missing_ok=True)
            if kind == "link":
                path.symlink_to(data)
            elif kind == "file":
                path.write_bytes(data)
                path.chmod(mode)
        self.temp.cleanup()

    def state(self):
        return json.loads(self.state_path.read_text())

    def set_state(self, **updates):
        self.state_path.write_text(json.dumps(self.state() | updates))

    def run_script(self, *args, unprivileged=False, piped=False):
        def drop_privileges():
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)

        env = self.env.copy()
        if unprivileged:
            env["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
        command = ["bash", "-s", "--", *args] if piped else [
            "bash", str(REPO / "neihe.sh"), *args
        ]
        return subprocess.run(
            command, input=(REPO / "neihe.sh").read_text() if piped else None,
            text=True, capture_output=True, env=env, cwd=self.temp.name,
            preexec_fn=drop_privileges if unprivileged else None,
            timeout=15,
        )

    def assert_protected_unchanged(self):
        for path, content in self.initial_protected.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def assert_no_changes(self):
        self.assertEqual(CONFIG.read_bytes(), self.initial_config)
        self.assert_protected_unchanged()
        self.assertEqual(self.state()["values"], BASE_VALUES)

    def test_preview_emits_profile_without_side_effects(self):
        result = self.run_script("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_config(result.stdout), EXPECTED)
        self.assertEqual(self.state()["calls"], [])
        self.assert_no_changes()

    def test_help_does_not_apply_settings(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dry-run", result.stdout)
        self.assertEqual(self.state()["calls"], [])
        self.assert_no_changes()

    def test_unknown_option_is_rejected(self):
        result = self.run_script("--unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_changes()

    def test_extra_arguments_are_rejected(self):
        result = self.run_script("--dry-run", "extra")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_changes()

    def test_apply_requires_root(self):
        result = self.run_script(unprivileged=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root", result.stderr)
        self.assert_no_changes()

    def test_preview_works_without_root(self):
        result = self.run_script("--dry-run", unprivileged=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_config(result.stdout), EXPECTED)
        self.assert_no_changes()

    def test_apply_requires_linux(self):
        self.set_state(os="Darwin")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Linux", result.stderr)
        self.assert_no_changes()

    def test_apply_overwrites_old_profile_and_preserves_other_files(self):
        CONFIG.write_text("net.ipv4.tcp_retries2 = 5\nnet.core.default_qdisc = cake\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_config(CONFIG.read_text()), EXPECTED)
        self.assertEqual(self.state()["values"], BASE_VALUES | EXPECTED)
        self.assert_protected_unchanged()
        self.assertNotIn(["reboot"], self.state()["calls"])
        self.assertEqual(stat.S_IMODE(CONFIG.stat().st_mode), 0o644)

    def test_repeat_apply_is_idempotent(self):
        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        config = CONFIG.read_bytes()
        second = self.run_script()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(CONFIG.read_bytes(), config)
        self.assertEqual(self.state()["values"], BASE_VALUES | EXPECTED)
        self.assert_protected_unchanged()

    def test_existing_bbr_skips_loading_its_module(self):
        self.set_state(available="reno cubic bbr")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()["modules"], ["sch_fq"])

    def test_missing_module_does_not_persist_config(self):
        for module in ["tcp_bbr", "sch_fq"]:
            with self.subTest(module=module):
                self.set_state(missing_module=module, available="reno cubic")
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_changes()

    def test_sysctl_read_error_does_not_persist_config(self):
        self.set_state(read_error=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_changes()

    def test_sysctl_apply_error_preserves_previous_config(self):
        self.set_state(apply_error=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("已应用", result.stdout)
        self.assert_no_changes()

    def test_target_symlink_does_not_overwrite_linked_system_config(self):
        CONFIG.unlink()
        CONFIG.symlink_to(PROTECTED[0])
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(CONFIG.is_symlink())
        self.assertEqual(parse_config(CONFIG.read_text()), EXPECTED)
        self.assert_protected_unchanged()

    def test_original_curl_pipe_invocation_still_works(self):
        result = self.run_script(piped=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_config(CONFIG.read_text()), EXPECTED)
        self.assert_protected_unchanged()


if __name__ == "__main__":
    unittest.main()
