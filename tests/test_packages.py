import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("packager", ROOT / "scripts/package-plugin.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackagesTest(unittest.TestCase):
    def test_directory_bundle_is_self_contained_and_has_no_local_server(self):
        with tempfile.TemporaryDirectory() as temp:
            with zipfile.ZipFile(packager.package("directory", temp)) as bundle:
                names = set(bundle.namelist())
                self.assertFalse(names & {"mcp.json", ".mcp.json", ".app.json"})
                self.assertFalse(any(n.startswith(("server/", "bin/", "native/")) for n in names))
                manifest = json.loads(bundle.read("plugin.json"))
                extension = manifest["extensions"]["com.openai"]
                self.assertIn(extension["onboardingSkill"][2:], names)
                interface = extension["interface"]
                for field in ("logo", "composerIcon"):
                    self.assertIn(interface[field][2:], names)
                self.assertLessEqual(len(interface["displayName"]), 30)
                self.assertLessEqual(len(interface["shortDescription"]), 30)
                self.assertIn("LICENSE", names)

    def test_desktop_bundle_excludes_user_cache_and_native_build_products(self):
        with tempfile.TemporaryDirectory() as temp:
            with zipfile.ZipFile(packager.package("desktop", temp)) as bundle:
                names = bundle.namelist()
                self.assertFalse(any(n.startswith(("bin/", ".build/", ".meeting-cache/")) for n in names))
                self.assertFalse(any("__pycache__" in n or n.endswith(".pyc") for n in names))
                config = json.loads(bundle.read("config.json"))
                self.assertNotIn("meetings_page_id", config)
                self.assertNotIn("meetings_page_url", config)
                for manifest in ("mcp.json", ".mcp.json"):
                    server = json.loads(bundle.read(manifest))["mcpServers"]["meeting-notes"]
                    self.assertNotIn("MEETING_NOTES_HOME", server.get("env", {}))
