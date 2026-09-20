from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIFIED_MODULES = [
    ROOT / "pipeline" / "branches" / "gmm" / "extract.py",
    ROOT / "pipeline" / "branches" / "gmm" / "validate.py",
    ROOT / "pipeline" / "branches" / "gmm" / "parser.py",
]
VALID_CLASSIFICATIONS = {"COMMON", "GMM-SPECIFIC"}


def module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def top_level_functions(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def module_constants(tree: ast.Module) -> dict[str, str]:
    constants = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            constants[node.targets[0].id] = node.value.value
    return constants


def function_classification(tree: ast.Module) -> dict[str, str]:
    constants = module_constants(tree)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "FUNCTION_CLASSIFICATION" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            raise AssertionError("FUNCTION_CLASSIFICATION must be a dict")
        classifications = {}
        for key_node, value_node in zip(node.value.keys, node.value.values):
            key = ast.literal_eval(key_node)
            if isinstance(value_node, ast.Name):
                value = constants[value_node.id]
            else:
                value = ast.literal_eval(value_node)
            classifications[key] = value
        return classifications
    raise AssertionError("FUNCTION_CLASSIFICATION is missing")


class FunctionClassificationTests(unittest.TestCase):
    def test_all_gmm_functions_are_marked_common_or_gmm_specific(self):
        for path in CLASSIFIED_MODULES:
            with self.subTest(path=path.relative_to(ROOT)):
                tree = module_tree(path)
                functions = top_level_functions(tree)
                classifications = function_classification(tree)

                self.assertEqual(functions - classifications.keys(), set())
                self.assertEqual(classifications.keys() - functions, set())
                self.assertTrue(set(classifications.values()) <= VALID_CLASSIFICATIONS)


if __name__ == "__main__":
    unittest.main()
