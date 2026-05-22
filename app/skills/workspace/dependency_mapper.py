import ast
import asyncio

from pathlib import Path
from typing import Dict, Any, Set, Tuple

from app.skills.registry import register_tool

def _extract_ast_data(tree: ast.Module) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    """Internal helper: Extracts imports and top-level exports natively."""
    imports: Set[str] = set()
    from_imports: Set[str] = set()
    defined_classes: Set[str] = set()
    defined_functions: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level if node.level else ""
            module = node.module or ""
            full_module = f"{prefix}{module}"
            
            for alias in node.names:
                from_imports.add(f"from {full_module} import {alias.name}")

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            defined_classes.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_functions.add(node.name)

    return imports, from_imports, defined_classes, defined_functions

def _format_architecture_report(file_name: str, imports: Set[str], from_imports: Set[str], classes: Set[str], functions: Set[str]) -> str:
    """Internal helper: Formats the extracted data into a readable report."""
    output = [
        f"📊 Architecture Map for: {file_name}",
        "=" * 50,
        ""
    ]

    if imports or from_imports:
        output.append("📦 DEPENDENCIES (What this file requires):")
        for imp in sorted(imports):
            output.append(f"  - import {imp}")
        for f_imp in sorted(from_imports):
            output.append(f"  - {f_imp}")
        output.append("")

    if classes:
        output.append("🏗️ EXPORTS: CLASSES (What this file provides):")
        for cls in sorted(classes):
            output.append(f"  - class {cls}")
        output.append("")

    if functions:
        output.append("⚡ EXPORTS: FUNCTIONS:")
        sorted_funcs = sorted(functions)
        for func in sorted_funcs[:40]:
            output.append(f"  - def {func}()")
            
        if len(sorted_funcs) > 40:
            output.append(f"  ... and {len(sorted_funcs) - 40} more top-level functions.")
        output.append("")

    if not any([imports, from_imports, classes, functions]):
        output.append("No significant dependencies or exports detected in this file.")

    return "\n".join(output)

@register_tool("map_file_dependencies")
async def map_file_dependencies(file_path: str) -> str:
    """Analyzes a Python file deterministically without blocking the async event loop."""
    path = Path(file_path)
    
    if not path.exists() or not path.is_file():
        return f"Error: File not found at {file_path}"
        
    if path.suffix.lower() != ".py":
        return f"Error: The dependency mapper currently only supports Python (.py) files. Received: {path.suffix}"

    try:
        def _process():
            code_content = path.read_text(encoding="utf-8")
            tree = ast.parse(code_content)
            
            imports, from_imports, classes, functions = _extract_ast_data(tree)
            
            return _format_architecture_report(
                file_name=path.name,
                imports=imports,
                from_imports=from_imports,
                classes=classes,
                functions=functions
            )

        return await asyncio.to_thread(_process)

    except SyntaxError as e:
        return f"Syntax Error: Could not parse {path.name}. The file may have broken code. ({str(e)})"
    except Exception as e:
        return f"System Error analyzing dependencies for {path.name}: {str(e)}"

if __name__ == "__main__":
    async def run_test():
        print("--- TEST 1: AST Tool ---")
        result = await map_file_dependencies(__file__)
        print(result)
    
    asyncio.run(run_test())