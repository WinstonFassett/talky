"""Base Backend Interface for Voice Bot LLM Services

Provides a unified interface that any LLM backend can implement.
Handles both simple request/response (Moltis) and complex async event models (OpenClaw).
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, Optional

from loguru import logger
from pipecat.frames.frames import Frame
from pipecat.services.llm_service import LLMService


class BaseLLMBackend(ABC):
    """Base interface for all LLM backends."""

    def __init__(self, **kwargs):
        """Initialize backend with configuration."""
        self.kwargs = kwargs
        self._service: Optional[LLMService] = None
        self._initialized = False

    @abstractmethod
    async def initialize(self) -> LLMService:
        """Initialize the backend and return a Pipecat LLMService.

        Returns:
            LLMService: Configured Pipecat LLM service
        """
        pass

    @abstractmethod
    async def cleanup(self):
        """Cleanup backend resources."""
        pass

    @abstractmethod
    def get_backend_info(self) -> Dict[str, Any]:
        """Get backend information for debugging/logging."""
        pass

    @property
    def service(self) -> LLMService:
        """Get the Pipecat LLM service (must be initialized first)."""
        if not self._service:
            raise RuntimeError(f"Backend {self.__class__.__name__} not initialized")
        return self._service

    @property
    def is_initialized(self) -> bool:
        """Check if backend is initialized."""
        return self._initialized


class SimpleBackendMixin:
    """Mixin for simple request/response backends (like Moltis)."""

    async def send_message(self, message: str, **kwargs) -> str:
        """Send a message and get response (simple sync interface)."""
        if not hasattr(self.service, "send_message"):
            raise NotImplementedError(
                f"Backend {self.__class__.__name__} doesn't support sync messages"
            )
        return await self.service.send_message(message, **kwargs)


class DualPipelineMixin:
    """Mixin for complex async backends (like OpenClaw) that need dual pipelines."""

    def __init__(self, **kwargs):
        # Don't call super() here - this is a mixin
        self._input_service = None
        self._output_service = None

    @abstractmethod
    async def get_input_service(self) -> LLMService:
        """Get the input pipeline service (STT → Backend)."""
        pass

    @abstractmethod
    async def get_output_service(self) -> LLMService:
        """Get the output pipeline service (Backend → TTS)."""
        pass

    @property
    def input_service(self) -> LLMService:
        """Get input service (must be initialized first)."""
        if not self._input_service:
            raise RuntimeError(f"Backend {self.__class__.__name__} input service not initialized")
        return self._input_service

    @property
    def output_service(self) -> LLMService:
        """Get output service (must be initialized first)."""
        if not self._output_service:
            raise RuntimeError(f"Backend {self.__class__.__name__} output service not initialized")
        return self._output_service


class BackendManager:
    """Manages multiple backends and provides unified access."""

    def __init__(self):
        self._backends: Dict[str, BaseLLMBackend] = {}
        self._current_backend: Optional[BaseLLMBackend] = None

    def register_backend(self, name: str, backend: BaseLLMBackend):
        """Register a backend instance."""
        self._backends[name] = backend
        logger.info(f"Registered backend: {name}")

    async def switch_backend(self, name: str) -> BaseLLMBackend:
        """Switch to a different backend."""
        if name not in self._backends:
            available = list(self._backends.keys())
            raise ValueError(f"Unknown backend: {name}. Available: {available}")

        backend = self._backends[name]

        # Initialize if not already done
        if not backend.is_initialized:
            await backend.initialize()

        self._current_backend = backend
        logger.info(f"Switched to backend: {name}")
        return backend

    def get_current_backend(self) -> Optional[BaseLLMBackend]:
        """Get the currently active backend."""
        return self._current_backend

    def list_backends(self) -> list[str]:
        """List all registered backends."""
        return list(self._backends.keys())

    async def cleanup_all(self):
        """Cleanup all registered backends."""
        for backend in self._backends.values():
            if backend.is_initialized:
                await backend.cleanup()
        logger.info("All backends cleaned up")


# Global backend manager instance
backend_manager = BackendManager()
