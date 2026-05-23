import ast

from pathlib import Path
from typing import Generator

_MAX_EMBED_TOKENS = 400
_CHARS_PER_TOKEN = 3.5
_MAX_EMBED_CHARS = int(_MAX_EMBED_TOKENS * _CHARS_PER_TOKEN)

def _split_at_lines(text: str, max_chars: int, header: str) -> Generator[str, None, None]:
    lines = text.splitlines(keepends=True)
    piece = header
    for line in lines:
        if len(piece) + len(line) > max_chars and len(piece) > len(header):
            yield piece.rstrip()
            piece = header + line
        else:
            piece += line
    tail = piece.rstrip()
    if tail and tail != header.rstrip():
        yield tail

class LogicalCodeChunker:
    def __init__(self, max_fallback_chars: int = 2000):
        self.max_fallback_chars = max_fallback_chars

    def is_supported(self, file_path: Path) -> bool:
        """Currently supports Python. Easy to expand to JS/TS using tree-sitter later."""
        supported = {'.py', '.js', '.ts', '.cpp', '.c', '.mq4', '.mq5', '.cs'}
        return file_path.suffix.lower() in supported

    def stream_code_chunks(self, code_text: str, file_path: str) -> Generator[str, None, None]:
        """Yields semantically complete code blocks."""
        if not self.is_supported(Path(file_path)):
            yield from self._basic_fallback_chunk(code_text, file_path)
            return

        try:
            tree = ast.parse(code_text)
            context_buffer = f"File Context: {file_path}\n"

            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if len(context_buffer) > 50 and context_buffer.strip() != f"File Context: {file_path}":
                        yield from self._safe_yield(context_buffer, file_path)
                        context_buffer = f"File Context: {file_path}\n"

                    node_source = ast.get_source_segment(code_text, node)
                    if node_source:
                        chunk = f"File: {file_path} | Object: {node.name}\n\n{node_source}"
                        yield from self._safe_yield(chunk, file_path)
                else:
                    node_source = ast.get_source_segment(code_text, node)
                    if node_source:
                        context_buffer += node_source + "\n"

            if len(context_buffer) > 50 and context_buffer.strip() != f"File Context: {file_path}":
                yield from self._safe_yield(context_buffer, file_path)

        except SyntaxError:
            yield from self._basic_fallback_chunk(code_text, file_path)

    def _safe_yield(self, chunk: str, file_path: str) -> Generator[str, None, None]:
        if len(chunk) <= _MAX_EMBED_CHARS:
            yield chunk
        else:
            header = f"File: {file_path}\n"
            yield from _split_at_lines(chunk, _MAX_EMBED_CHARS, header)

    def _basic_fallback_chunk(self, text: str, file_path: str) -> Generator[str, None, None]:
        header = f"File: {file_path}\n"
        blocks = text.split("\n\n")
        current_chunk = header

        for block in blocks:
            if len(current_chunk) + len(block) > self.max_fallback_chars:
                if len(current_chunk) > len(header):
                    yield from self._safe_yield(current_chunk, file_path)
                current_chunk = header + block + "\n\n"
            else:
                current_chunk += block + "\n\n"

        if len(current_chunk) > len(header):
            yield from self._safe_yield(current_chunk, file_path)