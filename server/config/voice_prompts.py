"""Voice conversation prompts for LLM backends"""

# Standard voice conversation guidance
VOICE_PROMPT = (
    "[VOICE MODE] No markdown, emojis, or special characters. Natural conversation only."
)

def format_voice_message(user_message: str) -> str:
    """Format user message with voice conversation guidance.
    
    Args:
        user_message: The original user message
        
    Returns:
        Formatted message with voice prompt
    """
    return f"{user_message}"
