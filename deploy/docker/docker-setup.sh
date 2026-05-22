#!/bin/bash

set -e

echo "🤖 Initializing W.A.D.E Environment..."

echo "📁 Setting up host directories..."
mkdir -p ~/.wade/workspace
mkdir -p ~/.wade/memory
mkdir -p ~/.wade/wa_session

echo "🚀 Bringing up the ecosystem..."
docker compose up -d --build

echo "✅ W.A.D.E is live! Gateway is running on port 8000."
