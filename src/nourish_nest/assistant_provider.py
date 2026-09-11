"""Replaceable async interpretation boundary. No model receives database records or tools."""

import re
import unicodedata
from decimal import Decimal
from typing import Protocol

from nourish_nest.assistant_contracts import AssistantRequest, PlanningIntent
from nourish_nest.config import get_settings


class AssistantError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ChatProvider(Protocol):
    async def interpret(self, messages: tuple[str, ...]) -> str: ...


class ChatRateLimited(Exception):
    pass


class ChatUnavailable(Exception):
    pass


class ChatMalformedResponse(Exception):
    pass


def user_messages(data: AssistantRequest) -> tuple[str, ...]:
    return tuple(m.content for m in data.conversation_context if m.role == "user") + (
        data.user_message,
    )


def normalized(text: str) -> str:
    return " ".join(
        "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                if unicodedata.category(c) != "Cf").split()
    )


def guard_request(messages: tuple[str, ...]) -> None:
    text = normalized(" ".join(messages))
    # Conservative MVP boundary, not a general prompt-injection or medical classifier.
    text = text.replace("do not ignore allergens", "respect allergens")
    patterns = (
        r"\b(ignore|bypass|override|forget)\b.*\b(allerg|previous|instruction|system|safety)",
        r"\b(system prompt|developer message|execute|sql|tool_call|tool call)\b",
        r"\b(diagnos\w*|treat\w*|cure\w*|medication|insulin|guarantee\w*|medical advice)\b",
        r"\b(delete|mutate|reserve|consume|save|update|purchase)\b",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        raise AssistantError(
            "unsafe_assistant_request",
            "I can preview meals, but cannot bypass allergy safeguards, provide medical "
            "treatment or guarantees, execute instructions, or change stored data.",
        )


def expected_tools(intent: PlanningIntent) -> list[str]:
    if intent.action != "plan":
        return []
    return ["recommendations", "nutrition_summary"] + (
        ["member_nutrition"] if intent.compare_target else []
    ) + (["grocery_shortage"] if intent.groceries else [])


def extract_fake_intent(messages: tuple[str, ...]) -> PlanningIntent:
    """Small declared grammar for local demonstrations, not a real language model."""
    text = normalized(" ".join(messages))
    for number, word in enumerate(("one", "two", "three", "four", "five", "six", "seven"), 1):
        text = re.sub(rf"\b{word}\b", str(number), text)
    intent = PlanningIntent()
    meals = re.findall(r"\b(breakfast|lunch|dinner|snack)(?:es|s)?\b", text)
    if len(set(meals)) == 1:
        intent.meal = meals[0]
    day_pattern = (r"\b([1-7]) (?:(?:vegetarian|vegan|pescatarian|halal) )*"
                   r"(?:days?|breakfasts?|lunch(?:es)?|dinners?|snacks?)\b")
    days = re.findall(day_pattern, text)
    intent.days = int(days[-1]) if days else (7 if re.search(r"\b(week|weekly)\b", text) else None)
    servings = re.findall(r"\b(?:for|servings?) ([0-9]+(?:\.[0-9]+)?) (?:people|persons?|servings?)\b", text)
    if not servings:
        servings = re.findall(r"\b([0-9]+(?:\.[0-9]+)?) servings?\b", text)
    if servings:
        intent.servings = Decimal(servings[-1])
    intent.diets = [d for d in ("vegetarian", "vegan", "pescatarian", "halal", "no_beef", "no_pork")
                    if re.search(rf"\b{d.replace('_', ' ')}\b", text)]
    calories = re.findall(r"\b(under|at most) ([0-9]+(?:\.[0-9]+)?) calories\b", text)
    if calories:
        intent.strict_calorie_limit = calories[-1][0] == "under"
        intent.maximum_calories = Decimal(calories[-1][1])
    minutes = re.findall(r"\b(?:within|under|at most) ([0-9]+) minutes\b", text)
    if minutes:
        intent.maximum_cooking_minutes = int(minutes[-1])
    intent.groceries = bool(re.search(r"\b(buy|shopping|groceries|shortages)\b", text))
    intent.compare_target = "compare" in text and "target" in text
    intent.allow_repeats = "allow repeats" in text
    # Anything outside the declared grammar requires clarification, never a guessed constraint.
    remainder = text
    patterns = [
        r"(?:under|at most) [0-9]+(?:\.[0-9]+)? calories(?: per serving)?",
        r"(?:within|under|at most) [0-9]+ minutes(?: cooking time)?",
        r"(?:for|servings?) [0-9]+(?:\.[0-9]+)? (?:people|persons?|servings?)",
        r"[0-9]+(?:\.[0-9]+)? servings?", day_pattern,
        r"prioritize expiring pantry items", r"show what i need to buy", r"allow repeats",
        r"compare (?:with )?(?:my |member |nutrition )?target", r"respect allergens",
        r"\b(plan|please|a|the|this|week|weekly|and|with|for|me|dinners?|lunch(?:es)?|breakfasts?|snacks?|vegetarian|vegan|pescatarian|halal|no beef|no pork|shopping|groceries|shortages)\b",
        r"[.,!?;:]",
    ]
    for pattern in patterns:
        remainder = re.sub(pattern, " ", remainder)
    out_of_range = False
    for field, upper in (("servings", 100), ("maximum_calories", 10000),
                         ("maximum_cooking_minutes", 1440)):
        value = getattr(intent, field)
        if value is not None and not 0 < value <= upper:
            setattr(intent, field, None)
            out_of_range = True
    if remainder.strip() or out_of_range:
        intent.reason = "unsupported_request"
    elif intent.days is None or intent.meal is None or intent.servings is None:
        intent.reason = "missing_inputs"
    else:
        intent.action = "plan"
    intent.tools = expected_tools(intent)
    return PlanningIntent.model_validate(intent.model_dump())


class FakeChatProvider:
    async def interpret(self, messages: tuple[str, ...]) -> str:
        return extract_fake_intent(messages).model_dump_json()


class DisabledChatProvider:
    async def interpret(self, messages: tuple[str, ...]) -> str:
        raise ChatUnavailable()


def get_chat_provider() -> ChatProvider:
    settings = get_settings()
    if settings.ai_provider == "ollama":
        from nourish_nest.ollama_provider import OllamaChatProvider

        return OllamaChatProvider(settings)
    return FakeChatProvider() if settings.ai_provider == "fake" else DisabledChatProvider()
