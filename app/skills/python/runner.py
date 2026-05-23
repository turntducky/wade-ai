import ast
import json
import uuid
import atexit
import asyncio
import subprocess

from pathlib import Path

from app.skills.registry import register_tool
from app.core.utils import safe_truncate, run_command_async

WORKSPACE_DIR = Path.home() / ".wade" / "workspace"
MAX_TOOL_OUTPUT_LENGTH = 1500
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

class PythonSandbox:
    """Persistent, UUID-delimited background process with namespace isolation."""
    def __init__(self):
        self._lock = asyncio.Lock()
        self.process = None
        self._start_process()

    def _start_process(self):
        self.shutdown() 
        
        wrapper_code = """
import sys, io, traceback, json

# Define the dictionary OUTSIDE the loop so variables persist across executions!
safe_globals = {"__builtins__": __builtins__}

while True:
    try:
        header = sys.stdin.readline()
        if not header: 
            break # Exit cleanly if stdin is closed
            
        header = header.strip()
        if not header: continue
        
        meta = json.loads(header)
        delimiter = meta['delimiter']
        
        code = []
        while True:
            line = sys.stdin.readline()
            if not line or line.strip() == delimiter + '_END': break
            code.append(line)
        
        old_stdout, old_stderr = sys.stdout, sys.stderr
        redirected_output = sys.stdout = io.StringIO()
        sys.stderr = redirected_output
        
        try:
            exec(''.join(code), safe_globals)
        except Exception:
            traceback.print_exc()
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
            print(redirected_output.getvalue())
            print(delimiter + '_RESULT')
            sys.stdout.flush()
    except Exception:
        pass
"""
        import os
        safe_env = {
            k: os.environ[k]
            for k in (
                "PATH", "HOME", "PYTHONPATH",
                "USERPROFILE", "APPDATA", "TEMP", "TMP",
                "SYSTEMROOT", "SystemRoot",
            )
            if k in os.environ
        }

        self.process = subprocess.Popen(
            ["python", "-c", wrapper_code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(WORKSPACE_DIR),
            bufsize=1,
            env=safe_env
        )

    def shutdown(self):
        """Safely terminates the background Python process to prevent memory leaks."""
        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            except Exception:
                pass
            finally:
                self.process = None

    async def execute(self, code: str, timeout: int = 15) -> str:
        if not self.process or self.process.poll() is not None:
            self._start_process()
            
        async with self._lock:
            try:
                if not self.process or not self.process.stdin:
                    return "Error: Failed to initialize sandbox process."
                
                marker = str(uuid.uuid4())
                meta = json.dumps({"delimiter": marker})
                
                self.process.stdin.write(f"{meta}\n{code}\n{marker}_END\n")
                self.process.stdin.flush()
                
                async def _read_output():
                    if not self.process or not self.process.stdout:
                        raise RuntimeError("Process stdout not available")
                    output = []
                    while True:
                        line = await asyncio.to_thread(self.process.stdout.readline)
                        if line.strip() == f"{marker}_RESULT":
                            break
                        output.append(line)
                    return "".join(output).strip()
                
                _fut = asyncio.ensure_future(_read_output())
                done, _ = await asyncio.wait([_fut], timeout=timeout)
                if not done:
                    _fut.cancel()
                    try:
                        await _fut
                    except (asyncio.CancelledError, Exception):
                        pass
                    raise asyncio.TimeoutError()
                exc = _fut.exception()
                if exc:
                    raise exc
                result = _fut.result()
                raw_res = result if result else "Executed successfully with no output."
                return f"<python_sandbox_stdout>\n{safe_truncate(raw_res, MAX_TOOL_OUTPUT_LENGTH)}\n</python_sandbox_stdout>"
                
            except asyncio.TimeoutError:
                print("⚠️ IPC Sandbox Timeout! Killing rogue worker process...")
                self._start_process()
                return f"Error: Execution timed out after {timeout} seconds. Check for infinite loops."
            except Exception as e:
                self._start_process() 
                return f"IPC Sandbox Error: {str(e)}"

python_sandbox = PythonSandbox()
atexit.register(python_sandbox.shutdown)

def _quick_syntax_check(code: str) -> str | None:
    """Instantly detects syntax errors, infinite loops, and dangerous system commands."""
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                return "ERROR: Execution rejected. 'while' loops risk hanging the system. Please achieve your goal using a bounded 'for' loop (e.g., for i in range(10000):)."
            
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ["exec", "eval"]:
                return "ERROR: Execution rejected. Dynamic execution ('exec', 'eval') is strictly blocked."
                
            if isinstance(node, ast.Attribute):
                dangerous_attrs = {"rmtree", "system", "popen", "remove", "unlink", "rmdir", "removedirs"}
                if node.attr in dangerous_attrs:
                    return f"ERROR: Execution rejected. Dangerous system call '{node.attr}' is blocked by sandbox security policy."
                    
    except SyntaxError as e:
        return f"ERROR: Syntax Error in your Python code: {e}. Please fix it and try again."
        
    return None

@register_tool("run_python")
async def run_python(script_code: str) -> str:
    """Wrapper function for the agent loop to call."""
    
    security_error = _quick_syntax_check(script_code)
    if security_error:
        return security_error
    
    if "def " in script_code and not "\nprint(" in script_code:
        func_name = script_code.split("def ")[1].split("(")[0]
        script_code += f"\n\n{func_name}()"
        
    return await python_sandbox.execute(script_code)

@register_tool("run_shell_command")
async def run_shell_command(command: str) -> str:
    """Executes a shell command directly on the host machine."""
    out, err, code = await run_command_async(command, shell=True, timeout=10)
    if code == 0:
        raw_res = out if out else "Command executed successfully with no output."
        return f"<shell_stdout>\n{safe_truncate(raw_res, MAX_TOOL_OUTPUT_LENGTH)}\n</shell_stdout>"
    else:
        raw_err = err if err else "Unknown error."
        return f"<shell_stderr>\n{safe_truncate(raw_err, MAX_TOOL_OUTPUT_LENGTH)}\n</shell_stderr>"
    
if __name__ == "__main__":
    async def run_test():
        print("--- TEST 1: Shell Command ---")
        shell_res = await run_shell_command("echo Hello from Windows Shell!")
        print(shell_res)
        
        print("\n--- TEST 2: Python Sandbox (Initialization) ---")
        py_res1 = await run_python("wade_target = 'Huntsville'\nprint(f'Target set to {wade_target}')")
        print(py_res1)
        
        print("\n--- TEST 3: Python Sandbox (Statefulness Check) ---")
        py_res2 = await run_python("print(f'I remember the target is: {wade_target}')")
        print(py_res2)

        print("\n--- TEST 4: Security Check ('while' loop block) ---")
        py_res3 = await run_python("while True:\n    pass")
        print(py_res3)

    asyncio.run(run_test())