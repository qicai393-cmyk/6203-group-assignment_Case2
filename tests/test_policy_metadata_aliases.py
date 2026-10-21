"""Offline public-schema regressions; no model call or tool body is executed."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import team_policy


class PolicyMetadataAliasTests(unittest.TestCase):
    def metadata(self, sources):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "agentdojo"
            tools = package / "default_suites" / "v1" / "tools"
            tools.mkdir(parents=True)
            for name, source in sources.items():
                (tools / name).write_text(source, encoding="utf-8")
            # Only AST is read: these fixtures deliberately have no imports or
            # executable environment objects. Do not import or invoke tools.
            with patch.object(team_policy, "agentdojo", SimpleNamespace(__file__=str(package / "__init__.py"))):
                return team_policy.load_tool_metadata()

    def test_pinned_get_channels_has_no_caller_slack_argument(self):
        metadata = team_policy.load_tool_metadata()
        self.assertEqual(metadata["get_channels"]["parameters"], [])

    def test_pinned_get_webpage_keeps_only_required_url(self):
        metadata = team_policy.load_tool_metadata()
        self.assertEqual(metadata["get_webpage"]["parameters"], [
            {"name": "url", "type": "str", "required": True}
        ])

    def test_recursive_aliases_omit_environment_but_preserve_caller_parameters(self):
        metadata = self.metadata({"tools.py": '''
Injected = Annotated[Environment, Depends("environment")]
Nested = Injected
Forward = "Nested"
ResourceID = str
def find_record(environment: Forward, record_id: ResourceID, *, limit: int = 5) -> str:
    """Find one requested record."""
    raise AssertionError("Tool body must never run")
'''})
        self.assertEqual(metadata["find_record"]["parameters"], [
            {"name": "record_id", "type": "ResourceID", "required": True},
            {"name": "limit", "type": "int", "required": False, "default": "5"},
        ])

    def test_ordinary_alias_and_literal_fields_remain_required(self):
        metadata = self.metadata({"tools.py": '''
RecordID = str
def read_record(record_id: RecordID, label: Literal["Depends"]):
    """Read the requested record."""
    raise AssertionError("Tool body must never run")
'''})
        self.assertEqual([p["name"] for p in metadata["read_record"]["parameters"]],
                         ["record_id", "label"])
        self.assertTrue(all(p["required"] for p in metadata["read_record"]["parameters"]))

    def test_cyclic_alias_is_unavailable_without_losing_valid_tools(self):
        metadata = self.metadata({"tools.py": '''
A = B
B = A
def broken(environment: A):
    """Unresolved environment dependency."""
    pass
def safe(record_id: str):
    """Read the requested record."""
    pass
'''})
        self.assertNotIn("broken", metadata)
        self.assertIn("safe", metadata)

    def test_unsupported_alias_is_unavailable(self):
        metadata = self.metadata({"tools.py": '''
Injected = arbitrary_factory()
def broken(environment: Injected):
    """Unresolved environment dependency."""
    pass
'''})
        self.assertNotIn("broken", metadata)

    def test_invalid_definition_cannot_hide_a_duplicate_name(self):
        metadata = self.metadata({"a.py": '''
A = B
B = A
def same(environment: A):
    """Unresolved dependency."""
    pass
''', "b.py": '''
def same(record_id: str):
    """A different implementation."""
    pass
'''})
        self.assertNotIn("same", metadata)


if __name__ == "__main__":
    unittest.main()
