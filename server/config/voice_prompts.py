"""Voice conversation prompts for LLM backends"""

# Standard voice conversation guidance
VOICE_PROMPT = (
    "This is a voice conversation. Respond naturally for spoken interaction. "
    "Keep responses concise and conversational. "
    "Do NOT use markdown formatting, emojis, or special characters. "
    "Use simple punctuation. Speak as if having a real conversation."
)

def format_voice_message(user_message: str) -> str:
    """Format user message with voice conversation guidance.
    
    Args:
        user_message: The original user message
        
    Returns:
        Formatted message with voice prompt prepended
    """
    return f"{VOICE_PROMPT}\n\nUser: {user_message}"
