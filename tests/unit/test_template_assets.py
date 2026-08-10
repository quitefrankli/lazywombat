import ast
import re
from pathlib import Path


TEMPLATES_ROOT = Path("web_app")
INLINE_EVENT_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
INLINE_STYLE_ATTRIBUTE_RE = re.compile(r"\sstyle\s*=", re.IGNORECASE)
STYLE_ELEMENT_RE = re.compile(r"<style\b", re.IGNORECASE)
SCRIPT_ELEMENT_RE = re.compile(
    r"<script\b(?P<attributes>[^>]*)>(?P<body>.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


def test_templates_keep_css_and_executable_javascript_in_static_assets():
    violations: list[str] = []

    for template in sorted(TEMPLATES_ROOT.glob("**/templates/**/*.html")):
        source = template.read_text(encoding="utf-8")

        if STYLE_ELEMENT_RE.search(source):
            violations.append(f"{template}: embedded <style> element")
        if INLINE_STYLE_ATTRIBUTE_RE.search(source):
            violations.append(f"{template}: style attribute")
        if INLINE_EVENT_HANDLER_RE.search(source):
            violations.append(f"{template}: inline event handler")

        for script in SCRIPT_ELEMENT_RE.finditer(source):
            attributes = script.group("attributes")
            if re.search(r"\bsrc\s*=", attributes, re.IGNORECASE):
                continue
            if re.search(
                r"\btype\s*=\s*['\"]application/json['\"]",
                attributes,
                re.IGNORECASE,
            ):
                continue
            if script.group("body").strip():
                violations.append(f"{template}: inline executable script")

    assert not violations, "\n" + "\n".join(violations)


def test_data_interfaces_do_not_expose_bare_model_save_methods():
    interface_modules = (
        Path("web_app/data_interface.py"),
        Path("web_app/file_store/data_interface.py"),
        Path("web_app/tubio/data_interface.py"),
    )
    forbidden = {"save_model", "save_users", "save_metadata", "save_report", "save_audio_metadata"}

    exposed = set()
    for module_path in interface_modules:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for class_node in (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "DataInterface"
        ):
            for member in class_node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if member.name in forbidden:
                        exposed.add(f"{module_path}:{member.lineno}:{member.name}")

    assert not exposed, "\n" + "\n".join(sorted(exposed))
