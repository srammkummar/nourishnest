"""Offline grammar extension of Phase 8; no real-provider factory is reachable."""

import re

from nourish_nest.assistant_provider import (
    AssistantError,
    extract_fake_intent,
    guard_request,
    normalized,
)
from nourish_nest.multi_agent_contracts import Intent

CUISINES = ("indian", "italian", "mexican", "mediterranean", "chinese", "thai", "american")
ALLERGENS = ("peanut", "milk", "egg", "soy", "wheat", "gluten", "tree nut", "fish", "shellfish", "sesame")


def guard(message: str):
    text = normalized(message)
    try:
        guard_request((text,))
    except AssistantError:
        return "This preview cannot provide treatment, override safety, or change records."
    if re.search(r"chain.of.thought|hidden (?:prompt|reasoning)|other household|another household|"
                 r"starv\w*|purge|eating disorder|anorexi\w*|bulimi\w*|health outcome", text):
        return "This request is outside the safe household preview scope."
    calories = re.findall(r"([0-9]+)\s*(?:kcal|calories)", text)
    if calories:
        return "Calorie-target requests need a qualified professional; this preview uses saved data."
    return None


class RuleBasedMultiAgentProvider:
    async def interpret(self, message: str) -> Intent:
        text = normalized(message)
        for n, word in enumerate(("one", "two", "three", "four", "five", "six", "seven"), 1):
            text = re.sub(rf"\b{word}\b", str(n), text)
        text = re.sub(r"\b(create|make)\b", "plan", text)
        text = re.sub(r"\badults?\b", "people", text)
        text = text.replace("show what we need to buy", "show what i need to buy")
        text = text.replace("show the grocery shortages", "shortages")
        text = text.replace("show grocery shortages", "shortages")
        text = text.replace("grocery shortages", "shortages")
        text = text.replace("show shortages", "shortages")
        text = re.sub(r"\b(?:stay|keep preparation|keep cooking|keep total time|keep)\s+(?=under|within|at most)", "", text)
        allergens = []
        for allergen in ALLERGENS:
            pattern = rf"\b(?:avoid|exclude|no) {allergen}s?\b"
            if re.search(pattern, text):
                allergens.append(allergen)
                text = re.sub(pattern, "", text)
        cuisines = [c for c in CUISINES if re.search(rf"\b{c}\b", text)]
        for cuisine in cuisines:
            text = re.sub(rf"\b{cuisine}(?: cuisine)?\b", "", text)
        expiring = bool(re.search(r"\bexpiring\b", text))
        text = re.sub(r"\b(?:use|prioritize) (?:pantry )?food expiring this week\b", "", text)
        text = re.sub(r"\b(?:use|prioritize) (?:expiring (?:pantry )?(?:food|items|inventory)|food expiring soon)\b", "", text)
        missing = re.search(r"(?:at most|no more than) ([0-9]+) missing ingredients", text)
        missing_count = int(missing[1]) if missing else 100
        text = re.sub(r"(?:at most|no more than) [0-9]+ missing ingredients", "", text)
        limit = re.search(r"limit to ([0-9]+) recipes", text)
        result_limit = int(limit[1]) if limit else 50
        text = re.sub(r"limit to [0-9]+ recipes", "", text)
        strict_time = bool(re.search(r"under [0-9]+ minutes", text))
        text = " ".join(text.split())
        base = extract_fake_intent((text,))
        if not 0 <= missing_count <= 100 or not 1 <= result_limit <= 50 or base.allow_repeats:
            return Intent(reason="Unsupported limits or repeats; use unique meals and bounded limits.")
        minutes = base.maximum_cooking_minutes
        if strict_time and minutes:
            minutes -= 1
        if minutes == 0:
            return Intent(reason="Preparation time must permit at least one minute.")
        return Intent(action=base.action, number_of_meals=base.days, servings=base.servings,
                      meal=base.meal, diets=base.diets, allergens=allergens, cuisines=cuisines,
                      maximum_minutes=minutes, maximum_missing_ingredients=missing_count,
                      prioritize_expiring=expiring, groceries=base.groceries,
                      result_limit=result_limit, reason=base.reason)
