#!/bin/bash
set -e

echo "🚀 W.A.D.E. Gateway Entrypoint"

mkdir -p /root/.wade/workspace /root/.wade/data /root/.wade/memory

if [ -n "$OLLAMA_HOST" ]; then
    echo "🔍 Checking Ollama connectivity at $OLLAMA_HOST..."
    until curl -s "$OLLAMA_HOST/api/tags" > /dev/null; do
        echo "⏳ Waiting for Ollama to be ready..."
        sleep 2
    done
    echo "✅ Ollama is online."
fi

if [ ! -f "/root/.wade/config.yaml" ] && [ "$WADE_CI" != "1" ]; then
    echo "⚠️ config.yaml not found. Running headless setup..."
fi

echo "🎬 Starting W.A.D.E. Gateway..."
exec "$@"
