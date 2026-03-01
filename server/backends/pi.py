"""Pi backend for Talky - starts MCP server and Pi client"""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from loguru import logger


class PiLLMService:
    """Pi LLM service that runs Pi as a subprocess with Talky voice extension."""
    
    def __init__(self, config: dict, work_dir: Optional[str] = None):
        self.config = config
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.pi_process: Optional[subprocess.Popen] = None
        self.mcp_process: Optional[subprocess.Popen] = None
        
    async def start(self):
        """Start Pi with Talky voice extension."""
        # Check if pi command exists
        try:
            subprocess.run(["pi", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise RuntimeError("Pi command not found. Install from https://github.com/mariozechner/pi") from e
        
        # Start MCP server in background
        logger.info("Starting Talky MCP server...")
        mcp_args = ["talky", "mcp"]
        
        # Add voice profile if specified in config
        if voice_profile := self.config.get("voice_profile"):
            mcp_args.extend(["--voice-profile", voice_profile])
        
        # Add host if specified
        if host := self.config.get("host"):
            mcp_args.extend(["--host", host])
        
        self.mcp_process = subprocess.Popen(
            mcp_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Give MCP server a moment to start
        time.sleep(2)
        
        # Check if MCP server started successfully
        if self.mcp_process.poll() is not None:
            stdout, stderr = self.mcp_process.communicate()
            raise RuntimeError(f"MCP server failed to start: {stdout or stderr}")
        
        logger.info("MCP server started")
        
        # Ensure Talky extension is linked
        self._ensure_extension_linked()
        
        # Start Pi in the specified directory
        logger.info(f"Starting Pi in: {self.work_dir}")
        self.pi_process = subprocess.Popen(
            ["pi"],
            cwd=self.work_dir,
            text=True
        )
        
    def _ensure_extension_linked(self):
        """Create symlink to Talky extension if not exists."""
        pi_extensions_dir = Path.home() / ".pi" / "agent" / "extensions"
        talky_extension = pi_extensions_dir / "talky"
        
        if not talky_extension.exists():
            logger.info("Creating symlink to Talky extension...")
            pi_extensions_dir.mkdir(parents=True, exist_ok=True)
            
            # Get the talky repo root
            talky_root = Path(__file__).parent.parent.parent
            extension_source = talky_root / "pi-extension"
            
            if not extension_source.exists():
                raise RuntimeError(f"Extension not found at: {extension_source}")
            
            talky_extension.symlink_to(extension_source, target_is_directory=True)
            logger.info(f"Extension linked: {talky_extension}")
    
    async def stop(self):
        """Stop Pi and MCP server."""
        logger.info("Stopping Pi and MCP server...")
        
        if self.pi_process:
            self.pi_process.terminate()
            try:
                self.pi_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.pi_process.kill()
            self.pi_process = None
        
        if self.mcp_process:
            self.mcp_process.terminate()
            try:
                self.mcp_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.mcp_process.kill()
            self.mcp_process = None
        
        logger.info("Stopped")
    
    async def send_message(self, message: str) -> str:
        """Send a message to Pi (not implemented - Pi handles its own UI)."""
        raise NotImplementedError("Pi backend doesn't support direct message injection")


def create_pi_service(config: dict, work_dir: Optional[str] = None) -> PiLLMService:
    """Create Pi LLM service instance."""
    return PiLLMService(config, work_dir)
