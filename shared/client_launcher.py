"""Client launcher for different AI clients (Pi, Claude, etc.)"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any

from loguru import logger


class AppLauncher:
    """Launches AI apps and manages their lifecycle."""
    
    def __init__(self, work_dir: Optional[str] = None):
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.processes: Dict[str, subprocess.Popen] = {}
        
    async def launch_app(self, app_name: str, config: Dict[str, Any]) -> subprocess.Popen:
        """Launch a specific app with configuration."""
        if app_name == "pi":
            return await self._launch_pi(config)
        elif app_name == "claude":
            return await self._launch_claude(config)
        else:
            raise ValueError(f"Unknown app: {app_name}")
    
    async def _launch_pi(self, config: Dict[str, Any]) -> subprocess.Popen:
        """Launch Pi app."""
        # Check if pi command exists
        try:
            subprocess.run(["pi", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise RuntimeError("Pi command not found. Install from https://github.com/mariozechner/pi") from e
        
        # Ensure Talky extension is linked
        self._ensure_talky_extension_linked()
        
        # Launch Pi interactively with /voice as initial command
        logger.info(f"Starting Pi interactively with /voice command: {self.work_dir}")
        process = subprocess.Popen(
            ["pi", "/voice"],
            cwd=self.work_dir,
            text=True
        )
        
        self.processes["pi"] = process
        return process
    
    async def _launch_claude(self, config: Dict[str, Any]) -> subprocess.Popen:
        """Launch Claude app."""
        # Check if claude command exists
        try:
            subprocess.run(["claude", "--version"], capture_output=True, check=True, timeout=10)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            raise RuntimeError("Claude command not found. Install from https://claude.ai/install.sh") from e
        
        # Ensure Talky skill is installed
        self._ensure_claude_skill_installed()
        
        # Ensure MCP server is running (same logic as in _run_backend_client_profile)
        from shared.client_launcher import MCPServerManager
        mcp_manager = MCPServerManager()
        
        mcp_config = {}
        if config.get("voice_profile"):
            mcp_config["voice_profile"] = config["voice_profile"]
        
        mcp_available = await mcp_manager.ensure_running(mcp_config)
        if not mcp_available:
            logger.warning("Failed to start MCP server - voice features may not work")
        
        # Ensure Claude is connected to Talky MCP server
        self._ensure_claude_mcp_connected()
        
        # Launch Claude with pre-approved tools and initial prompt
        logger.info(f"Starting Claude in: {self.work_dir}")
        
                
                
        # Start Claude with the voice conversation prompt
        # Note: Don't use --allowedTools as it prevents prompt processing
        claude_args = ["claude", "I want to have a voice conversation"]
        logger.info(f"Executing: {' '.join(claude_args)}")
        
        process = subprocess.Popen(
            claude_args,
            cwd=self.work_dir,
            text=True
        )
        
        self.processes["claude"] = process
        return process
    
    def _ensure_talky_extension_linked(self):
        """Create symlink to Talky extension if not exists."""
        pi_extensions_dir = Path.home() / ".pi" / "agent" / "extensions"
        talky_extension = pi_extensions_dir / "talky"
        
        if not talky_extension.exists():
            logger.info("Creating symlink to Talky extension...")
            pi_extensions_dir.mkdir(parents=True, exist_ok=True)
            
            # Find Talky root directory
            talky_root = Path(__file__).parent.parent
            extension_source = talky_root / "pi-extension"
            
            if not extension_source.exists():
                raise RuntimeError(f"Extension not found at: {extension_source}")
            
            talky_extension.symlink_to(extension_source, target_is_directory=True)
            logger.info(f"Extension linked: {talky_extension}")
    
    def _ensure_claude_skill_installed(self):
        """Install Talky skill for Claude if not exists."""
        claude_skills_dir = Path.home() / ".claude" / "skills"
        talky_skill_dir = claude_skills_dir / "talky"
        talky_skill_file = talky_skill_dir / "SKILL.md"
        
        if not talky_skill_file.exists():
            logger.info("Installing Talky skill for Claude...")
            claude_skills_dir.mkdir(parents=True, exist_ok=True)
            talky_skill_dir.mkdir(exist_ok=True)
            
            # Find Talky root directory and copy skill file
            talky_root = Path(__file__).parent.parent
            skill_source = talky_root / "docs" / "integrations" / "claude-skill.md"
            
            if not skill_source.exists():
                raise RuntimeError(f"Skill file not found at: {skill_source}")
            
            import shutil
            shutil.copy2(skill_source, talky_skill_file)
            logger.info(f"Talky skill installed: {talky_skill_file}")
        else:
            logger.debug("Talky skill already installed")
    
    def _ensure_claude_mcp_connected(self):
        """Ensure Claude is connected to Talky MCP server."""
        try:
            # Check if talky MCP server is in Claude's configuration
            result = subprocess.run(
                ["claude", "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if "talky" in result.stdout or "pipecat-mcp-server" in result.stdout:
                logger.debug("Claude already connected to Talky MCP server")
                return
            
            # Try to connect automatically
            logger.info("Connecting Claude to Talky MCP server...")
            subprocess.run([
                "claude", "mcp", "add", "--transport", "http",
                "talky", "http://localhost:9090/mcp"
            ], capture_output=True, timeout=30)
            logger.info("Connected Claude to Talky MCP server")
            
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to connect Claude to MCP server: {e}")
            logger.info("Manual connection required:")
            logger.info("  claude mcp add --transport http talky http://localhost:9090/mcp")
    
        
    def _ensure_vite_client_running(self):
        """Start Vite client if not already running."""
        # Check if Vite client is already running on port 5173
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('localhost', 5173))
            sock.close()
            
            if result == 0:
                logger.debug("Vite client already running on port 5173")
                return
        except Exception:
            pass
        
        # Start Vite client
        logger.info("Starting Vite client for WebRTC connection...")
        try:
            # Find Talky root directory and client directory
            talky_root = Path(__file__).parent.parent
            client_dir = talky_root / "client"
            
            if not client_dir.exists():
                raise RuntimeError(f"Client directory not found: {client_dir}")
            
            # Start Vite dev server
            vite_process = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=client_dir,
                text=True
            )
            
            self.processes["vite"] = vite_process
            logger.info("Vite client started on port 5173")
            
        except Exception as e:
            logger.error(f"Failed to start Vite client: {e}")
            logger.info("You may need to start it manually: cd client && npm run dev")
    
    async def trigger_voice_command(self, app_name: str):
        """Trigger voice command in the running app."""
        if app_name == "pi" and app_name in self.processes:
            logger.info("Pi app running with /voice command already executed.")
            return True
        elif app_name == "claude" and app_name in self.processes:
            logger.info("Claude app running with MCP server connection.")
            logger.info("Use voice tools: voice_speak, voice_listen, voice_stop")
            return True
        return False
    
    async def stop_all(self):
        """Stop all running apps."""
        for name, process in self.processes.items():
            logger.info(f"Stopping {name} app...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self.processes.clear()
        logger.info("All apps stopped")


class MCPServerManager:
    """Manages MCP server lifecycle."""
    
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        
    async def ensure_running(self, config: Dict[str, Any]) -> bool:
        """Ensure MCP server is running, start if needed. Returns True if server is available."""
        # Check if port is already in use
        try:
            result = subprocess.run(["lsof", "-ti:9090"], capture_output=True, text=True)
            if result.stdout.strip():
                logger.info("MCP server already running on port 9090")
                return True
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            logger.debug(f"Could not check port 9090: {e}")
        
        # Start MCP server in background
        logger.info("Starting Talky MCP server in background...")
        mcp_args = ["talky", "mcp"]
        
        if voice_profile := config.get("voice_profile"):
            mcp_args.extend(["--voice-profile", voice_profile])
        
        if host := config.get("host"):
            mcp_args.extend(["--host", host])
        
        # Start detached - don't wait for it
        subprocess.Popen(
            mcp_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True  # Detach from parent process
        )
        
        # Give MCP server a moment to start
        time.sleep(2)
        
        # Verify it started
        try:
            result = subprocess.run(["lsof", "-ti:9090"], capture_output=True, text=True)
            if result.stdout.strip():
                logger.info("MCP server started successfully")
                return True
            else:
                logger.error("MCP server failed to start")
                return False
        except (FileNotFoundError, subprocess.SubprocessError):
            logger.error("Could not verify MCP server startup")
            return False
    
    async def stop(self):
        """MCP server is left running as a service - no cleanup needed."""
        logger.info("MCP server left running as background service")
