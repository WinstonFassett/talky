#!/bin/bash

# Install Talky skill for Claude Code
# This script installs the talky skill in Claude Code's skills directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TALKY_ROOT="$(dirname "$SCRIPT_DIR")"
SKILL_NAME="talky"

echo "🔧 Installing Talky skill for Claude Code..."

# Check if Claude Code is installed
if ! command -v claude &> /dev/null; then
    echo "❌ Claude Code not found. Please install it first:"
    echo "   curl -fsSL https://claude.ai/install.sh | bash"
    exit 1
fi

# Determine Claude skills directory
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
if [ ! -d "$CLAUDE_SKILLS_DIR" ]; then
    echo "❌ Claude skills directory not found at $CLAUDE_SKILLS_DIR"
    echo "   Make sure Claude Code has been run at least once"
    exit 1
fi

# Create skill directory
SKILL_DIR="$CLAUDE_SKILLS_DIR/$SKILL_NAME"
echo "📁 Creating skill directory: $SKILL_DIR"
mkdir -p "$SKILL_DIR"

# Copy skill file
SKILL_SOURCE="$TALKY_ROOT/docs/integrations/claude-skill.md"
SKILL_DEST="$SKILL_DIR/SKILL.md"

if [ ! -f "$SKILL_SOURCE" ]; then
    echo "❌ Skill source file not found: $SKILL_SOURCE"
    exit 1
fi

echo "📋 Installing skill file..."
cp "$SKILL_SOURCE" "$SKILL_DEST"

echo "✅ Talky skill installed successfully!"
echo ""
echo "📖 Usage:"
echo "   1. Start Talky MCP server: talky mcp"
echo "   2. Connect Claude: claude mcp add --transport http talky http://localhost:9090/mcp"
echo "   3. Run Claude: claude"
echo "   4. Start voice: \"I want to have a voice conversation\""
echo ""
echo "🔧 Skill location: $SKILL_DEST"
echo "📝 Edit the skill file to customize voice behavior"
