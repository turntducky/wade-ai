from app.skills.registry import register_tool
from app.skills.python.runner import python_sandbox

@register_tool("calculate_math")
async def calculate_math(expression: str) -> str:
    """Evaluates a complex math expression using an enriched Python environment."""
    safe_expr = repr(expression)
    
    math_script = f"""
import math
import cmath
import statistics
from fractions import Fraction
from decimal import Decimal

# Isolate heavy lifters so one missing library doesn't break the others
try: import numpy as np
except ImportError: pass

try: import pandas as pd
except ImportError: pass

try: import scipy
except ImportError: pass

try:
    import sympy
    from sympy import symbols, Eq, solve, integrate, diff
    x, y, z = symbols('x y z')
except ImportError: pass

expr = {safe_expr}

try:
    # First, try to evaluate it as a direct expression (e.g., "25 * 4" or "np.mean([1,2,3])")
    result = eval(expr)
    print(result)
except SyntaxError:
    # If it's a multi-line block (e.g., defining arrays and then doing math), execute it
    try:
        exec_globals = globals().copy()
        exec(expr, exec_globals)
    except Exception as e:
        print(f"Execution Error: {{e}}")
except Exception as e:
    print(f"Math Error: {{e}}")
"""
    return await python_sandbox.execute(math_script)

import asyncio

if __name__ == "__main__":
    async def run_test():
        print("--- TEST 1: Basic Math (eval) ---")
        res1 = await calculate_math("2500 * (1.05 ** 10)")
        print(res1)
        
        print("\n--- TEST 2: Algebra with Sympy (eval) ---")
        res2 = await calculate_math("solve(Eq(x**2 - 16, 0), x)")
        print(res2)
        
        print("\n--- TEST 3: Multi-line Script (exec) ---")
        res3 = await calculate_math("arr = [10, 20, 30, 40]\navg = sum(arr)/len(arr)\nprint(f'Average is {avg}')")
        print(res3)

    asyncio.run(run_test())