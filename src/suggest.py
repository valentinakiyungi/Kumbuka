from pydantic import BaseModel

from src import llm


class Suggestions(BaseModel):
    suggestions: list[str]


def next_prompts(user_message: str, reply: str, n: int = 3) -> list[str]:
    """Guess what the user is likely to say next, based on this exchange."""
    prompt = (
        f"Suggest {n} short follow-up messages that the USER might send next, based on "
        "their last message and the assistant's reply below. Write each in the first person, "
        "from the user's point of view, under 12 words, specific to this conversation. "
        "Make them different from each other (for example a deeper question, a next step, "
        "and a related topic). Do not number them.\n\n"
        f"User's last message: {user_message[:1000]}\n\n"
        f"Assistant's reply: {reply[:2500]}"
    )
    raw = llm.generate(prompt, schema=Suggestions, temperature=0.8, retries=1, task="suggest")
    items = [s.strip().strip('"') for s in Suggestions.model_validate_json(raw).suggestions]
    return [s for s in items if 3 <= len(s) <= 120][:n]
