import sys
import shutil
import subprocess

from pathlib import Path

WADE_DIR = Path.home() / ".wade"
CHROMA_DB_DIR = WADE_DIR / "vector_store"
STATE_DB_PATH = WADE_DIR / "indexer_state.db"
PID_FILE = WADE_DIR / "gateway.pid"

def is_wade_running() -> bool:
    """Checks if the W.A.D.E. gateway is currently locking the database."""
    if PID_FILE.exists():
        return True
        
    if sys.platform == "win32":
        try:
            res = subprocess.run('netstat -ano | findstr :8000', shell=True, capture_output=True, text=True)
            if "LISTENING" in res.stdout:
                return True
        except Exception:
            pass
    return False

def reset_database():
    print("\n⚠️ WARNING: This will permanently delete W.A.D.E.'s vector memory and indexer state.")
    
    if is_wade_running():
        print("\n🛑 ERROR: W.A.D.E. is currently running!")
        print("ChromaDB locks its files while active. You must stop the gateway before wiping the brain.")
        print("Please run 'wade stop' in your CLI, then try again.")
        return

    print(f"\nTarget 1: {CHROMA_DB_DIR}")
    print(f"Target 2: {STATE_DB_PATH}")
    
    confirm = input("\nAre you sure you want to wipe the database? (y/n): ")
    
    if confirm.lower() != 'y':
        print("🛑 Reset cancelled. Your data is safe.")
        return

    print("\n🧹 Initiating deep clean...")

    if CHROMA_DB_DIR.exists():
        try:
            shutil.rmtree(CHROMA_DB_DIR)
            print("✅ Vector store (ChromaDB) completely erased.")
        except PermissionError:
            print("❌ Permission Error: A background Python process is still locking the database.")
            print("   Try closing your IDE or terminal, or check Task Manager for lingering Python tasks.")
        except Exception as e:
            print(f"❌ Failed to delete Vector store: {e}")
    else:
        print("➖ Vector store not found (already clean).")

    if STATE_DB_PATH.exists():
        try:
            STATE_DB_PATH.unlink()
            print("✅ Indexer state (SQLite) completely erased.")
        except Exception as e:
            print(f"❌ Failed to delete SQLite DB: {e}")
    else:
        print("➖ Indexer state not found (already clean).")

    print("\n✨ Wipe complete! Next time you start W.A.D.E., he will rebuild his brain using the new Modular RAG architecture.")

if __name__ == "__main__":
    reset_database()