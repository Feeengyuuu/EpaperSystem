# randomizer.py
import logging

logger = logging.getLogger(__name__)


def _build_messages(from_prompt: str | None):
    system_content = (
        "You are a creative assistant generating extremely random and unique image prompts. "
        "Avoid common themes. Focus on unexpected, unconventional, and bizarre combinations "
        "of art style, medium, subjects, time periods, and moods. No repetition. Prompts "
        "should be 20 words or less and specify random artist, movie, tv show or time period "
        "for the theme. Do not provide any headers or repeat the request, just provide the "
        "updated prompt in your response."
    )
    user_content = (
        "Give me a completely random image prompt, something unexpected and creative! "
        "Let's see what your AI mind can cook up!"
    )

    if from_prompt and str(from_prompt).strip():
        system_content = (
            "You are a creative assistant specializing in generating highly descriptive "
            "and unique prompts for creating images. When given a short or simple image "
            "description, your job is to rewrite it into a more detailed, imaginative, "
            "and descriptive version that captures the essence of the original while "
            "making it unique and vivid. Avoid adding irrelevant details but feel free "
            "to include creative and visual enhancements. Avoid common themes. Focus on "
            "unexpected, unconventional, and bizarre combinations of art style, medium, "
            "subjects, time periods, and moods. Do not provide any headers or repeat the "
            "request, just provide your updated prompt in the response. Prompts "
            "should be 20 words or less and specify random artist, movie, tv show or time "
            "period for the theme."
        )
        user_content = (
            f"Original prompt: \"{from_prompt}\"\n"
            "Rewrite it to make it more detailed, imaginative, and unique while staying "
            "true to the original idea. Include vivid imagery and descriptive details. "
            "Avoid changing the subject of the prompt."
        )

    return system_content, user_content

# Randomize text
def randomizer(
    llm_client,
    from_prompt: str | None = None,
    *,
    llm_provider: str,
    chat_model: str,
    temperature: float = 1,
) -> str:
    """
    LLM provider routing only:
      - llm_provider="openai" or "groq": OpenAI-compatible client: .chat.completions.create(...)
      - llm_provider="gemini": google-genai client: .models.generate_content(...)

    NOTE: AI Horde uses llm_provider="groq" in your pipeline.
    """
    llm_provider = (llm_provider or "").strip().lower()
    if llm_provider not in ("openai", "groq", "gemini"):
        raise ValueError("llm_provider must be: openai, groq, or gemini")

    system_content, user_content = _build_messages(from_prompt)
    logger.info("Randomizing prompt... llm_provider=%s model=%s", llm_provider, chat_model)

    if llm_provider in ("openai", "groq"):
        resp = llm_client.chat.completions.create(
            model=chat_model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
        )
        out = (resp.choices[0].message.content or "").strip()
    else:
        resp = llm_client.models.generate_content(
            model=chat_model,
            contents=f"SYSTEM:\n{system_content}\n\nUSER:\n{user_content}\n",
            config={"temperature": float(temperature)},
        )
        out = (resp.text or "").strip()

    if not out:
        raise RuntimeError("Randomizer returned empty prompt.")

    logger.info("Randomized prompt: %s", out)
    return out
