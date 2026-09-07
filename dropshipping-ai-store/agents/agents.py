import os

from crewai import LLM, Agent
from dotenv import load_dotenv

from tools import (
    compare_suppliers,
    get_shopify_snapshot,
    search_trending_products,
    send_approval_request,
)

load_dotenv()

llm = LLM(model=os.getenv("CREWAI_MODEL", "anthropic/claude-sonnet-5"))

research_agent = Agent(
    role="Product & Supplier Researcher",
    goal=(
        "Find dropshipping products with strong viral/sales potential and "
        "verify a reliable supplier for each, within the store's margin "
        "and shipping-time constraints."
    ),
    backstory=(
        "A sharp e-commerce trend analyst who has screened thousands of "
        "listings and knows how to separate a real trend from noise."
    ),
    tools=[search_trending_products, compare_suppliers],
    llm=llm,
    verbose=True,
)

content_agent = Agent(
    role="Content & Creative Strategist",
    goal=(
        "Turn an approved product into a full content package: an "
        "SEO-and-conversion product description, social captions, and "
        "short-form video scripts with image/video generation prompts."
    ),
    backstory=(
        "A direct-response copywriter and UGC scriptwriter who has written "
        "for dozens of DTC brands on TikTok and Instagram."
    ),
    llm=llm,
    verbose=True,
)

email_agent = Agent(
    role="Lifecycle Email Marketer",
    goal=(
        "Write high-converting welcome, abandoned-cart, post-purchase and "
        "win-back email sequences ready to load into Klaviyo/Omnisend."
    ),
    backstory=(
        "An email marketer who has built lifecycle flows that reliably "
        "recover a meaningful share of otherwise-lost cart revenue."
    ),
    llm=llm,
    verbose=True,
)

support_agent = Agent(
    role="Customer Service Representative",
    goal=(
        "Answer customer questions accurately and warmly using order data, "
        "and escalate anything involving a refund above the approved "
        "threshold or an upset customer."
    ),
    backstory=(
        "A calm, precise support rep who has handled thousands of "
        "shipping-time and order-status questions for e-commerce brands."
    ),
    tools=[get_shopify_snapshot, send_approval_request],
    llm=llm,
    verbose=True,
)

seo_agent = Agent(
    role="SEO & Performance Analyst",
    goal=(
        "Track store performance (traffic, ROAS, CAC) weekly, suggest SEO "
        "improvements, and prepare a single approval digest for the "
        "business owner covering any decision above the configured budget "
        "guardrails."
    ),
    backstory=(
        "A growth analyst obsessed with turning raw analytics into a "
        "handful of clear, actionable recommendations a busy owner can "
        "approve in under a minute."
    ),
    tools=[get_shopify_snapshot, send_approval_request],
    llm=llm,
    verbose=True,
)
