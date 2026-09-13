"""Presentation tokens and local assets. No application data access or remote assets."""

from base64 import b64encode
from functools import lru_cache
from html import escape

import streamlit as st

from nourish_nest.ui_assets import ASSETS, IMAGES, page_hero_image
from nourish_nest.ui_labels import friendly_message

NAV_GROUPS = {
    "Home": ("Dashboard",),
    "Plan": ("Meal Planner", "Recipes", "AI Assistant"),
    "Shop": ("Grocery Lists",),
    "Manage": ("Pantry",),
    "Profile": ("Household", "Nutrition"),
}
NAV_PAGES = tuple(page for pages in NAV_GROUPS.values() for page in pages)
HEADERS = {
    "Dashboard": ("YOUR EVERYDAY, WELL NOURISHED", "vegetables", "Plan nourishing meals, use what you have, and shop with confidence."),
    "Household": ("A PLACE FOR EVERYONE", "kitchen", "The people, preferences and routines that make this home yours."),
    "Nutrition": ("NOURISH YOUR DAY", "nutrition", "Understand your needs. Make room for balance."),
    "Recipes": ("GOOD FOOD, WORTH SHARING", "pasta", "Your collection of everyday favorites and new possibilities."),
    "Pantry": ("MAKE THE MOST OF WHAT YOU HAVE", "pantry", "A little order today. Less waste and easier meals tomorrow."),
    "Grocery Lists": ("SHOP WITH CONFIDENCE", "grocery", "From meal ideas to a basket of just what you need."),
    "Meal Planner": ("A WEEK THAT WORKS FOR YOU", "meal-prep", "Bring your next seven days to the table."),
    "AI Assistant": ("A LITTLE HELP WITH WHAT’S NEXT", "assistant", "Tell NourishNest what sounds good. Explore a plan together."),
}


def safe(value):
    return escape(friendly_message(str(value)), quote=True)


@lru_cache(maxsize=16)
def local_text(relative):
    return (ASSETS / relative).read_text(encoding="utf-8")


def image_html(key, *, compact=False):
    relative, alt = IMAGES.get(key, IMAGES["recipe"])
    path = ASSETS / relative
    if not path.is_file():
        return '<div class="nn-image nn-fallback" role="img" aria-label="Serving inspiration unavailable">A little inspiration, coming soon</div>'
    if path.suffix == ".svg":
        encoded = b64encode(local_text(relative).encode()).decode()
        content = f'<img src="data:image/svg+xml;base64,{encoded}" alt="{escape(alt, quote=True)}" width="640" height="360">'
    else:
        base = st.get_option("server.baseUrlPath").strip("/")
        url = f"/{base + '/' if base else ''}app/static/assets/{relative}"
        content = f'<img src="{url}" alt="{escape(alt, quote=True)}" loading="lazy" width="960" height="600">'
    return f'<div class="nn-image {"nn-compact" if compact else ""}">{content}</div>'


def illustration(key, *, compact=True):
    st.html(image_html(key, compact=compact))


def apply_design():
    st.html(f"<style>{local_text('theme.css')}</style>")


def brand():
    encoded = b64encode(local_text("brand/wordmark.svg").encode()).decode()
    st.html(f'<img src="data:image/svg+xml;base64,{encoded}" alt="NourishNest — a leaf growing from a bowl" width="242" height="50" style="max-width:100%;height:auto">')
    st.caption("Good food. A little more together.")


def page_header(page, household=None):
    kicker, _, subtitle = HEADERS[page]
    asset = page_hero_image(page)
    title = f"Welcome home, {household.name}." if page == "Dashboard" and household else page
    st.html(
        f'<section class="nn-header {"nn-hero" if page == "Dashboard" else ""}" aria-label="{safe(page)}">'
        f'<div class="nn-header-copy"><p class="nn-kicker">{kicker}</p><h1>{safe(title)}</h1><p>{subtitle}</p></div>'
        f'{image_html(asset)}</section>'
    )


def badge(text, tone="neutral"):
    tone = tone if tone in {"neutral", "success", "warning", "error"} else "neutral"
    st.html(f'<span class="nn-badge nn-{tone}">{safe(text)}</span>')


def avatar(name):
    initials = "".join(word[0] for word in name.split()[:2]) or "?"
    st.html(f'<span class="nn-avatar" role="img" aria-label="Profile for {safe(name)}">{safe(initials)}</span>')


def steps(labels):
    st.html('<ol class="nn-steps" aria-label="Workflow">' + "".join(
        f'<li><span>{index}</span>{safe(label)}</li>' for index, label in enumerate(labels, 1)
    ) + "</ol>")


def week_cards(meals, days):
    cards = []
    for day in days:
        entries = [(slot, meal) for (name, slot), meal in meals.items() if name == day]
        content = "".join(
            f'<p class="nn-kicker">{safe(slot)}</p><h3>{safe(meal["recipe"].recipe_name)}</h3>'
            f'<p>{safe(meal["servings"])} servings</p>'
            f'<p>{safe(meal["recipe"].nutrition_per_serving.calories if meal["recipe"].nutrition_per_serving.calories is not None else "Unavailable")} kcal / serving</p>'
            f'<p>{safe(meal["recipe"].missing_ingredient_count)} ingredients need shopping</p>'
            for slot, meal in entries
        ) or '<p class="nn-muted">A little space for something good.</p>'
        cards.append(f'<article class="nn-day"><h2>{safe(day)}</h2>{content}</article>')
    st.html('<div class="nn-week">' + "".join(cards) + '</div>')
