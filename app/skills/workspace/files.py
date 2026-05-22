import os
import asyncio
import itertools

from pathlib import Path
from app.skills.registry import register_tool

WORKSPACE_DIR = Path.home() / ".wade" / "workspace"
MAX_TOOL_OUTPUT_LENGTH = 4000

def _get_safe_path(filename: str) -> Path:
    """Ensures the AI cannot perform directory traversal attacks within the workspace."""
    safe_name = os.path.basename(filename)
    return WORKSPACE_DIR / safe_name

@register_tool("update_workspace_file")
async def update_workspace_file(filename: str, content: str) -> str:
    """Updates or creates a file in the Wade workspace asynchronously with safety checks."""
    try:
        def _write():
            WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
            filepath = _get_safe_path(filename)
            clean_content = content.replace('\\n', '\n')
            stripped_content = clean_content.strip()
            
            if stripped_content.startswith("```"):
                lines = stripped_content.split('\n')
                if len(lines) >= 2 and lines[-1].startswith("```"):
                    clean_content = '\n'.join(lines[1:-1])
            
            if filepath.exists():
                old_content = filepath.read_text(encoding="utf-8")
                
                if "... [" in clean_content or "] ..." in clean_content:
                    return f"ERROR: Write rejected. Do not use placeholders like '...'. You MUST provide the full, complete document text."
                
                if len(old_content) > 100:
                    if len(clean_content) < (len(old_content) * 0.7):
                        return f"ERROR: Write rejected. The new content is significantly shorter than the original file. If you are trying to add a rule, you must rewrite the ENTIRE file including all original content."
            
            filepath.write_text(clean_content, encoding="utf-8")
            return f"Success: Content written safely to workspace at {filepath.name}. Absolute paths are not permitted in this tool."
            
        return await asyncio.to_thread(_write)
    except Exception as e:
        return f"Error updating {filename}: {str(e)}"

@register_tool("append_workspace_file")
async def append_workspace_file(filename: str, new_text: str) -> str:
    """Appends text to a file asynchronously."""
    try:
        def _append():
            WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
            filepath = _get_safe_path(filename)
            
            if not filepath.exists():
                return f"Error: {filename} does not exist. Use update_workspace_file to create it first."
                
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(f"\n{new_text}\n")
                
            return f"Success: Text was safely appended to the bottom of {filename}."
            
        return await asyncio.to_thread(_append)
    except Exception as e:
        return f"Error appending to {filename}: {str(e)}"

@register_tool("delete_workspace_file")
async def delete_workspace_file(filename: str) -> str:
    """Deletes a file from the Wade workspace asynchronously."""
    try:
        def _delete():
            filepath = _get_safe_path(filename)
            if filepath.exists():
                filepath.unlink()
                return f"Success: {filename} has been deleted."
            return f"Error: {filename} not found in workspace."
            
        return await asyncio.to_thread(_delete)
    except Exception as e:
        return f"Error deleting {filename}: {str(e)}"

@register_tool("scan_directory")
async def scan_directory(directory_path: str, max_depth: int = 2) -> str:
    """Returns a formatted tree of a directory, ignoring heavy/irrelevant folders."""
    target_dir = Path(directory_path)
    
    if target_dir.is_file():
        target_dir = target_dir.parent

    if not target_dir.exists() or not target_dir.is_dir():
        return f"Error: The directory '{target_dir}' does not exist or is inaccessible."

    IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', 'venv', 'env', '.wade', '.idea', 'dist', 'build'}

    def _generate_tree(current_path: Path, current_depth: int) -> str:
        if current_depth > max_depth:
            return "  " * current_depth + "└── [... max depth reached ...]\n"
            
        tree_str = ""
        try:
            items = sorted(current_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            
            for item in items:
                if item.name.startswith('.') and item.name != '.env':
                    continue
                if item.is_dir() and item.name in IGNORE_DIRS:
                    continue
                    
                indent = "  " * current_depth
                icon = "📁" if item.is_dir() else "📄"
                tree_str += f"{indent}└── {icon} {item.name}\n"
                
                if item.is_dir():
                    tree_str += _generate_tree(item, current_depth + 1)
                    
        except PermissionError:
            tree_str += "  " * current_depth + "└── [Permission Denied]\n"
        except Exception as e:
            tree_str += "  " * current_depth + f"└── [Error: {str(e)}]\n"
            
        return tree_str

    header = f"🗂️ Project Map for: {target_dir.absolute()}\n"
    header += "=" * 40 + "\n"
    
    tree_output = _generate_tree(target_dir, 0)
    
    if not tree_output.strip():
        return header + "Directory is empty."
        
    return header + tree_output

@register_tool("search_in_files")
async def search_in_files(directory_path: str, query: str, extension: str | None = None) -> str:
    """Searches for a pattern in files using a fast recursive scan."""
    try:
        def _search():
            import re
            target_dir = Path(directory_path)
            if not target_dir.exists() or not target_dir.is_dir():
                return f"Error: '{directory_path}' is not a valid directory."
            
            results = []
            pattern = re.compile(query, re.IGNORECASE)
            
            for file_path in target_dir.rglob("*"):
                if not file_path.is_file(): continue
                if extension and file_path.suffix != extension: continue
                if any(part.startswith('.') or part == '__pycache__' for part in file_path.parts): continue
                
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f):
                            if pattern.search(line):
                                results.append(f"{file_path.absolute()}:{i+1} | {line.strip()}")
                                if len(results) > 50:
                                    results.append("... [Too many results, truncated] ...")
                                    break
                except Exception:
                    continue
                if len(results) > 50: break
            
            if not results:
                return f"No matches found for '{query}' in {directory_path}."
            
            return f"Found {len(results)} matches for '{query}':\n" + "\n".join(results)

        return await asyncio.to_thread(_search)
    except Exception as e:
        return f"ERROR searching files: {str(e)}"

@register_tool("read_host_file")
async def read_host_file(absolute_path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Reads a file from anywhere on the host machine with memory-safe pagination."""
    try:
        def _read():
            path = Path(absolute_path)
            if not path.exists():
                return f"ERROR: File not found at {absolute_path}"
            if not path.is_file():
                return f"ERROR: Path is a directory, not a file."

            start = max(1, start_line)
            
            if end_line is not None and start > end_line:
                return f"ERROR: start_line ({start}) cannot be greater than end_line ({end_line})."

            output_lines = []
            current_length = 0
            actual_end_line = start - 1
            
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                
                sliced_file = itertools.islice(f, start - 1, end_line)
                
                for i, line in enumerate(sliced_file):
                    line_num = start + i
                    formatted_line = f"{line_num:4d} | {line}"
                    
                    if current_length + len(formatted_line) > MAX_TOOL_OUTPUT_LENGTH:
                        truncation_msg = (
                            f"\n...[SYSTEM: OUTPUT TRUNCATED AT LINE {line_num-1} DUE TO LENGTH LIMIT. "
                            f"READ NEXT LINES USING start_line={line_num}]..."
                        )
                        output_lines.append(truncation_msg)
                        break
                    
                    output_lines.append(formatted_line)
                    current_length += len(formatted_line)
                    actual_end_line = line_num

            if not output_lines:
                return f"--- Reading {absolute_path} ---\nNo content found at line {start} or file is empty."

            file_size_kb = path.stat().st_size / 1024
            header = f"--- Reading {absolute_path} ({file_size_kb:.1f} KB) (Lines {start} to {actual_end_line}) ---\n"
            
            return header + "".join(output_lines)

        return await asyncio.to_thread(_read)
    except Exception as e:
        return f"ERROR reading file: {str(e)}"

@register_tool("write_host_file")
async def write_host_file(absolute_path: str, content: str) -> str:
    """Writes a file anywhere on the host machine."""
    try:
        def _write():
            p = Path(absolute_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Success: File written to {p}"
        return await asyncio.to_thread(_write)
    except Exception as e:
        return f"ERROR writing file: {str(e)}"

@register_tool("delete_host_file")
async def delete_host_file(absolute_path: str) -> str:
    """Deletes a file anywhere on the host machine."""
    try:
        def _delete():
            p = Path(absolute_path)
            if not p.exists():
                return f"ERROR: File not found at {absolute_path}"
            if not p.is_file():
                return f"ERROR: Path is a directory, not a file."
            p.unlink()
            return f"Success: File deleted at {absolute_path}"
        return await asyncio.to_thread(_delete)
    except Exception as e:
        return f"ERROR deleting file: {str(e)}"

@register_tool("patch_host_file")
async def patch_host_file(absolute_path: str, start_line: int, end_line: int, content: str) -> str:
    """Surgically replaces lines in a file."""
    try:
        def _patch():
            p = Path(absolute_path)
            if not p.exists() or not p.is_file():
                return f"ERROR: File not found at {absolute_path}"

            with open(p, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            total_lines = len(lines)
            start = max(1, start_line)
            end = min(total_lines, end_line)

            if start > end:
                return f"ERROR: start_line ({start}) cannot be greater than end_line ({end})."

            new_lines = [line + '\n' for line in content.splitlines()]

            lines[start-1:end] = new_lines

            with open(p, 'w', encoding='utf-8') as f:
                f.writelines(lines)

            new_end = start - 1 + len(new_lines)
            return f"Success: Replaced lines {start} to {end}. The new content now occupies lines {start} to {new_end} in {absolute_path}."

        return await asyncio.to_thread(_patch)
    except Exception as e:
        return f"ERROR patching file: {str(e)}"
    
if __name__ == "__main__":
    async def run_test():
        print("--- TEST 1: Create/Update Workspace File ---")
        res1 = await update_workspace_file("TEST_WADE.md", "# W.A.D.E. Test\nLine 2\nLine 3 is wrong.\nLine 4")
        print(res1)
        
        print("\n--- TEST 2: Append to Workspace File ---")
        res2 = await append_workspace_file("TEST_WADE.md", "Line 5: Appended successfully.")
        print(res2)
        
        test_file_path = str(WORKSPACE_DIR / "TEST_WADE.md")
        
        print("\n--- TEST 3: Global Read (Lines 2-4) ---")
        res3 = await read_host_file(test_file_path, start_line=2, end_line=4)
        print(res3)
        
        print("\n--- TEST 4: Patching the File ---")
        res4 = await patch_host_file(start_line=3, end_line=3, content="Line 3 is now fixed!", absolute_path=test_file_path)
        print(res4)
        
        print("\n--- TEST 5: Verify Patch ---")
        res5 = await read_host_file(test_file_path)
        print(res5)
        
        print("\n--- TEST 6: Global Delete ---")
        res6 = await delete_host_file(absolute_path=test_file_path)
        print(res6)

    asyncio.run(run_test())