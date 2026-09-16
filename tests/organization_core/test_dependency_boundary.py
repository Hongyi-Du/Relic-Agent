import ast
from pathlib import Path


FORBIDDEN_IMPORT_ROOTS = {
    "agent_sdk",
    "environments",
    "organization_adapters",
    "society_core",
}


def test_organization_core_does_not_import_hosts_or_product_packages():
    package = Path(__file__).resolve().parents[2] / "organization_core"
    violations = []

    for source_path in sorted(package.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                root = name.split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{source_path.name}:{node.lineno}: {name}")

    assert violations == []
